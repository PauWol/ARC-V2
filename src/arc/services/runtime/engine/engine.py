"""LlamaEngine: owns the loaded model(s) and exposes generate()/embed().

Loading happens once, in Service.run() (see main.py). Generation and
completion both flow through the same serial RequestQueue; embeddings use a
second, lazily-loaded llama.cpp context over the *same* GGUF file (llama.cpp
can't serve generation and embedding from one context simultaneously).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator

from llama_cpp import (
    LLAMA_POOLING_TYPE_CLS,
    LLAMA_POOLING_TYPE_LAST,
    LLAMA_POOLING_TYPE_MEAN,
    LLAMA_POOLING_TYPE_UNSPECIFIED,
    Llama,
)

from arc.services.runtime.engine.config import EngineConfig
from arc.services.runtime.engine.parsing import ParsedEvent, StreamParser
from arc.services.runtime.engine.queue import RequestQueue
from arc.services.runtime.engine.sampling import build_sampling_kwargs
from arc.services.runtime.engine.templating import ChatTemplateRenderer
from arc.services.runtime.types import ChatMessage, ChatRequest, CompletionRequest, Tool

logger = logging.getLogger(__name__)

_POOLING_TYPES = {
    "unspecified": LLAMA_POOLING_TYPE_UNSPECIFIED,  # let llama.cpp use the model's own default
    "mean": LLAMA_POOLING_TYPE_MEAN,
    "cls": LLAMA_POOLING_TYPE_CLS,
    "last": LLAMA_POOLING_TYPE_LAST,
}


@dataclass
class GenerationChunk:
    event: ParsedEvent
    finish_reason: str | None = None


class LlamaEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self._model: Llama | None = None
        self._embedding_model: Llama | None = None
        self._embedding_lock = asyncio.Lock()
        self._renderer: ChatTemplateRenderer | None = None
        self._queue = RequestQueue()
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
        chat_template = None
        try:
            chat_template = self._model.metadata.get("tokenizer.chat_template")
        except Exception:
            pass
        self._renderer = ChatTemplateRenderer(chat_template)
        self.model_name = str(self.config.model_path).rsplit("/", 1)[-1]

    def _construct_llama(self, n_gpu_layers: int) -> Llama:
        """Construct the Llama() context, with a one-time fallback to CPU-only.

        A too-aggressive n_gpu_layers value (from auto-detection, or a bad
        manual override) can make llama.cpp's CUDA/Metal backend abort the
        *entire process* natively (SIGSEGV/SIGABRT) rather than raising a
        catchable Python exception — that shows up as the service silently
        "crashing" with no traceback. We can't recover from that abort after
        the fact, but we CAN avoid triggering it in the first place: if GPU
        offload is requested, first probe VRAM headroom is already handled
        in config.py's auto-detect. This wraps the actual construction call
        so that any Python-level exception (missing file, bad n_ctx, OOM
        that *does* raise) is at least logged with a full traceback instead
        of vanishing, and retries once at n_gpu_layers=0 if a GPU load was
        requested and failed.
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

    # -- chat / completion ---------------------------------------------------

    async def generate_chat(self, req: ChatRequest) -> AsyncIterator[GenerationChunk]:
        assert self._model is not None and self._renderer is not None
        prompt = self._renderer.render(
            req.messages, req.tools, add_generation_prompt=True
        )
        async for chunk in self._generate(prompt, req):
            yield chunk

    async def generate_completion(
        self, req: CompletionRequest
    ) -> AsyncIterator[GenerationChunk]:
        async for chunk in self._generate(req.prompt, req):
            yield chunk

    async def _generate(
        self, prompt: str, req: ChatRequest | CompletionRequest
    ) -> AsyncIterator[GenerationChunk]:
        assert self._model is not None
        sampling_kwargs = build_sampling_kwargs(req)
        parser = StreamParser(
            think_open=self.config.think_open,
            think_close=self.config.think_close,
            tool_open=self.config.tool_call_open,
            tool_close=self.config.tool_call_close,
        )

        def _run(cancel_event: threading.Event):
            for out in self._model(prompt, **sampling_kwargs):
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
                "Model rejected forced pooling_type=%s; this model likely "
                "wasn't built with an embedding head that supports it. "
                "Retrying with the model's own default pooling instead.",
                pooling_type,
            )
            return self._construct_embedding_llama(LLAMA_POOLING_TYPE_UNSPECIFIED)
