"""
Action layer. Deliberately minimal — this is where you'll register your
real tools (filesystem, messaging, home automation, whatever Jarvis
needs to touch). The kernel only knows about `ActionRegistry.execute`;
it doesn't care what's behind it.

Autonomous (non-user-initiated) actions are rate-limited via StateStore
so a bad triage/main-model decision loop can't spam the world.
"""

from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable

from arc.services.kernel.config import CONFIG
from arc.services.kernel.state import StateStore

log = logging.getLogger("arc.actions")

ActionFn = Callable[[dict[str, Any]], Awaitable[str]]


class ActionRegistry:
    def __init__(self, state: StateStore) -> None:
        self.state = state
        self._actions: dict[str, ActionFn] = {}

    def register(self, name: str) -> Callable[[ActionFn], ActionFn]:
        def decorator(fn: ActionFn) -> ActionFn:
            self._actions[name] = fn
            return fn

        return decorator

    async def execute(
        self, name: str, args: dict[str, Any], is_autonomous: bool
    ) -> str:
        if name not in self._actions:
            return f"error: unknown action '{name}'"

        if is_autonomous:
            count = self.state.autonomous_actions_last_hour()
            if count >= CONFIG.wakeups.max_autonomous_actions_per_hour:
                log.warning(
                    "Autonomous action '%s' blocked — rate limit hit (%d/hr)",
                    name,
                    count,
                )
                return "blocked: autonomous action rate limit exceeded, needs human check-in"
            self.state.record_autonomous_action(name)

        log.info(
            "Executing action '%s' args=%s autonomous=%s", name, args, is_autonomous
        )
        try:
            return await self._actions[name](args)
        except Exception as e:
            log.exception("Action '%s' raised", name)
            return f"error: action '{name}' failed: {e}"


# --- example built-in actions, replace/extend freely ---


def build_default_registry(state: StateStore) -> ActionRegistry:
    registry = ActionRegistry(state)

    @registry.register("noop")
    async def _noop(args: dict[str, Any]) -> str:
        return "ok: no action taken"

    @registry.register("log_note")
    async def _log_note(args: dict[str, Any]) -> str:
        note = args.get("note", "")
        state.log_episode(source="tool_call", content=note, reason="log_note")
        return "ok: note logged"

    return registry
