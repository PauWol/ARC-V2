from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

from llama_cpp import Llama


MODEL_PATH = Path(
    "/home/paul/arc/models/unsloth__Qwen3.5-4B-GGUF/Qwen3.5-4B-Q4_K_M.gguf"
)

SYSTEM_PROMPT = "You are Alex, a personal all-in-one assistant (schedule, coding, home, study). Personality: calm, competent, dry wit, direct but warm, quietly proactive (flag issues before asked), never falsely enthusiastic. Keep replies concise. Give real answers, not hedged corporate-speak. Own mistakes briefly, then fix and move on. Nudge/remind once or twice max, don't nag. Name meaning if asked: 'Advanced Life Executor' — unofficially, a better Alexa."


class ToolCallFunction(TypedDict):
    name: str
    arguments: str


class ToolCall(TypedDict, total=False):
    id: str
    type: str
    function: ToolCallFunction


class ChatMessage(TypedDict, total=False):
    role: str
    content: str | None
    tool_calls: list[ToolCall]
    tool_call_id: str


ToolArgs = dict[str, object]
ToolFn = Callable[[ToolArgs], str | float]


def get_time(zone: str = "local") -> str:
    if zone != "local":
        return f"unsupported zone: {zone}"
    return datetime.now(timezone.utc).astimezone().isoformat()


def add(a: float, b: float) -> float:
    return a + b


TOOLS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local time as an ISO-8601 string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "zone": {
                        "type": "string",
                        "description": "Use 'local'.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Add two numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        },
    },
]

TOOL_IMPLS: dict[str, ToolFn] = {
    "get_time": lambda args: get_time(str(args.get("zone", "local"))),
    "add": lambda args: add(float(args["a"]), float(args["b"])),
}


def as_str(value: object) -> str:
    return value if isinstance(value, str) else str(value)


def parse_tool_arguments(raw_args: object) -> ToolArgs:
    if isinstance(raw_args, dict):
        return cast(ToolArgs, raw_args)

    if isinstance(raw_args, str):
        if not raw_args.strip():
            return {}
        parsed = json.loads(raw_args)
        if isinstance(parsed, dict):
            return cast(ToolArgs, parsed)
        raise TypeError("Tool arguments must be a JSON object.")

    if raw_args is None:
        return {}

    raise TypeError(f"Unsupported tool arguments type: {type(raw_args)!r}")


def run_tool(tool_name: str, args: ToolArgs) -> str:
    tool = TOOL_IMPLS.get(tool_name)
    if tool is None:
        return f"Unknown tool: {tool_name}"

    try:
        result = tool(args)
    except (KeyError, TypeError, ValueError) as exc:
        return f"Tool error in {tool_name}: {exc}"

    return as_str(result)


def print_tool_call(tc: ToolCall) -> None:
    fn = tc.get("function", {})
    name = fn.get("name", "<unknown>")
    raw_args = fn.get("arguments", "{}")

    print("\n[tool call]")
    print(f"  name: {name}")
    print(f"  args: {raw_args}")


def main() -> None:
    llm = Llama(
        model_path=str(MODEL_PATH),
        n_ctx=8192,
        n_gpu_layers=-1,
        chat_format="chatml-function-calling",
        verbose=False,  # keeps llama.cpp logs quiet
    )

    messages: list[ChatMessage] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    print("ARC chat ready. Type 'exit' to quit.\n")

    while True:
        user_text = input("you> ").strip()
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break

        messages.append({"role": "user", "content": user_text})

        while True:
            response = llm.create_chat_completion(
                messages=cast(list[dict[str, object]], messages),
                # tools=TOOLS,
                # tool_choice="auto",
                temperature=0.2,
            )

            choices = cast(list[object], response["choices"])
            choice = cast(dict[str, object], choices[0])
            assistant_msg = cast(dict[str, object], choice["message"])

            content = assistant_msg.get("content")
            tool_calls_obj = assistant_msg.get("tool_calls")

            if isinstance(content, str) and content.strip():
                print(f"\narc> {content}")

            # No tool call -> assistant is done for this turn.
            if not isinstance(tool_calls_obj, list) or len(tool_calls_obj) == 0:
                messages.append(
                    {
                        "role": "assistant",
                        "content": content if isinstance(content, str) else None,
                    }
                )
                print()
                break

            # Store assistant tool-call message.
            tool_calls: list[ToolCall] = []
            for item in tool_calls_obj:
                if isinstance(item, dict):
                    tool_calls.append(cast(ToolCall, item))

            messages.append(
                {
                    "role": "assistant",
                    "content": content if isinstance(content, str) else None,
                    "tool_calls": tool_calls,
                }
            )

            # Execute every tool call.
            for tc in tool_calls:
                print_tool_call(tc)

                fn = tc.get("function")
                if not isinstance(fn, dict):
                    tool_result = "Tool call missing function payload."
                    print(f"[tool result] {tool_result}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": as_str(tc.get("id", "")),
                            "content": tool_result,
                        }
                    )
                    continue

                fn_name = as_str(fn.get("name", ""))
                raw_args = fn.get("arguments", "{}")

                try:
                    args = parse_tool_arguments(raw_args)
                except Exception as exc:
                    tool_result = f"Failed to parse tool args for {fn_name}: {exc}"
                    print(f"[tool result] {tool_result}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": as_str(tc.get("id", "")),
                            "content": tool_result,
                        }
                    )
                    continue

                tool_result = run_tool(fn_name, args)
                print(f"[tool result] {tool_result}")

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": as_str(tc.get("id", "")),
                        "content": tool_result,
                    }
                )


if __name__ == "__main__":
    main()
