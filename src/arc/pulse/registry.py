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
        for _level in tree:
            _tl: list[str] = []
            for name, config in _level:
                self.add(ServiceInstance.from_config_registry(config))

                _tl.append(name)

            self._dependency_tree.append(_tl)

    def iter_startup_order(self):
        for level in self._dependency_tree:
            for name in level:
                yield self.get(name)

    def iter_levels(self):
        for level in self._dependency_tree:
            yield [self.get(name) for name in level]
