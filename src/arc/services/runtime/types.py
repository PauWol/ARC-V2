from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class Tool(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON-encoded, matching OpenAI's wire format


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None

    name: str | None = None
    tool_call_id: str | None = None

    # Populated on assistant messages coming *out* of the engine.
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None


class GenerationRequest(BaseModel):
    stream: bool = True

    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
    )
    top_p: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
    )

    stop: list[str] = Field(default_factory=list)

    response_format: dict[str, Any] | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(GenerationRequest):
    messages: list[ChatMessage] = Field(min_length=1)

    tools: list[Tool] = Field(default_factory=list)

    tool_choice: Literal["auto", "none", "required"] | None = None


class CompletionRequest(GenerationRequest):
    prompt: str


class EmbeddingRequest(BaseModel):
    input: str | list[str]

    metadata: dict[str, Any] = Field(default_factory=dict)


# -- Response shapes (OpenAI-compatible) -------------------------------------


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]


class ChatCompletionChunkDelta(BaseModel):
    role: Literal["assistant"] | None = None
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: str | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]


class EmbeddingData(BaseModel):
    index: int
    embedding: list[float]
    object: Literal["embedding"] = "embedding"


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    model: str
    data: list[EmbeddingData]


class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str = "arc"
