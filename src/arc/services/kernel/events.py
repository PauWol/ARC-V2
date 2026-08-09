"""
Everything that can wake the kernel funnels into a WakeEvent and lands
on one asyncio.PriorityQueue. This is the crux of the "single
serialization point" design: the kernel never has two model calls
racing each other, and priority lets a live user message jump ahead
of a random idle wakeup that's already queued.
"""

from __future__ import annotations
import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Optional


class Priority(IntEnum):
    USER_LIVE = 0        # a real-time message from you — always first
    EXTERNAL_EVENT = 1   # webhook / email / file change etc.
    CRON = 2             # scheduled job
    RANDOM_WAKEUP = 3    # idle/proactive check-in
    DREAM = 4            # lowest priority — never preempts anything live


@dataclass(order=True)
class WakeEvent:
    priority: Priority
    seq: int = field(compare=True)          # tiebreaker so heap never compares payloads
    reason: str = field(compare=False)      # short machine tag, e.g. "cron:daily_summary"
    payload: dict[str, Any] = field(default_factory=dict, compare=False)
    created_at: datetime = field(default_factory=datetime.utcnow, compare=False)
    skip_triage: bool = field(default=False, compare=False)


class EventQueue:
    """Thin wrapper around asyncio.PriorityQueue with an auto-incrementing
    sequence number so same-priority events stay FIFO instead of raising
    a comparison error on the dataclass payload."""

    def __init__(self) -> None:
        self._q: asyncio.PriorityQueue[WakeEvent] = asyncio.PriorityQueue()
        self._counter = itertools.count()

    async def push(
        self,
        priority: Priority,
        reason: str,
        payload: Optional[dict[str, Any]] = None,
        skip_triage: bool = False,
    ) -> None:
        evt = WakeEvent(
            priority=priority,
            seq=next(self._counter),
            reason=reason,
            payload=payload or {},
            skip_triage=skip_triage,
        )
        await self._q.put(evt)

    async def pop(self) -> WakeEvent:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()
