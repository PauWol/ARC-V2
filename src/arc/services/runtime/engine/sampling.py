"""Maps GenerationRequest fields to llama-cpp-python call kwargs."""

from __future__ import annotations

from typing import Any

from arc.services.runtime.shared.types import GenerationRequest


def build_sampling_kwargs(req: GenerationRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stream": True,  # engine always streams internally; non-stream is buffered at the API layer
    }
    if req.max_output_tokens is not None:
        kwargs["max_tokens"] = req.max_output_tokens
    if req.temperature is not None:
        kwargs["temperature"] = req.temperature
    if req.top_p is not None:
        kwargs["top_p"] = req.top_p
    if req.top_k is not None:
        kwargs["top_k"] = req.top_k
    if req.stop:
        kwargs["stop"] = req.stop
    return kwargs
