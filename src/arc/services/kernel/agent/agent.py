"""
The Kernel: pulls WakeEvents off the queue one at a time (single
serialization point — no concurrent model calls), triages, builds a
context prompt from state, calls the appropriate model tier, executes
any resulting action, and writes everything back to the episodic log.

This file intentionally does NOT contain the semantic/vector retrieval
piece (build_context's "semantically relevant older episodes" layer) —
wire in your Chroma/LanceDB lookup inside `_gather_semantic_recall`
where marked. Everything else runs without it (degrades to recency-only
recall), so you can bring the kernel up before the vector layer exists.
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from arc.services.kernel.config import CONFIG
from arc.services.kernel.events import EventQueue, WakeEvent, Priority
from arc.services.kernel.state import StateStore
from arc.services.kernel.actions import ActionRegistry
from arc.services.kernel.dream import run_dream_cycle

log = logging.getLogger("arc.agent_loop")

MAIN_SYSTEM_TEMPLATE = """\
{core_identity}

Current goals:
{current_goals}

Behavioral rules:
{behavioral_rules}

Recent self-summary: {recent_self_summary}

Relevant facts about the user/world:
{facts}

You woke up because: {wake_reason}
If this warrants an action, respond ONLY with JSON:
{{"say": "<message to surface to the user, or empty string if nothing to say>",
  "action": "<action name or 'noop'>", "action_args": {{...}}}}
If nothing needs to happen, use action "noop" and an empty "say".
"""


class Agent:
    def __init__(
        self,
        queue: EventQueue,
        state: StateStore,
        actions: ActionRegistry,
    ) -> None:
        self.queue = queue
        self.state = state
        self.actions = actions
        self._last_dream_at = datetime.utcnow() - timedelta(days=1)
        self._shutdown = asyncio.Event()
        # channel name -> async fn(chat_id, text). Registered by interfaces
        # (Telegram, CLI, etc) via register_delivery_channel. This is what
        # lets _deliver actually reach the user instead of just logging.
        self._delivery_channels: dict[str, Any] = {}

    def register_delivery_channel(self, name: str, send_fn) -> None:
        """send_fn: async def send_fn(chat_id: Any, text: str) -> None"""
        log.info("Registered delivery channel: %s", name)
        self._delivery_channels[name] = send_fn

    async def run(self) -> None:
        log.info("Kernel main loop starting")
        while not self._shutdown.is_set():
            event = await self.queue.pop()
            try:
                await self._handle_event(event)
            except Exception:
                log.exception("Unhandled error processing event %s", event.reason)
            finally:
                self.queue.task_done()

    def stop(self) -> None:
        self._shutdown.set()

    # ---------------- event handling ----------------

    async def _handle_event(self, event: WakeEvent) -> None:
        log.info("Handling event: %s (priority=%s)", event.reason, event.priority.name)

        if event.priority == Priority.DREAM:
            await self._run_dream(event)
            return

        await self._respond(event)

    async def _respond(self, event: WakeEvent) -> None:
        persona = self.state.current_persona()
        facts_text = self._facts_context_text()
        system_prompt = MAIN_SYSTEM_TEMPLATE.format(
            core_identity=persona["core_identity"],
            current_goals="\n".join(f"- {g}" for g in persona.get("current_goals", []))
            or "(none set)",
            behavioral_rules="\n".join(
                f"- {r}" for r in persona.get("behavioral_rules", [])
            ),
            recent_self_summary=persona.get("recent_self_summary", "(none yet)"),
            facts=facts_text,
            wake_reason=f"{event.reason} | payload={json.dumps(event.payload)}",
        )

        user_message = (
            event.payload.get("message") or f"[system wakeup: {event.reason}]"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            *self._recent_context_messages(limit=12),
            {"role": "user", "content": user_message},
        ]

        resp = await client.chat(
            messages,
            temperature=0.6,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        raw = client.extract_text(resp)

        try:
            parsed: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "Main model returned non-JSON, treating as plain say: %r", raw[:200]
            )
            parsed = {"say": raw, "action": "noop", "action_args": {}}

        say = parsed.get("say", "") or ""
        action_name = parsed.get("action", "noop") or "noop"
        action_args = parsed.get("action_args", {}) or {}

        self.state.log_episode(
            source="assistant",
            content=json.dumps(parsed),
            reason=event.reason,
        )

        if action_name != "noop":
            result = await self.actions.execute(action_name, action_args, is_autonomous)
            self.state.log_episode(
                source="tool_call", content=result, reason=action_name
            )

        if say:
            await self._deliver(say, event)

    async def _deliver(self, message: str, event: WakeEvent) -> None:
        """Route a message back out. Live user messages carry their own
        channel/chat_id in payload (set by the interface that created the
        event). Autonomous wakeups (cron/random/dream) have no inbound
        chat to reply to, so they fall back to the configured primary
        channel/chat_id — this is what lets Arc proactively message you
        instead of only replying."""
        channel = event.payload.get("channel")
        chat_id = event.payload.get("chat_id")

        if channel is None:
            # autonomous wakeup with no originating chat — use default
            if CONFIG.telegram.enabled and CONFIG.telegram.primary_chat_id:
                channel = "telegram"
                chat_id = CONFIG.telegram.primary_chat_id
            else:
                log.info("[ARC -> USER] (no delivery channel configured) %s", message)
                return

        send_fn = self._delivery_channels.get(channel)
        if send_fn is None:
            log.warning(
                "No delivery channel registered for '%s', dropping message: %s",
                channel,
                message[:100],
            )
            return

        try:
            await send_fn(chat_id, message)
        except Exception:
            log.exception("Delivery via channel '%s' failed", channel)

    # ---------------- dream cycle ----------------

    async def _run_dream(self, event: WakeEvent) -> None:
        reflection = await run_dream_cycle(self.router, self.state, self._last_dream_at)
        self._last_dream_at = datetime.utcnow()
        if reflection is None:
            log.info("Dream cycle produced nothing (insufficient new episodes).")

    # ---------------- context assembly ----------------

    def _recent_context_text(self, limit: int = 10) -> str:
        episodes = self.state.recent_episodes(limit=limit)
        return "\n".join(f"({e['source']}) {e['content'][:200]}" for e in episodes)

    def _recent_context_messages(self, limit: int = 12) -> list[dict[str, str]]:
        episodes = self.state.recent_episodes(limit=limit)
        out = []
        for e in episodes:
            role = "assistant" if e["source"] in ("assistant", "dream") else "user"
            out.append({"role": role, "content": e["content"][:1000]})
        return out

    def _facts_context_text(self) -> str:
        pinned = self.state.pinned_facts()
        # TODO: augment with semantic recall against the current wake
        # reason/message via your vector store here, e.g.:
        #   recalled = vector_store.query(query_text, k=CONFIG.context_semantic_recall_k)
        # and merge (dedupe by fact id) with `pinned` before formatting.
        if not pinned:
            return "(no pinned facts yet)"
        return "\n".join(
            f"- {f['subject']} {f['predicate']} {f['value']}" for f in pinned
        )
