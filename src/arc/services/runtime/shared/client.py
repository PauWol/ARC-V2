from collections.abc import Callable
from dataclasses import dataclass, field

from arc.foundation.constants import ARC_RUNTIME_PORT
from arc.services.runtime.shared.types import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    EmbeddingResponse,
    ModelInfo,
)


@dataclass
class WorkingMemory:
    messages: list[str] = field(default_factory=list[str])


class Client:
    def __init__(self, working_memory: WorkingMemory | None = None) -> None:
        if not working_memory:
            working_memory = WorkingMemory()

        self._working_mem = working_memory
        self._api_endpoint = f"http://localhost:{ARC_RUNTIME_PORT}"

    async def chat_completion(
        self, messages: list[str], tools: list[Callable]
    ) -> ChatCompletionResponse:
        pass

    async def stream_chat_completion(
        self, messages: list[str], tools: list[Callable]
    ) -> ChatCompletionChunk:
        pass

    async def embed(self, query: str) -> EmbeddingResponse:
        pass

    async def model_info(self) -> ModelInfo:
        pass
