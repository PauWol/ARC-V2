import asyncio
import logging

from arc.foundation.constants import load_dot_env
from arc.foundation.logger import setup_logging
from arc.pulse.pulse import Pulse


async def main() -> None:
    env_loaded = load_dot_env()
    _ = setup_logging()
    logger = logging.getLogger("boot")

    if not env_loaded:
        logger.warning("ARC .env not found. Using default configuration.")

    pulse = Pulse()

    try:
        await pulse.startup()
        logger.info("Boot startup completed!")
        await pulse.supervise()

    except asyncio.CancelledError:
        logger.info("Shutdown requested")

    finally:
        await pulse.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
