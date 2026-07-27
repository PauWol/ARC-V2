# arc/services/runtime/main.py
from __future__ import annotations

import asyncio

from arc.foundation.service import BaseContext, Service


class RuntimeService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="runtime",
            version="1.0.0",
            description="ARC runtime service",
        )
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info("runtime service started")

        tick = 0
        while not self._stop_event.is_set():
            tick += 1
            self.ctx.logger.info("runtime tick %d", tick)

            if tick == 5:
                raise RuntimeError("Test error")
            await asyncio.sleep(2)

        self.ctx.logger.info("runtime service run loop ended")

    async def stop(self) -> None:
        assert self.ctx is not None
        self.ctx.logger.info("runtime service stopping")
        self._stop_event.set()
