from __future__ import annotations

import asyncio

from arc.foundation.service import BaseContext, Service


class TestService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="test",
            version="0.1.0",
            description="A simple test service for Pulse",
        )
        self._stop_event = asyncio.Event()
        self._ready = False
        self._ticks = 0

    async def run(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info("test service started")

        # Simulate startup work
        await asyncio.sleep(1.0)
        self._ready = True
        self.ctx.logger.info("test service is ready")

        try:
            while not self._stop_event.is_set():
                self._ticks += 1
                self.ctx.logger.info("test service tick %d", self._ticks)
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            self.ctx.logger.info("test service cancelled")
            raise
        finally:
            self.ctx.logger.info("test service stopped")

    async def stop(self) -> None:
        self._stop_event.set()

    async def healthy(self) -> tuple[bool, str | None]:
        if self._stop_event.is_set():
            return False, "service is stopping"
        return True, None

    async def ready(self) -> tuple[bool, str | None]:
        if self._ready:
            return True, None
        return False, "still starting"
