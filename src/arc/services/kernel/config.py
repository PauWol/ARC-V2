"""
Central configuration for the Arc-v2 kernel.

Everything that varies by machine/setup lives here — model endpoints,
wakeup cadence, context budgets. Load from env vars / a yaml file in
production; defaults below are sane for a single local box running
an OpenAI-compatible server (vLLM / llama.cpp server / Ollama) per tier.
"""

from __future__ import annotations
from typing import Mapping
from pydantic import BaseModel, Field


class ModelEndpoint(BaseModel):
    base_url: str
    model_name: str
    api_key: str = "not-needed"      # local servers usually ignore this
    max_context_tokens: int = 8192
    timeout_s: float = 120.0


class ModelTiers(BaseModel):
    """
    Three tiers. Triage should be small/fast and can be the SAME server
    as `main` running the 4B if you don't want a second process — just
    point both at different model_name / port.
    """
    triage: ModelEndpoint = ModelEndpoint(
        base_url="http://localhost:8001/v1",
        model_name="qwen3-4b",
        max_context_tokens=4096,
    )
    main: ModelEndpoint = ModelEndpoint(
        base_url="http://localhost:8000/v1",
        model_name="qwen3-9b",
        max_context_tokens=32768,
    )
    big: ModelEndpoint = ModelEndpoint(
        base_url="http://localhost:8002/v1",
        model_name="qwen3-big",
        max_context_tokens=65536,
        timeout_s=600.0,           # give it room, esp. if loaded on-demand
    )


class WakeupConfig(BaseModel):
    # Random/idle wakeups: mean interval in seconds, exponential distribution
    random_wakeup_mean_s: float = 60 * 45          # ~every 45 min on average
    random_wakeup_min_s: float = 60 * 10           # never fire more often than this
    random_wakeup_enabled: bool = True

    # Dream cycle: cron-style, e.g. nightly
    dream_cron: str = "0 3 * * *"                  # 3am daily (croniter format)
    dream_min_new_episodes: int = 5                 # skip dreaming if nothing happened

    # Safety valve: cap self-triggered (non-user-initiated) actions per hour
    max_autonomous_actions_per_hour: int = 12


class TelegramConfig(BaseModel):
    bot_token: str = ""                 # set via env var in practice, not hardcoded
    primary_chat_id: int = 0             # your chat id — used for autonomous outbound messages
                                          # (cron/random wakeups have no inbound message to reply to)
    enabled: bool = False


class KernelConfig(BaseModel):
    models: ModelTiers = ModelTiers()
    wakeups: WakeupConfig = WakeupConfig()
    telegram: TelegramConfig = TelegramConfig()
    db_path: str = "./arc_state.db"
    chroma_path: str = "./arc_chroma"
    log_path: str = "./arc_kernel.log"
    persona_pinned_facts_limit: int = 20
    context_semantic_recall_k: int = 8


CONFIG = KernelConfig()


def configure_from_env(env: Mapping[str, str]) -> None:
    """Populate CONFIG from ctx.env at service startup. Per the ARC
    Services contract, services receive env from Pulse and should not
    load .env files themselves — this is the single place that reads
    it. Called once from KernelService.run() before anything else is
    constructed. Unset vars keep the defaults above."""

    def _get(key: str, default: str | None = None) -> str | None:
        return env.get(key, default)

    if v := _get("ARC_TRIAGE_MODEL_URL"):
        CONFIG.models.triage.base_url = v
    if v := _get("ARC_TRIAGE_MODEL_NAME"):
        CONFIG.models.triage.model_name = v

    if v := _get("ARC_MAIN_MODEL_URL"):
        CONFIG.models.main.base_url = v
    if v := _get("ARC_MAIN_MODEL_NAME"):
        CONFIG.models.main.model_name = v

    if v := _get("ARC_BIG_MODEL_URL"):
        CONFIG.models.big.base_url = v
    if v := _get("ARC_BIG_MODEL_NAME"):
        CONFIG.models.big.model_name = v

    if v := _get("ARC_DB_PATH"):
        CONFIG.db_path = v
    if v := _get("ARC_CHROMA_PATH"):
        CONFIG.chroma_path = v

    if v := _get("ARC_TELEGRAM_BOT_TOKEN"):
        CONFIG.telegram.bot_token = v
        CONFIG.telegram.enabled = True
    if v := _get("ARC_TELEGRAM_PRIMARY_CHAT_ID"):
        CONFIG.telegram.primary_chat_id = int(v)

    if v := _get("ARC_RANDOM_WAKEUP_MEAN_S"):
        CONFIG.wakeups.random_wakeup_mean_s = float(v)
    if v := _get("ARC_DREAM_CRON"):
        CONFIG.wakeups.dream_cron = v
