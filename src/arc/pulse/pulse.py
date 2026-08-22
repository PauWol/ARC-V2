import asyncio
import logging
from collections import defaultdict

from arc.foundation.constants import MAX_UNHEALTHY_SERVICE_CHECKS, WAIT_ON_DEPENDENCIES
from arc.foundation.service import ServiceInstance, ServiceState
from arc.foundation.service_process import ProcessOutcome, ServiceStatus
from arc.pulse.configLoader import ConfigLoader
from arc.pulse.manager import ServiceManager
from arc.pulse.registry import ServiceRegistry

logger = logging.getLogger(__name__)

# ServiceConfig.restart values this supervisor understands.
RESTART_ALWAYS = "always"
RESTART_ON_FAILURE = "on-failure"
RESTART_NEVER = "never"

HEALTH_ACTION_IGNORE = "ignore"
HEALTH_ACTION_RESTART = "restart"
HEALTH_ACTION_STOP = "stop"


class Pulse:
    def __init__(self) -> None:
        self._service_registry: ServiceRegistry = ServiceRegistry()
        self._service_manager: ServiceManager = ServiceManager(self._service_registry)

        self._service_health_counter: defaultdict[int, int] = defaultdict(int)

    async def startup(self, wait_on_deps: bool = WAIT_ON_DEPENDENCIES) -> None:
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
                # if its not running or failed it doesn't have any info
                if service.state not in (ServiceState.RUNNING, ServiceState.FAILED):
                    continue

                # check if failed before check
                outcome = self._service_manager.check(service)
                if outcome:
                    logger.info(
                        "Service '%s' exited: %s", service.config.name, outcome.value
                    )

                    if self._should_restart(service.config.restart, outcome):
                        logger.info("Restarting service '%s'", service.config.name)
                        await self._service_manager.restart(service)

                # Perform an internal service health check.
                if service.pid is None or service.process is None:
                    continue

                _state = service.process.status()
                _pid = service.pid

                if _state is None:
                    logger.warning(
                        f"Could not determine health for service {service.config.name}"
                    )
                    continue

                # if unhealthy
                if _state.healthy:
                    self._service_health_counter[_pid] = 0
                else:
                    self._service_health_counter[_pid] += 1

                _ma = MAX_UNHEALTHY_SERVICE_CHECKS

                if self._service_health_counter[_pid] >= _ma:
                    self._service_health_counter[_pid] = 0
                    logger.info(
                        f"Service {service.config.name} was unhealthy for {_ma} times"
                    )
                    logger.warning(
                        f"Service {service.config.name} reports unhealthy: {_state.healthy_reason}"
                    )
                    # Restart, Stop or Ignore depending on policy
                    if self._should_restart_health(service.config.health, _state):
                        logger.info("Restarting service '%s'", service.config.name)
                        await self._service_manager.restart(service)
                    elif service.config.health == HEALTH_ACTION_STOP:
                        logger.info("Stopping service '%s'", service.config.name)
                        await self._service_manager.stop(service)

            await asyncio.sleep(poll_interval)

    @staticmethod
    def _should_restart(policy: str, outcome: ProcessOutcome) -> bool:
        if policy == RESTART_ALWAYS:
            return True
        if policy == RESTART_ON_FAILURE:
            return outcome is ProcessOutcome.CRASHED
        return False  # "never" or anything unrecognized

    @staticmethod
    def _should_restart_health(policy: str, outcome: ServiceStatus):
        if policy == HEALTH_ACTION_IGNORE:
            return False
        if policy == HEALTH_ACTION_RESTART:
            return not outcome.healthy
        return False

    async def shutdown(self) -> None:
        """Stop all managed services, then the log relay."""
        await self._service_manager.stop_all()
