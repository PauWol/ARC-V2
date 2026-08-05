"""Incremental parsing of Qwen3-style model output.

Qwen3 (and Hermes-family models) emit plain text interleaved with two kinds
of tagged blocks:

    <think>...reasoning...</think>normal answer text
    <tool_call>{"name": "...", "arguments": {...}}</tool_call>

Because we stream tokens as they're generated, we can't just wait for the
whole string and split it — tags can be split across token boundaries. This
module is a small buffering state machine: feed() takes whatever text chunk
just arrived and yields fully-resolved ParsedEvent objects as soon as it's
safe to do so (i.e. no partial tag could still be forming at the end of the
buffer).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

EventKind = Literal["reasoning", "content", "tool_call"]


@dataclass
class ParsedEvent:
    kind: EventKind
    text: str = ""  # for "reasoning" / "content"
    tool_name: str | None = None  # for "tool_call"
    tool_arguments: dict | None = None  # for "tool_call"
    raw: str | None = None  # raw tool_call payload if JSON parsing failed


class StreamParser:
    def __init__(
        self,
        think_open: str = "<think>",
        think_close: str = "</think>",
        tool_open: str = "<tool_call>",
        tool_close: str = "</tool_call>",
    ) -> None:
        self._think_open = think_open
        self._think_close = think_close
        self._tool_open = tool_open
        self._tool_close = tool_close
        self._max_tag_len = max(
            len(think_open), len(think_close), len(tool_open), len(tool_close)
        )

        self._buf = ""
        self._mode: Literal["content", "reasoning", "tool_call"] = "content"

    def feed(self, chunk: str) -> list[ParsedEvent]:
        self._buf += chunk
        events: list[ParsedEvent] = []

        while True:
            if self._mode == "content":
                idx_think = self._buf.find(self._think_open)
                idx_tool = self._buf.find(self._tool_open)
                candidates = [i for i in (idx_think, idx_tool) if i != -1]

                if not candidates:
                    # Emit everything except a tail that might be a partial tag opener.
                    safe_len = max(0, len(self._buf) - self._max_tag_len)
                    if safe_len > 0:
                        events.append(ParsedEvent(kind="content", text=self._buf[:safe_len]))
                        self._buf = self._buf[safe_len:]
                    break

                cut = min(candidates)
                if cut > 0:
                    events.append(ParsedEvent(kind="content", text=self._buf[:cut]))
                if cut == idx_think:
                    self._buf = self._buf[cut + len(self._think_open):]
                    self._mode = "reasoning"
                else:
                    self._buf = self._buf[cut + len(self._tool_open):]
                    self._mode = "tool_call"
                continue

            if self._mode == "reasoning":
                idx_close = self._buf.find(self._think_close)
                if idx_close == -1:
                    safe_len = max(0, len(self._buf) - self._max_tag_len)
                    if safe_len > 0:
                        events.append(ParsedEvent(kind="reasoning", text=self._buf[:safe_len]))
                        self._buf = self._buf[safe_len:]
                    break
                if idx_close > 0:
                    events.append(ParsedEvent(kind="reasoning", text=self._buf[:idx_close]))
                self._buf = self._buf[idx_close + len(self._think_close):]
                self._mode = "content"
                continue

            if self._mode == "tool_call":
                idx_close = self._buf.find(self._tool_close)
                if idx_close == -1:
                    break  # need the full JSON payload before we can parse it
                payload = self._buf[:idx_close].strip()
                self._buf = self._buf[idx_close + len(self._tool_close):]
                self._mode = "content"
                events.append(self._parse_tool_call(payload))
                continue

        return events

    def finalize(self) -> list[ParsedEvent]:
        """Flush whatever remains in the buffer at end-of-stream."""
        events: list[ParsedEvent] = []
        if not self._buf:
            return events
        if self._mode == "content":
            events.append(ParsedEvent(kind="content", text=self._buf))
        elif self._mode == "reasoning":
            events.append(ParsedEvent(kind="reasoning", text=self._buf))
        elif self._mode == "tool_call":
            events.append(self._parse_tool_call(self._buf.strip()))
        self._buf = ""
        return events

    @staticmethod
    def _parse_tool_call(payload: str) -> ParsedEvent:
        try:
            data = json.loads(payload)
            return ParsedEvent(
                kind="tool_call",
                tool_name=data.get("name"),
                tool_arguments=data.get("arguments", {}),
            )
        except (json.JSONDecodeError, AttributeError):
            return ParsedEvent(kind="tool_call", raw=payload)
