import subprocess
import sys
import asyncio
from datetime import datetime

from arc.foundation.service import ServiceInstance, ServiceState
from arc.pulse.registry import ServiceRegistry


class ServiceManager:
    def __init__(self, registry: ServiceRegistry) -> None:
        self._registry: ServiceRegistry = registry

    async def start_all(self) -> None:
        for level in self._registry.iter_levels():
            _ = await asyncio.gather(*(self.start(service) for service in level))

    async def start(self, service: ServiceInstance) -> None:
        service.state = ServiceState.STARTING

        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    service.config.module,
                ]
            )

            service.process = process
            service.pid = process.pid
            service.started_at = datetime.now()
            service.state = ServiceState.RUNNING

        except Exception as exc:
            service.state = ServiceState.FAILED
            service.last_error = str(exc)
            raise

    def stop(self, service: ServiceInstance) -> None:
        process = service.process

        if process is None:
            return

        if process.poll() is not None:
            return

        service.state = ServiceState.STOPPING

        process.terminate()
        _ = process.wait()

        service.state = ServiceState.NONE
        service.process = None
        service.pid = None

    def kill(self, service: ServiceInstance) -> None:
        process = service.process

        if process is None:
            return

        if process.poll() is not None:
            return

        service.state = ServiceState.STOPPING

        process.kill()
        _ = process.wait()

        service.state = ServiceState.NONE
        service.process = None
        service.pid = None

    async def restart(self, service: ServiceInstance) -> None:
        self.stop(service)

        service.restart_count += 1

        await self.start(service)

    @staticmethod
    def is_running(service: ServiceInstance) -> bool:
        process = service.process

        if process is None:
            return False

        return process.poll() is None
