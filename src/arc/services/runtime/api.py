import json
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from arc.foundation.constants import ARC_RUNTIME_DEBUG
from arc.services.runtime.engine.engine import LlamaEngine
from arc.services.runtime.engine.parsing import ParsedEvent
from arc.services.runtime.types import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionResponse,
    ChatMessage,
    ChatRequest,
    CompletionRequest,
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelInfo,
    ToolCall,
    ToolCallFunction,
)

app = FastAPI(debug=ARC_RUNTIME_DEBUG)

VERSION = "/v1"


def version_string(_path: str):
    return f"{VERSION}{_path}"


def _get_engine(request: Request) -> LlamaEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Model is not loaded yet")
    return engine


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


# -- accumulation helpers: turn a stream of ParsedEvents into a final ChatMessage --


class _Accumulator:
    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.reasoning_parts: list[str] = []
        self.tool_calls: list[ToolCall] = []

    def add(self, event: ParsedEvent) -> None:
        if event.kind == "content" and event.text:
            self.content_parts.append(event.text)
        elif event.kind == "reasoning" and event.text:
            self.reasoning_parts.append(event.text)
        elif event.kind == "tool_call":
            self.tool_calls.append(
                ToolCall(
                    id=_new_id("call"),
                    function=ToolCallFunction(
                        name=event.tool_name or "",
                        arguments=json.dumps(event.tool_arguments or {}),
                    ),
                )
            )

    def to_message(self) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content="".join(self.content_parts) or None,
            reasoning_content="".join(self.reasoning_parts) or None,
            tool_calls=self.tool_calls or None,
        )


# -- routes -------------------------------------------------------------------


@app.post(version_string("/chat/completions"))
async def chat_completions(req: ChatRequest, request: Request):
    engine = _get_engine(request)
    completion_id = _new_id("chatcmpl")
    created = int(time.time())

    if req.stream:

        async def event_stream():
            async for chunk in engine.generate_chat(req):
                delta = ChatCompletionChunkDelta()
                if chunk.event.kind == "content" and chunk.event.text:
                    delta.content = chunk.event.text
                elif chunk.event.kind == "reasoning" and chunk.event.text:
                    delta.reasoning_content = chunk.event.text
                elif chunk.event.kind == "tool_call":
                    delta.tool_calls = [
                        ToolCall(
                            id=_new_id("call"),
                            function=ToolCallFunction(
                                name=chunk.event.tool_name or "",
                                arguments=json.dumps(chunk.event.tool_arguments or {}),
                            ),
                        )
                    ]

                out = ChatCompletionChunk(
                    id=completion_id,
                    created=created,
                    model=engine.model_name,
                    choices=[
                        ChatCompletionChunkChoice(
                            delta=delta, finish_reason=chunk.finish_reason
                        )
                    ],
                )
                yield f"data: {out.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    acc = _Accumulator()
    finish_reason = None
    async for chunk in engine.generate_chat(req):
        acc.add(chunk.event)
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason

    return ChatCompletionResponse(
        id=completion_id,
        created=created,
        model=engine.model_name,
        choices=[
            ChatCompletionChoice(message=acc.to_message(), finish_reason=finish_reason)
        ],
    )


@app.post(version_string("/completions"))
async def completions(req: CompletionRequest, request: Request):
    engine = _get_engine(request)
    completion_id = _new_id("cmpl")
    created = int(time.time())

    if req.stream:

        async def event_stream():
            async for chunk in engine.generate_completion(req):
                if chunk.event.kind != "content":
                    continue  # raw /completions has no reasoning/tool-call channel
                payload = {
                    "id": completion_id,
                    "object": "text_completion",
                    "created": created,
                    "model": engine.model_name,
                    "choices": [
                        {
                            "index": 0,
                            "text": chunk.event.text,
                            "finish_reason": chunk.finish_reason,
                        }
                    ],
                }
                yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    text_parts: list[str] = []
    finish_reason = None
    async for chunk in engine.generate_completion(req):
        if chunk.event.kind == "content":
            text_parts.append(chunk.event.text)
        if chunk.finish_reason:
            finish_reason = chunk.finish_reason

    return {
        "id": completion_id,
        "object": "text_completion",
        "created": created,
        "model": engine.model_name,
        "choices": [
            {"index": 0, "text": "".join(text_parts), "finish_reason": finish_reason}
        ],
    }


@app.post(version_string("/embeddings"))
async def embeddings(req: EmbeddingRequest, request: Request):
    engine = _get_engine(request)
    inputs = [req.input] if isinstance(req.input, str) else req.input
    vectors = await engine.embed(inputs)
    return EmbeddingResponse(
        model=engine.model_name,
        data=[EmbeddingData(index=i, embedding=vec) for i, vec in enumerate(vectors)],
    )


@app.get(version_string("/models"))
async def models(request: Request):
    engine = _get_engine(request)
    return {"object": "list", "data": [ModelInfo(id=engine.model_name).model_dump()]}


@app.get(version_string("/model"))
async def model(request: Request):
    engine = _get_engine(request)
    return ModelInfo(id=engine.model_name)
