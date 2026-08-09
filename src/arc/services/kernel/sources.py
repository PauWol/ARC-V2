"""
Wakeup sources. Each one is an independent asyncio task that pushes
WakeEvents onto the shared EventQueue — they never touch the model or
state directly, keeping them trivially testable in isolation.
"""

from __future__ import annotations
import asyncio
import logging
import random
from datetime import datetime

from croniter import croniter

from arc.services.kernel.config import CONFIG
from arc.services.kernel.events import EventQueue, Priority

log = logging.getLogger("arc.sources")


class RandomSource:
    """Fires wakeups at random (exponentially distributed) intervals to
    give the system its 'proactive, alive' feel. Most of these should
    resolve to a no-op after triage — that's expected and fine."""

    def __init__(self, queue: EventQueue) -> None:
        self.queue = queue
        self.enabled = CONFIG.wakeups.random_wakeup_enabled

    async def run(self) -> None:
        if not self.enabled:
            log.info("RandomSource disabled")
            return
        while True:
            interval = max(
                random.expovariate(1 / CONFIG.wakeups.random_wakeup_mean_s),
                CONFIG.wakeups.random_wakeup_min_s,
            )
            await asyncio.sleep(interval)
            log.debug("RandomSource firing wakeup after %.0fs", interval)
            await self.queue.push(
                priority=Priority.RANDOM_WAKEUP,
                reason="random_idle_wakeup",
            )


class CronSource:
    """Runs a set of named cron jobs. Add jobs via `add_job`; each is
    checked once a minute against its croniter schedule."""

    def __init__(self, queue: EventQueue) -> None:
        self.queue = queue
        self._jobs: dict[str, str] = {}  # name -> cron expr

    def add_job(self, name: str, cron_expr: str) -> None:
        self._jobs[name] = cron_expr

    async def run(self) -> None:
        # track next fire time per job so we don't double-fire within a minute
        next_fire = {
            name: croniter(expr, datetime.utcnow()).get_next(datetime)
            for name, expr in self._jobs.items()
        }
        while True:
            now = datetime.utcnow()
            for name, expr in self._jobs.items():
                if now >= next_fire[name]:
                    log.info("CronSource firing job %s", name)
                    await self.queue.push(
                        priority=Priority.CRON,
                        reason=f"cron:{name}",
                        skip_triage=True,  # scheduled jobs are pre-approved, skip triage
                    )
                    next_fire[name] = croniter(expr, now).get_next(datetime)
            await asyncio.sleep(30)


class ExternalEventSource:
    """Placeholder for webhook/queue-driven wakeups (new email, file
    change, message received, etc). Wire your actual listener
    (FastAPI route, Redis pubsub, filesystem watcher...) to call
    `.notify()`."""

    def __init__(self, queue: EventQueue) -> None:
        self.queue = queue

    async def notify(self, reason: str, payload: dict | None = None) -> None:
        log.info("ExternalEventSource: %s", reason)
        await self.queue.push(
            priority=Priority.EXTERNAL_EVENT,
            reason=reason,
            payload=payload or {},
        )


class DreamScheduler:
    """Dedicated cron-like source just for the dream cycle, so it's
    trivial to reason about separately from regular cron jobs."""

    def __init__(self, queue: EventQueue) -> None:
        self.queue = queue
        self.expr = CONFIG.wakeups.dream_cron

    async def run(self) -> None:
        next_fire = croniter(self.expr, datetime.utcnow()).get_next(datetime)
        while True:
            now = datetime.utcnow()
            if now >= next_fire:
                log.info("DreamScheduler firing dream cycle")
                await self.queue.push(
                    priority=Priority.DREAM,
                    reason="dream_cycle",
                    skip_triage=True,
                )
                next_fire = croniter(self.expr, now).get_next(datetime)
            await asyncio.sleep(30)
