"""Streaming parser for chat generation that does NOT depend on
llama-cpp-python's built-in `chatml-function-calling` chat handler.

Why this exists
----------------
llama-cpp-python's `chatml_function_calling` handler hard-codes a
restriction: `tool_choice="auto"` (the default whenever tools are given)
combined with `stream=True` raises `ValueError("Automatic streaming tool
choice is not supported")`. The handler needs to commit to a grammar
(free text vs. JSON-schema-constrained) before it starts streaming, and it
can't do that while it's still ambiguous whether the model will produce
plain text or a tool call. That's an unresolved upstream limitation, not
something you can configure around (see
https://github.com/abetlen/llama-cpp-python/discussions/1615).

This module sidesteps the high-level handler completely. `engine.py` now
renders the prompt itself (via `templating.ChatTemplateRenderer`, using the
GGUF's own embedded chat template) and drives the *raw* completion API
(`Llama.__call__`), exactly the way `generate_completion` already does for
`/completions`. This parser turns that raw output stream into the same
three channels the old code produced from llama-cpp-python's structured
deltas: reasoning text, content text, and tool-call fragments -- so
`engine.py`/`api.py`/`types.py` downstream don't need to change shape.

Tag conventions (two dialects, pick via `dialect=`)
----------------------------------------------------
Reasoning is the same in both dialects:  <think> ... </think>

`dialect="json"` (Hermes/most ChatML-family models):
    <tool_call>{"name": "...", "arguments": {...}}</tool_call>
  Assumes `"name"` appears before `"arguments"` in the JSON, and that
  `"arguments"` is always a JSON *object* (starts with `{`).

`dialect="xml_function_parameter"` (Qwen3-Coder / Qwen3.5+ native format --
what this codebase's GGUFs have been observed to actually emit; their
embedded `tokenizer.chat_template` instructs the model to use this, not
JSON):
    <tool_call>
    <function=get_weather>
    <parameter=location>
    Berlin, Germany
    </parameter>
    <parameter=units>
    celsius
    </parameter>
    </function>
    </tool_call>
  Parameter values are raw, untyped text (can span multiple lines) framed
  by exactly one leading/trailing newline inserted by the template -- that
  single newline is stripped, not the whole value, so intentional internal
  newlines in multi-line values survive. Because values arrive untyped,
  this dialect needs each tool's JSON Schema (`tool_schemas`) to know
  whether to cast a value to int/float/bool/JSON before packing it into
  the `arguments` object; unknown/`string`-typed params are passed through
  as-is.

Multiple `<tool_call>...</tool_call>` blocks in the output stream are
treated as parallel tool calls in both dialects (matches OpenAI's
`index`-keyed wire format already used throughout this codebase). The
outer `<tool_call>`/`<think>` tag detection and the `tool_tail` (closing
tag) handling are shared code paths -- only what happens *inside* a tool
call differs between dialects.

No JSON grammar is enforced here -- this trades the hard guarantee of
"always syntactically valid JSON" for real streaming + real auto tool
choice. If a fine-tune goes off the rails and emits malformed output
inside `<tool_call>`, the consumer (your agent loop) will get an arguments
string that fails to `json.loads`; treat that the same way you'd treat any
other malformed tool call and re-prompt. If you want the grammar guarantee
back, see the note at the bottom of this file about grammar-switching.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Union

from arc.services.runtime.engine.parsing import ParsedEvent

_Mode = Literal[
    "content",
    "reasoning",
    "tool_header",
    "tool_args_seek",
    "tool_args_stream",
    "tool_tail",
    "xml_function_header",
    "xml_param_seek",
    "xml_param_value",
]

Dialect = Literal["json", "xml_function_parameter"]

_NAME_RE = re.compile(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"')
_ARGS_KEY_RE = re.compile(r'"arguments"\s*:\s*')
_FUNCTION_OPEN_RE = re.compile(r"<function=([^>]+)>")
_PARAM_OPEN_RE = re.compile(r"<parameter=([^>]+)>")
_FUNCTION_CLOSE = "</function>"
_PARAM_CLOSE = "</parameter>"


@dataclass
class ToolCallFragment:
    """A partial tool call as it streams out of the parser. `id`/`name`
    populate once (as soon as the `"name"` field is fully parsed);
    `arguments_fragment` arrives in raw-text pieces as the model writes the
    arguments object and must be concatenated by `index` by the consumer,
    exactly like the fragments llama-cpp-python's own streaming deltas
    produced -- this dataclass intentionally has the same shape as the one
    it replaces so nothing downstream (engine.py's GenerationChunk,
    api.py's accumulator, types.py's ToolCallDelta) needs to change.
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_fragment: str = ""


