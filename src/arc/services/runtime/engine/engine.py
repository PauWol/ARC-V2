"""LlamaEngine: owns the loaded model(s) and exposes generate()/embed().

Loading happens once, in Service.run() (see main.py).

Chat generation (`generate_chat`) does NOT use llama-cpp-python's built-in
`create_chat_completion()` / `chat_format="chatml-function-calling"` path
anymore. That handler hard-codes a restriction where `tool_choice="auto"`
(the default whenever tools are present) combined with `stream=True` raises
`ValueError("Automatic streaming tool choice is not supported")` — an
unresolved upstream limitation, not a config mistake
(https://github.com/abetlen/llama-cpp-python/discussions/1615).

Instead, `generate_chat` renders the prompt itself using
`templating.ChatTemplateRenderer` (which prefers the chat template embedded
in the GGUF's own metadata — the same template Qwen3/Hermes-family models
were trained against for tool calling) and drives the *raw* completion API
(`Llama.__call__`), exactly like `generate_completion` already does for
`/completions`. `agentic_parsing.AgenticStreamParser` turns that raw token
stream into content/reasoning text plus tool-call fragments by watching for
`<think>...</think>` and `<tool_call>...</tool_call>` tags as they arrive —
the same incremental-tag-detection technique `parsing.StreamParser` already
used for `<think>` alone. This gives real token-level streaming *and* real
"auto" tool choice at the same time, with no grammar-constrained-decoding
overhead unless you opt into it (see the note at the bottom of
agentic_parsing.py).

Embeddings use a second, lazily-loaded llama.cpp context over the *same*
GGUF file (llama.cpp can't serve generation and embedding from one context
simultaneously).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass

from llama_cpp import (
    LLAMA_POOLING_TYPE_CLS,
    LLAMA_POOLING_TYPE_LAST,
    LLAMA_POOLING_TYPE_MEAN,
    LLAMA_POOLING_TYPE_UNSPECIFIED,
    Llama,
)

from arc.services.runtime.engine.agentic_parsing import (
    AgenticStreamParser,
    ToolCallFragment,
)
from arc.services.runtime.engine.config import EngineConfig
from arc.services.runtime.engine.parsing import ParsedEvent, StreamParser
from arc.services.runtime.engine.queue import RequestQueue
from arc.services.runtime.engine.sampling import build_sampling_kwargs
from arc.services.runtime.engine.templating import ChatTemplateRenderer
from arc.services.runtime.shared.types import ChatRequest, CompletionRequest

logger = logging.getLogger(__name__)

_POOLING_TYPES = {
    "unspecified": LLAMA_POOLING_TYPE_UNSPECIFIED,
    "mean": LLAMA_POOLING_TYPE_MEAN,
    "cls": LLAMA_POOLING_TYPE_CLS,
    "last": LLAMA_POOLING_TYPE_LAST,
}


@dataclass
class GenerationChunk:
    event: ParsedEvent | None = None
    tool_call: ToolCallFragment | None = None
    finish_reason: str | None = None


class LlamaEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self._model: Llama | None = None
        self._embedding_model: Llama | None = None
        self._embedding_lock = asyncio.Lock()
        self._queue = RequestQueue()
        self._renderer: ChatTemplateRenderer | None = None
        self.model_name: str = ""

    # -- lifecycle ---------------------------------------------------------

    async def load(self) -> None:
        """Blocking model load, run off the event loop."""
        await asyncio.to_thread(self._load_sync)
        self._queue.start()

    def _load_sync(self) -> None:
        logger.info(
            "Loading model: path=%s n_ctx=%d n_gpu_layers=%d n_threads=%s n_batch=%d",
            self.config.model_path,
            self.config.n_ctx,
            self.config.n_gpu_layers,
            self.config.n_threads,
            self.config.n_batch,
        )
        self._model = self._construct_llama(self.config.n_gpu_layers)
        self.model_name = str(self.config.model_path).rsplit("/", 1)[-1]

        chat_template = None
        metadata = getattr(self._model, "metadata", None) or {}
        chat_template = metadata.get("tokenizer.chat_template")
        self._renderer = ChatTemplateRenderer(chat_template)
        if self._renderer.using_fallback:
            logger.warning(
                "No tokenizer.chat_template found in this GGUF's metadata; "
                "falling back to the built-in ChatML template. Tool-calling "
                "reliability depends on how closely the model was trained "
                "on that exact <tool_call> tag convention — check "
                "templating.py's _FALLBACK_TEMPLATE if results look off."
            )

    def _construct_llama(self, n_gpu_layers: int) -> Llama:
        """Construct the Llama() context, with a one-time fallback to CPU-only.

        Note: no `chat_format` is passed here anymore. Chat generation goes
        through the raw completion API driven by our own prompt rendering
        and tag parsing (see module docstring), so llama-cpp-python's
        built-in chat-format machinery is unused and its grammar overhead
        is avoided entirely.

        See original docstring: a too-aggressive n_gpu_layers can abort the
        whole process natively rather than raising. We can't recover from
        that after the fact, but any *catchable* Python exception here
        (missing file, bad n_ctx, OOM that does raise) is logged with a full
        traceback and retried once at n_gpu_layers=0.
        """
        try:
            return Llama(
                model_path=str(self.config.model_path),
                n_ctx=self.config.n_ctx,
                n_threads=self.config.n_threads,
                n_batch=self.config.n_batch,
                n_gpu_layers=n_gpu_layers,
                embedding=False,
                verbose=self.config.verbose,
            )
        except Exception:
            logger.exception(
                "Llama() construction raised with n_gpu_layers=%d", n_gpu_layers
            )
            if n_gpu_layers != 0:
                logger.warning(
                    "Retrying model load on CPU only (n_gpu_layers=0) after GPU load failure"
                )
                return self._construct_llama(0)
            raise

    async def unload(self) -> None:
        await self._queue.stop()
        self._model = None
        self._embedding_model = None

    # -- chat -----------------------------------------------------------------

    async def generate_chat(self, req: ChatRequest) -> AsyncIterator[GenerationChunk]:
        assert self._model is not None
        assert self._renderer is not None
        sampling_kwargs = build_sampling_kwargs(req)
        # NOTE: unlike the old create_chat_completion()-based implementation,
        # `stream=True` must stay in sampling_kwargs here — the raw
        # Llama.__call__ API below only returns a generator of chunks when
        # `stream=True` is actually passed. Popping it (as the old code did,
        # since create_chat_completion handled `stream` separately) makes
        # this default to stream=False, so `self._model(...)` returns one
        # dict instead of an iterator, and the `for out in ...` loop below
        # would then iterate over that dict's *keys* instead of chunks.

        tool_choice = req.tool_choice or ("auto" if req.tools else None)
        include_tools = bool(req.tools) and tool_choice != "none"
        # "required" is implemented via an assistant-prefix trick: we
        # append the tool_call opening tag to the *prompt* so the model has
        # no choice but to continue directly into a tool call's JSON body.
        # This doesn't pick a *specific* tool (that would need either a
        # stronger prompt hint or a per-tool grammar swap — see
        # agentic_parsing.py's closing note) but it does guarantee *some*
        # tool is called, deterministically, with full streaming support.
        force_tool_call = include_tools and tool_choice == "required"

        prompt = self._renderer.render(
            messages=req.messages,
            tools=req.tools if include_tools else None,
            add_generation_prompt=True,
        )
        if force_tool_call:
            prompt += self.config.tool_call_open

        tool_schemas = None
        if include_tools and self.config.tool_call_dialect == "xml_function_parameter":
            # Only needed by the xml dialect: its parameter values arrive
            # as untyped raw text, so we need each tool's JSON Schema to
            # cast them (int/float/bool/JSON) before packing them into the
            # arguments object -- see agentic_parsing._coerce_param_value.
            tool_schemas = {
                t.function.name: (t.function.parameters or {}).get("properties", {})
                for t in req.tools
            }

        parser = AgenticStreamParser(
            think_open=self.config.think_open,
            think_close=self.config.think_close,
            tool_call_open=self.config.tool_call_open,
            tool_call_close=self.config.tool_call_close,
            dialect=self.config.tool_call_dialect,
            tool_schemas=tool_schemas,
            force_tool_call=force_tool_call,
        )

        # De-dup while preserving order: config defaults first, then any
        # request-supplied stop strings.
        stop = list(dict.fromkeys([*self.config.stop_sequences, *req.stop]))

        def _run(cancel_event: threading.Event):
            for out in self._model(prompt, stop=stop, **sampling_kwargs):
                if cancel_event.is_set():
                    return
                choice = out["choices"][0]
                text = choice.get("text", "")
                finish_reason = choice.get("finish_reason")

                for event in parser.feed(text):
                    if isinstance(event, ToolCallFragment):
                        yield GenerationChunk(tool_call=event)
                    else:
                        yield GenerationChunk(event=event)

                if finish_reason:
                    for event in parser.finalize():
                        if isinstance(event, ToolCallFragment):
                            yield GenerationChunk(tool_call=event)
                        else:
                            yield GenerationChunk(event=event)
                    if parser.incomplete_tool_call_mode is not None:
                        # A <tool_call> tag opened but never resolved into a
                        # complete call before generation stopped. Common
                        # causes: max_output_tokens too low for the JSON to
                        # finish, a stop sequence matching too eagerly, or
                        # the model's actual tool-call convention not
                        # matching config.tool_call_open/close. Any leftover
                        # raw text was already emitted as a content chunk
                        # above by parser.finalize() -- this just makes the
                        # cause visible server-side too.
                        logger.warning(
                            "generate_chat: tool call did not complete before "
                            "finish_reason=%r (stuck in mode=%r). Check "
                            "max_output_tokens, the `stop` list (%r), and "
                            "whether this model's tool-call tag convention "
                            "matches tool_call_open/close in EngineConfig.",
                            finish_reason,
                            parser.incomplete_tool_call_mode,
                            stop,
                        )
                    resolved_finish = (
                        "tool_calls" if parser.saw_tool_call else finish_reason
                    )
                    yield GenerationChunk(
                        event=ParsedEvent(kind="content", text=""),
                        finish_reason=resolved_finish,
                    )

        async for chunk in self._queue.submit(
            _run, timeout_s=self.config.generation_timeout_s
        ):
            yield chunk

    # -- raw completion (no chat template, no tools) --------------------------

    async def generate_completion(
        self, req: CompletionRequest
    ) -> AsyncIterator[GenerationChunk]:
        assert self._model is not None
        sampling_kwargs = build_sampling_kwargs(req)
        parser = StreamParser(
            think_open=self.config.think_open, think_close=self.config.think_close
        )

        def _run(cancel_event: threading.Event):
            for out in self._model(req.prompt, **sampling_kwargs):
                if cancel_event.is_set():
                    return
                text = out["choices"][0]["text"]
                finish_reason = out["choices"][0].get("finish_reason")
                for event in parser.feed(text):
                    yield GenerationChunk(event=event)
                if finish_reason:
                    for event in parser.finalize():
                        yield GenerationChunk(event=event)
                    yield GenerationChunk(
                        event=ParsedEvent(kind="content", text=""),
                        finish_reason=finish_reason,
                    )

        async for chunk in self._queue.submit(
            _run, timeout_s=self.config.generation_timeout_s
        ):
            yield chunk

    # -- embeddings ---------------------------------------------------------

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        model = await self._ensure_embedding_model()

        def _run(cancel_event: threading.Event):
            result = model.create_embedding(inputs)
            yield [item["embedding"] for item in result["data"]]

        async for vectors in self._queue.submit(
            _run, timeout_s=self.config.generation_timeout_s
        ):
            return vectors
        return []

    async def _ensure_embedding_model(self) -> Llama:
        async with self._embedding_lock:
            if self._embedding_model is None:
                requested_pooling = _POOLING_TYPES.get(
                    self.config.embedding_pooling, LLAMA_POOLING_TYPE_UNSPECIFIED
                )
                self._embedding_model = await asyncio.to_thread(
                    self._construct_embedding_llama, requested_pooling
                )
            return self._embedding_model

    def _construct_embedding_llama(self, pooling_type: int) -> Llama:
        try:
            return Llama(
                model_path=str(self.config.model_path),
                n_ctx=self.config.n_ctx,
                n_threads=self.config.n_threads,
                n_gpu_layers=self.config.n_gpu_layers,
                embedding=True,
                pooling_type=pooling_type,
                verbose=self.config.verbose,
            )
        except ValueError:
            if pooling_type == LLAMA_POOLING_TYPE_UNSPECIFIED:
                raise
            logger.warning(
                "Model rejected forced pooling_type=%s; retrying with the model's own default pooling.",
                pooling_type,
            )
            return self._construct_embedding_llama(LLAMA_POOLING_TYPE_UNSPECIFIED)
