"""
KernelService: the Pulse-managed process that owns the agent loop
(AgentLoop, formerly the standalone `Kernel` class), the wakeup sources
(cron/random/dream), state, model router, actions, and optionally the
Telegram interface.

Depends on `runtime` in services.arc.yaml — Pulse won't start this
until RuntimeService reports ready, so the agent loop never comes up
racing against a not-yet-reachable inference backend.

Everything that used to live in a standalone `main()` + `asyncio.run()`
script now lives in `run()`, and shutdown that used to be "whatever
happens when the process gets killed" is now `stop()`, called by Pulse.
"""

from __future__ import annotations

import asyncio

from arc.foundation.service import Service

from arc.services.kernel.actions import build_default_registry
from arc.services.kernel.agent_loop import AgentLoop
from arc.services.kernel.config import CONFIG, configure_from_env
from arc.services.kernel.events import EventQueue
from arc.services.kernel.model_client import ModelRouter
from arc.services.kernel.sources import CronSource, DreamScheduler, RandomSource
from arc.services.kernel.state import StateStore
from arc.services.kernel.telegram_interface import TelegramInterface


class KernelService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="kernel",
            version="0.1.0",
            description="Arc-v2 agent loop: wakeups, state, dreaming, delivery",
        )
        self._stop_event = asyncio.Event()
        self._ready = False
        self._tasks: dict[str, asyncio.Task] = {}
        self._telegram: TelegramInterface | None = None
        self._router: ModelRouter | None = None
        self._agent_loop: AgentLoop | None = None

    async def run(self) -> None:
        assert self.ctx is not None
        log = self.ctx.logger

        # Per the BaseContext contract: env comes from Pulse, don't load
        # .env files here.
        configure_from_env(self.ctx.env)

        log.info("Kernel service initializing")
        try:
            queue = EventQueue()
            state = StateStore(CONFIG.db_path)
            self._router = ModelRouter()
            actions = build_default_registry(state)
            agent_loop = AgentLoop(queue, state, self._router, actions)
            self._agent_loop = agent_loop

            random_source = RandomSource(queue)
            cron_source = CronSource(queue)
            dream_source = DreamScheduler(queue)
            cron_source.add_job("morning_checkin", "0 7 * * *")
            cron_source.add_job("evening_summary", "0 21 * * *")

            self._tasks = {
                "agent_loop": asyncio.create_task(agent_loop.run(), name="agent_loop"),
                "random_source": asyncio.create_task(
                    random_source.run(), name="random_source"
                ),
                "cron_source": asyncio.create_task(
                    cron_source.run(), name="cron_source"
                ),
                "dream_source": asyncio.create_task(
                    dream_source.run(), name="dream_source"
                ),
            }

            if CONFIG.telegram.enabled:
                self._telegram = TelegramInterface(queue)
                agent_loop.register_delivery_channel("telegram", self._telegram.send)
                self._tasks["telegram"] = asyncio.create_task(
                    self._telegram.start(), name="telegram"
                )
                log.info("Telegram interface enabled")
            else:
                log.info("Telegram interface disabled (no bot token configured)")

            self._ready = True
            log.info(
                "Kernel service ready — %d background tasks running", len(self._tasks)
            )

            await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        finally:
            await self._shutdown_tasks()
            log.info("Kernel service stopped")

    async def _shutdown_tasks(self) -> None:
        assert self.ctx is not None
        if self._agent_loop is not None:
            self._agent_loop.stop()

        if self._telegram is not None:
            try:
                await self._telegram.stop()
            except Exception:
                self.ctx.logger.exception("Error stopping telegram interface")

        for name, task in self._tasks.items():
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

        if self._router is not None:
            await self._router.close_all()

    async def stop(self) -> None:
        self._stop_event.set()

    async def ready(self) -> tuple[bool, str | None]:
        if self._ready:
            return True, None
        return False, "still starting"

    async def healthy(self) -> tuple[bool, str | None]:
        if self._stop_event.is_set():
            return False, "service is stopping"
        dead = [name for name, t in self._tasks.items() if t.done()]
        if dead:
            return False, f"background task(s) exited unexpectedly: {dead}"
        return True, None
