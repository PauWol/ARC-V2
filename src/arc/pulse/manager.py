from __future__ import annotations

import asyncio
from datetime import datetime

from arc.foundation.service import ServiceInstance, ServiceState
from arc.foundation.service_process import (
    ProcessOutcome,
    ServiceProcess,
    resolve_entrypoint,
)
from arc.pulse.registry import ServiceRegistry


class ServiceManager:
    """
    Owns lifecycle actions (start/stop/restart/kill) for services. Never
    creates independent state of its own -- everything it mutates lives on
    the ServiceInstance objects owned by the registry.
    """

    def __init__(self, registry: ServiceRegistry) -> None:
        self._registry: ServiceRegistry = registry

    def _clear_process(self, service: ServiceInstance) -> None:
        if service.process is not None:
            service.process.close()
        service.process = None
        service.pid = None

    async def start_all(self) -> None:
        for level in self._registry.iter_levels():
            # return_exceptions=True: one service failing to start must not
            # crash the whole startup sequence for its siblings/Pulse itself.
            _ = await asyncio.gather(
                *(self.start(service) for service in level),
                return_exceptions=True,
            )

    async def stop_all(self) -> None:
        for level in self._registry.iter_levels():
            _ = await asyncio.gather(
                *(self.stop(service) for service in level),
                return_exceptions=True,
            )

    async def start(self, service: ServiceInstance) -> None:
        service.state = ServiceState.STARTING

        try:
            entrypoint = resolve_entrypoint(service.config.module)
            process = ServiceProcess(service.config.name, entrypoint)
            process.start()

            service.process = process
            service.pid = process.pid
            service.started_at = datetime.now()
            service.state = ServiceState.RUNNING

        except Exception as exc:
            service.state = ServiceState.FAILED
            service.last_error = str(exc)
            # Deliberately not re-raised: start_all's gather(return_exceptions=True)
            # would catch it anyway, but callers invoking start() directly
            # (e.g. restart()) should also see FAILED state rather than a
            # bare exception.

    async def stop(
        self,
        service: ServiceInstance,
        timeout: float = 10.0,
    ) -> None:
        process = service.process

        if process is None:
            return

        if not process.is_alive():
            self._clear_process(service)
            return

        service.state = ServiceState.STOPPING

        # terminate() blocks (process.join under the hood), so run it off
        # the event loop thread rather than stalling other services' stop().
        await asyncio.to_thread(process.terminate, timeout)

        self._clear_process(service)
        service.state = ServiceState.STOPPED

    def kill(self, service: ServiceInstance) -> None:
        process = service.process
        if process is None:
            return

        service.state = ServiceState.STOPPING
        process.kill()
        service.state = ServiceState.STOPPED
        self._clear_process(service)

    async def restart(self, service: ServiceInstance) -> None:
        await self.stop(service)

        service.restart_count += 1

        await self.start(service)

    def check(self, service: ServiceInstance) -> ProcessOutcome | None:
        """
        Non-blocking crash/stop detection, meant to be polled from
        Pulse.supervise(). Returns the outcome if the child has exited since
        the last check, otherwise None. Updates service.state/last_error on
        a crash so the registry stays the single source of truth.
        """
        process = service.process

        if process is None:
            return None

        result = process.poll_result()
        if result is None:
            return None

        if result.outcome is ProcessOutcome.CRASHED:
            service.state = ServiceState.FAILED
            service.last_error = result.traceback or result.error

        return result.outcome

    @staticmethod
    def is_running(service: ServiceInstance) -> bool:
        return service.process is not None and service.process.is_alive()
