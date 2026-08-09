"""
Thin async client over OpenAI-compatible chat endpoints (vLLM, llama.cpp
server, Ollama --compat, etc). One instance per tier. Kept deliberately
dumb — no retries/backoff logic beyond a basic timeout, since local
inference failures usually mean "server not running" and you want that
loud, not silently swallowed.
"""

from __future__ import annotations
import logging
from typing import Any, Optional

import httpx

from arc.services.kernel.config import ModelEndpoint, CONFIG

log = logging.getLogger("arc.model")


class ModelClient:
    def __init__(self, endpoint: ModelEndpoint) -> None:
        self.endpoint = endpoint
        self._client = httpx.AsyncClient(
            base_url=endpoint.base_url,
            timeout=endpoint.timeout_s,
            headers={"Authorization": f"Bearer {endpoint.api_key}"},
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        response_format: Optional[dict[str, Any]] = None,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.endpoint.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format
        if tools:
            body["tools"] = tools

        try:
            resp = await self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error(
                "Model call to %s (%s) failed: %s",
                self.endpoint.base_url,
                self.endpoint.model_name,
                e,
            )
            raise

        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def extract_text(response: dict[str, Any]) -> str:
        return response["choices"][0]["message"]["content"]


class ModelRouter:
    """Owns the three tier clients and exposes them by name. Nothing
    fancier than that — the *decision* of which tier to use lives in
    kernel.py / triage.py, not here."""

    def __init__(self) -> None:
        self.triage = ModelClient(CONFIG.models.triage)
        self.main = ModelClient(CONFIG.models.main)
        self.big = ModelClient(CONFIG.models.big)

    def get(self, tier: str) -> ModelClient:
        return {"triage": self.triage, "main": self.main, "big": self.big}[tier]

    async def close_all(self) -> None:
        for c in (self.triage, self.main, self.big):
            await c.close()
