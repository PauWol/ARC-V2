"""Renders ChatMessage[] (+ tools) into a prompt string.

Prefers the chat template embedded in the GGUF file's metadata
(`tokenizer.chat_template`), which is how Qwen3/Hermes-family models expect
to receive tool definitions and produce <think>/<tool_call> tags correctly.
Falls back to a hand-written ChatML-with-tools template if the GGUF has none
(some community quantizations strip it).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import jinja2

from arc.services.runtime.types import ChatMessage, Tool

_FALLBACK_TEMPLATE = """\
{%- if tools %}
<|im_start|>system
You have access to the following tools. Call them by responding with
<tool_call>{"name": "...", "arguments": {...}}</tool_call>.
{{ tools | tojson }}
<|im_end|>
{%- endif %}
{%- for message in messages %}
<|im_start|>{{ message.role }}
{{ message.content }}<|im_end|>
{%- endfor %}
{%- if add_generation_prompt %}
<|im_start|>assistant
{%- endif %}
"""


def _raise_exception(message: str):
    """Chat templates (Qwen, Llama, Mistral, ...) commonly call this for
    their own input validation, mirroring the global HuggingFace's
    apply_chat_template injects. Without it, any template that hits a
    validation branch fails with a confusing 'raise_exception is undefined'
    instead of the template's actual, more useful error message."""
    raise jinja2.exceptions.TemplateError(message)


def _strftime_now(fmt: str) -> str:
    return datetime.now().strftime(fmt)


class ChatTemplateRenderer:
    def __init__(self, chat_template: str | None) -> None:
        template_src = chat_template or _FALLBACK_TEMPLATE
        env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True)
        env.globals["raise_exception"] = _raise_exception
        env.globals["strftime_now"] = _strftime_now
        if "tojson" not in env.filters:
            env.filters["tojson"] = lambda obj, **_kwargs: json.dumps(obj)
        self._template = env.from_string(template_src)
        self._using_fallback = chat_template is None

    @property
    def using_fallback(self) -> bool:
        return self._using_fallback

    def render(
        self,
        messages: list[ChatMessage],
        tools: list[Tool] | None = None,
        add_generation_prompt: bool = True,
    ) -> str:
        rendered_messages: list[dict[str, Any]] = [
            {
                "role": m.role,
                "content": m.content or "",
                "name": m.name,
                "tool_call_id": m.tool_call_id,
            }
            for m in messages
        ]
        rendered_tools = None
        if tools:
            rendered_tools = [
                {
                    "type": t.type,
                    "function": {
                        "name": t.function.name,
                        "description": t.function.description,
                        "parameters": t.function.parameters,
                    },
                }
                for t in tools
            ]

        return self._template.render(
            messages=rendered_messages,
            tools=rendered_tools,
            add_generation_prompt=add_generation_prompt,
        )
