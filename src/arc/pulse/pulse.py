from arc.pulse.configLoader import ConfigLoader
from arc.pulse.manager import ServiceManager
from arc.pulse.registry import ServiceRegistry


class Pulse:
    def __init__(self) -> None:
        self._service_registry: ServiceRegistry = ServiceRegistry()
        self._service_manager: ServiceManager = ServiceManager(self._service_registry)

    async def startup(self):
        _tree = ConfigLoader().run()

        self._service_registry.register_tree(_tree)

        await self._service_manager.start_all()

        # TODO: Implement Manager to start the service levels -> find out how to manage state singleton etc maybe pass registry instance
