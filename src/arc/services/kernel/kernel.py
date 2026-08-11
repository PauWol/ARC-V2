import asyncio
from asyncio.locks import Event
from typing import Any

from arc.foundation.service import Service


class Kernel(Service):
    def __init__(self) -> None:
        super().__init__("Kernel", "0.0.1", "The main control layer of Arc.")

        # Service runtime vars
        self._stop_event: Event = asyncio.Event()
        self._ready: bool = False

        # Kernel specific vars
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def run(self) -> None:
        return await super().run()

    async def healthy(self) -> tuple[bool, str | None]:  # pyright: ignore[reportImplicitOverride]
        return True, None

    async def ready(self) -> tuple[bool, str | None]:  # pyright: ignore[reportImplicitOverride]
        return self._ready, None

    async def stop(self) -> None:  # pyright: ignore[reportImplicitOverride]
        self._stop_event.set()
