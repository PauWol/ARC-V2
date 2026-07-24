from arc.foundation.service import ServiceInstance, ServiceTree


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, ServiceInstance] = {}
        self._dependency_tree: list[list[str]] = []

    def add(self, service: ServiceInstance):
        self._services[service.config.name] = service

    def get(self, name: str):
        if name not in self._services:
            raise KeyError(f"Unknown service: {name}")
        return self._services[name]

    def register_tree(self, tree: ServiceTree):
        pass
        # TODO: Implement the make dependency tree list and the dict[str,ServiceInstance] list from tree and then some helpers