def _new_call_id() -> str:
    return f"call-{uuid.uuid4().hex}"


def _json_unescape(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except json.JSONDecodeError:
        return s


def _trim_param_value(raw: str) -> str:
    """Strip exactly one leading and one trailing newline -- the formatting
    the xml_function_parameter template inserts around every value -- while
    preserving any newlines *within* an intentionally multi-line value."""
    if raw.startswith("\n"):
        raw = raw[1:]
    if raw.endswith("\n"):
        raw = raw[:-1]
    return raw


def _coerce_param_value(raw: str, schema_type: str | None) -> Any:
    """Cast an xml_function_parameter value's raw text against its JSON
    Schema type. Falls back to the raw string on any parse failure --
    never raises, since a bad cast shouldn't take down the whole stream."""
    if schema_type == "boolean":
        low = raw.strip().lower()
        if low in ("true", "false"):
            return low == "true"
        return raw
    if schema_type == "integer":
        try:
            return int(raw.strip())
        except ValueError:
            return raw
    if schema_type == "number":
        try:
            return float(raw.strip())
        except ValueError:
            return raw
    if schema_type in ("array", "object"):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return raw  # "string", unknown, or no schema available


class _JsonObjectScanner:
    """Tracks brace depth across incremental text chunks, respecting string
    literals and backslash escapes, to find where a JSON *object* value
    ends without needing the full JSON buffered in memory. Assumes the
    value starts with '{' (see module docstring)."""

    __slots__ = ("depth", "in_string", "escape")

    def __init__(self) -> None:
        self.depth = 0
        self.in_string = False
        self.escape = False

    def consume(self, text: str) -> int:
        """Returns the index (exclusive) in `text` where the object value
        closes, or -1 if it's still open after consuming all of `text`."""
        for i, ch in enumerate(text):
            if self.escape:
                self.escape = False
                continue
            if self.in_string:
                if ch == "\\":
                    self.escape = True
                elif ch == '"':
                    self.in_string = False
                continue
            if ch == '"':
                self.in_string = True
            elif ch == "{":
                self.depth += 1
            elif ch == "}":
                self.depth -= 1
                if self.depth == 0:
                    return i + 1
        return -1


ParserEvent = Union[ParsedEvent, ToolCallFragment]


class AgenticStreamParser:
    def __init__(
        self,
        think_open: str = "<think>",
        think_close: str = "</think>",
        tool_call_open: str = "<tool_call>",
        tool_call_close: str = "</tool_call>",
        dialect: Dialect = "json",
        tool_schemas: dict[str, dict[str, Any]] | None = None,
        force_tool_call: bool = False,
    ) -> None:
        """
        `dialect` picks which inner tool-call body format to expect --
        see the module docstring for both. `tool_schemas` is only used by
        `dialect="xml_function_parameter"`, mapping function name ->
        JSON-Schema `properties` dict (i.e. `tool.function.parameters.get(
        "properties", {})`), so raw XML parameter text can be cast to the
        right type before being packed into the `arguments` JSON.

        If `force_tool_call` is True, the parser assumes the caller has
        already appended `tool_call_open` to the *prompt* itself (the
        "assistant-prefix" trick for a forced/required tool call -- see
        engine.py) so the model's own output starts directly with the call
        body, not the opening tag. In that case the parser starts directly
        in the dialect's header state instead of waiting to see the tag in
        the stream.
        """
        self._think_open = think_open
        self._think_close = think_close
        self._tool_open = tool_call_open
        self._tool_close = tool_call_close
        self._max_tag_len = max(
            len(think_open), len(think_close), len(tool_call_open), len(tool_call_close)
        )
        self._dialect: Dialect = dialect
        self._tool_schemas = tool_schemas or {}

        self._buf = ""
        self._tool_index = -1
        self._scanner: _JsonObjectScanner | None = None
        self._completed_indices: set[int] = set()

        # xml_function_parameter-only state
        self._current_function_name: str | None = None
        self._current_param_key: str | None = None
        self._pending_args: dict[str, Any] = {}

        # Set by finalize() if generation ended while a tool call was still
        # mid-parse. Check this after finalize() to log/diagnose truncated
        # or malformed tool calls instead of them silently vanishing.
        self.incomplete_tool_call_mode: str | None = None

        header_mode: _Mode = (
            "tool_header" if dialect == "json" else "xml_function_header"
        )
        if force_tool_call:
            self._mode: _Mode = header_mode
            self._tool_index = 0
        else:
            self._mode = "content"

    # -- public API ----------------------------------------------------------

    def feed(self, chunk: str) -> list[ParserEvent]:
        self._buf += chunk
        out: list[ParserEvent] = []
        while self._step(out):
            pass
        return out

    def finalize(self) -> list[ParserEvent]:
        """Flush whatever remains in the buffer at end-of-stream (e.g. the
        model hit max_tokens or a stop sequence mid-tool-call)."""
        events: list[ParserEvent] = []
        if self._mode in ("content", "reasoning") and self._buf:
            events.append(ParsedEvent(kind=self._mode, text=self._buf))
        elif self._mode == "tool_args_stream" and self._buf:
            events.append(
                ToolCallFragment(index=self._tool_index, arguments_fragment=self._buf)
            )
        elif self._mode in (
            "tool_header",
            "tool_args_seek",
            "tool_tail",
            "xml_function_header",
            "xml_param_seek",
            "xml_param_value",
        ):
            # Generation ended before this tool call became parseable at
            # all (cut off by `stop`/EOS or max_tokens before the header
            # completed, before a parameter closed, or before the closing
            # tag arrived). Previously this branch didn't exist and the
            # buffered text was just dropped, which is how you can end up
            # with a response that has neither content nor tool_calls and
            # no indication anything went wrong.
            self.incomplete_tool_call_mode = self._mode
            if self._buf:
                # Surface the raw fragment as content rather than losing it
                # outright -- at minimum this makes a format mismatch or a
                # too-low max_output_tokens visible in the response instead
                # of invisible.
                events.append(ParsedEvent(kind="content", text=self._buf))
        self._buf = ""
        return events

    @property
    def saw_tool_call(self) -> bool:
        """True only once a tool call's closing tag has actually been
        parsed -- NOT merely when an opening `<tool_call>` tag was seen.
        Use this (not "was a tag opened") to decide finish_reason, or a
        truncated/malformed tool call gets misreported as a successful one."""
        return bool(self._completed_indices)

    # -- state machine ---------------------------------------------------------

    def _step(self, out: list[ParserEvent]) -> bool:
        step_fn = {
            "content": self._step_content,
            "reasoning": self._step_reasoning,
            "tool_header": self._step_tool_header,
            "tool_args_seek": self._step_tool_args_seek,
            "tool_args_stream": self._step_tool_args_stream,
            "tool_tail": self._step_tool_tail,
            "xml_function_header": self._step_xml_function_header,
            "xml_param_seek": self._step_xml_param_seek,
            "xml_param_value": self._step_xml_param_value,
        }[self._mode]
        return step_fn(out)

    def _step_content(self, out: list[ParserEvent]) -> bool:
        think_idx = self._buf.find(self._think_open)
        tool_idx = self._buf.find(self._tool_open)
        candidates = [i for i in (think_idx, tool_idx) if i != -1]
        if not candidates:
            safe_len = max(0, len(self._buf) - self._max_tag_len)
            if safe_len > 0:
                out.append(ParsedEvent(kind="content", text=self._buf[:safe_len]))
                self._buf = self._buf[safe_len:]
            return False

        idx = min(candidates)
        if idx > 0:
            out.append(ParsedEvent(kind="content", text=self._buf[:idx]))

        if idx == think_idx:
            self._buf = self._buf[idx + len(self._think_open) :]
            self._mode = "reasoning"
        else:
            self._buf = self._buf[idx + len(self._tool_open) :]
            self._tool_index += 1
            self._mode = (
                "tool_header" if self._dialect == "json" else "xml_function_header"
            )
        return True

    def _step_reasoning(self, out: list[ParserEvent]) -> bool:
        idx = self._buf.find(self._think_close)
        if idx == -1:
            safe_len = max(0, len(self._buf) - self._max_tag_len)
            if safe_len > 0:
                out.append(ParsedEvent(kind="reasoning", text=self._buf[:safe_len]))
                self._buf = self._buf[safe_len:]
            return False
        if idx > 0:
            out.append(ParsedEvent(kind="reasoning", text=self._buf[:idx]))
        self._buf = self._buf[idx + len(self._think_close) :]
        self._mode = "content"
        return True

    def _step_tool_header(self, out: list[ParserEvent]) -> bool:
        m = _NAME_RE.search(self._buf)
        if not m:
            return False  # wait for more of the "name" field to arrive
        name = _json_unescape(m.group(1))
        out.append(
            ToolCallFragment(index=self._tool_index, id=_new_call_id(), name=name)
        )
        self._buf = self._buf[m.end() :]
        self._mode = "tool_args_seek"
        return True

    def _step_tool_args_seek(self, out: list[ParserEvent]) -> bool:
        m = _ARGS_KEY_RE.search(self._buf)
        if not m:
            return False
        rest = self._buf[m.end() :]
        brace_idx = rest.find("{")
        if brace_idx == -1:
            return False  # wait for the opening brace to arrive
        self._buf = rest[brace_idx:]
        self._scanner = _JsonObjectScanner()
        self._mode = "tool_args_stream"
        return True

    def _step_tool_args_stream(self, out: list[ParserEvent]) -> bool:
        assert self._scanner is not None
        end = self._scanner.consume(self._buf)
        if end == -1:
            if self._buf:
                out.append(
                    ToolCallFragment(
                        index=self._tool_index, arguments_fragment=self._buf
                    )
                )
                self._buf = ""
            return False
        out.append(
            ToolCallFragment(index=self._tool_index, arguments_fragment=self._buf[:end])
        )
        self._buf = self._buf[end:]
        self._scanner = None
        self._mode = "tool_tail"
        return True

    def _step_tool_tail(self, out: list[ParserEvent]) -> bool:
        idx = self._buf.find(self._tool_close)
        if idx == -1:
            # Only ever whitespace + (json dialect only) the outer object's
            # closing '}' live here before the closing tag, so it's safe to
            # discard everything except a tag-length tail.
            safe_len = max(0, len(self._buf) - self._max_tag_len)
            self._buf = self._buf[safe_len:]
            return False
        self._buf = self._buf[idx + len(self._tool_close) :]
        self._completed_indices.add(self._tool_index)
        self._mode = "content"  # back to content mode; a parallel call may follow
        return True

    # -- xml_function_parameter dialect --------------------------------------

    def _step_xml_function_header(self, out: list[ParserEvent]) -> bool:
        m = _FUNCTION_OPEN_RE.search(self._buf)
        if not m:
            return False  # wait for the rest of "<function=name>" to arrive
        name = m.group(1).strip()
        self._current_function_name = name
        self._pending_args = {}
        out.append(
            ToolCallFragment(index=self._tool_index, id=_new_call_id(), name=name)
        )
        self._buf = self._buf[m.end() :]
        self._mode = "xml_param_seek"
        return True

    def _step_xml_param_seek(self, out: list[ParserEvent]) -> bool:
        func_close_idx = self._buf.find(_FUNCTION_CLOSE)
        m = _PARAM_OPEN_RE.search(self._buf)
        param_idx = m.start() if m else -1

        if param_idx == -1 and func_close_idx == -1:
            return False  # wait for either the next <parameter=...> or </function>

        if param_idx != -1 and (func_close_idx == -1 or param_idx < func_close_idx):
            self._current_param_key = m.group(1).strip()
            self._buf = self._buf[m.end() :]
            self._mode = "xml_param_value"
            return True

        # No more parameters -- the function block is closing. Serialize
        # everything we collected into one arguments JSON string now,
        # rather than incrementally per-parameter: xml values are raw text
        # that needs type-casting and comma/brace placement isn't knowable
        # until we're sure no further parameter follows, so one clean burst
        # here is far more robust than fiddly incremental JSON assembly.
        schema = self._tool_schemas.get(self._current_function_name or "", {})
        typed_args = {
            key: _coerce_param_value(raw, (schema.get(key) or {}).get("type"))
            for key, raw in self._pending_args.items()
        }
        out.append(
            ToolCallFragment(
                index=self._tool_index, arguments_fragment=json.dumps(typed_args)
            )
        )
        self._buf = self._buf[func_close_idx + len(_FUNCTION_CLOSE) :]
        self._mode = "tool_tail"  # shared with the json dialect
        return True

    def _step_xml_param_value(self, out: list[ParserEvent]) -> bool:
        idx = self._buf.find(_PARAM_CLOSE)
        if idx == -1:
            return False  # wait for </parameter> -- values can be multi-line
        raw_value = _trim_param_value(self._buf[:idx])
        assert self._current_param_key is not None
        self._pending_args[self._current_param_key] = raw_value
        self._buf = self._buf[idx + len(_PARAM_CLOSE) :]
        self._current_param_key = None
        self._mode = "xml_param_seek"
        return True


# -- optional hardening: grammar-constrained arguments ------------------------
#
# This parser trades JSON-grammar guarantees for streaming + auto choice. If
# a particular model/fine-tune turns out to emit malformed output often
# enough to matter, you can claw back some guarantee *without* reintroducing
# the auto+stream restriction: once `_step_tool_header` /
# `_step_xml_function_header` fires (i.e. you know a tool call has started
# and which tool it's for), swap the `Llama` instance's active grammar to a
# `LlamaGrammar` built from that tool's JSON Schema
# (`LlamaGrammar.from_json_schema(schema)` for the json dialect, or a
# hand-written GBNF for the xml dialect's tag structure) before consuming
# further tokens, then swap it back to `None` once the call closes. This is
# a larger change (grammar has to be mutated on the same worker thread
# mid-generation) and isn't implemented here -- flagging it as the natural
# next step if malformed output rate turns out to matter in practice.
