import asyncio
import logging

from arc.foundation.service import ServiceState
from arc.foundation.service_process import ProcessOutcome
from arc.pulse.configLoader import ConfigLoader
from arc.pulse.manager import ServiceManager
from arc.pulse.registry import ServiceRegistry

logger = logging.getLogger(__name__)

# ServiceConfig.restart values this supervisor understands.
RESTART_ALWAYS = "always"
RESTART_ON_FAILURE = "on-failure"
RESTART_NEVER = "never"


class Pulse:
    def __init__(self) -> None:
        self._service_registry: ServiceRegistry = ServiceRegistry()
        self._service_manager: ServiceManager = ServiceManager(self._service_registry)

    async def startup(self, wait_on_deps: bool = True) -> None:
        """Start the log relay, build the service registry, and start all services."""
        # Must start before any service is forked -- the queue it owns is
        # what gets inherited at fork time.

        tree = ConfigLoader().run()
        self._service_registry.register_tree(tree)

        await self._service_manager.start_all(wait_on_deps)

    async def supervise(self, poll_interval: float = 2.0) -> None:
        """
        Continuously watch running services and apply each service's
        restart policy when one exits. Never raises on a single service's
        behalf -- a crash is handled (logged, possibly restarted), not
        propagated.
        """
        while True:
            for service in self._service_registry.iter_startup_order():
                if service.state not in (ServiceState.RUNNING, ServiceState.FAILED):
                    continue

                outcome = self._service_manager.check(service)
                if outcome is None:
                    continue  # still running

                logger.info(
                    "Service '%s' exited: %s", service.config.name, outcome.value
                )

                if self._should_restart(service.config.restart, outcome):
                    logger.info("Restarting service '%s'", service.config.name)
                    await self._service_manager.restart(service)

            await asyncio.sleep(poll_interval)

    @staticmethod
    def _should_restart(policy: str, outcome: ProcessOutcome) -> bool:
        if policy == RESTART_ALWAYS:
            return True
        if policy == RESTART_ON_FAILURE:
            return outcome is ProcessOutcome.CRASHED
        return False  # "never" or anything unrecognized

    async def shutdown(self) -> None:
        """Stop all managed services, then the log relay."""
        await self._service_manager.stop_all()


# Todo: Make supervise use the heathy pipeline to check and log
