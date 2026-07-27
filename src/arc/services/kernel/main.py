# arc/services/test/main.py
from __future__ import annotations

import asyncio

from arc.foundation.service import BaseContext


async def start(ctx: BaseContext) -> None:
    ctx.logger.info("test service started")

    tick = 0
    try:
        while True:
            tick += 1
            ctx.logger.info("test service tick %d", tick)
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        ctx.logger.info("test service cancelled")
        raise
    finally:
        ctx.logger.info("test service stopped")
