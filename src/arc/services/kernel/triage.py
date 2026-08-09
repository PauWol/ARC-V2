"""
The triage pass exists to make random/idle wakeups cheap in LATENCY
(not token cost, since this is local) — most wakeups should resolve
to "nothing to do" without ever touching the main 9B model.

Returns a small structured decision the kernel acts on directly.
"""

from __future__ import annotations
import json
import logging
from typing import Literal

from pydantic import BaseModel

from arc.services.kernel.model_client import ModelRouter
from arc.services.kernel.events import WakeEvent

log = logging.getLogger("arc.triage")

TRIAGE_SYSTEM_PROMPT = """\
You are the triage layer of an autonomous local assistant. You are given \
a reason the system woke up, plus brief recent context. Decide whether \
this warrants engaging the main reasoning model, and at what tier.

Respond ONLY with JSON matching this schema, no prose, no markdown fences:
{"action": "ignore" | "respond_main" | "escalate_big", "rationale": "<one short sentence>"}

Guidelines:
- "ignore": nothing meaningful happened, or it's a routine check with no signal.
- "respond_main": normal-complexity task, conversation, or routine action.
- "escalate_big": genuinely complex reasoning, planning, or this IS a dream cycle.
Default to "ignore" for random idle wakeups unless there's a real reason to act.
"""


class TriageDecision(BaseModel):
    action: Literal["ignore", "respond_main", "escalate_big"]
    rationale: str


async def triage(
    router: ModelRouter, event: WakeEvent, recent_context: str
) -> TriageDecision:
    if event.skip_triage:
        # cron/dream events are pre-approved — route straight to main by default
        return TriageDecision(action="respond_main", rationale="skip_triage set")

    messages = [
        {"role": "system", "content": TRIAGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Wakeup reason: {event.reason}\n"
                f"Payload: {json.dumps(event.payload)}\n"
                f"Recent context:\n{recent_context}"
            ),
        },
    ]

    try:
        resp = await router.triage.chat(
            messages,
            temperature=0.1,
            max_tokens=150,
            response_format={"type": "json_object"},
        )
        raw = router.triage.extract_text(resp)
        data = json.loads(raw)
        return TriageDecision(**data)
    except Exception as e:
        log.warning("Triage failed (%s) — defaulting to respond_main to be safe", e)
        return TriageDecision(action="respond_main", rationale="triage_error_fallback")
