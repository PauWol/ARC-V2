import logging
from pathlib import Path

import yaml

from arc.foundation.constants import SERVICES_CONFIG_PATH
from arc.foundation.service import ServiceConfig, ServiceNode, ServiceTree

logger = logging.getLogger(__name__)


class ConfigNotFoundError(FileNotFoundError):
    pass


class ConfigLoader:
    def __init__(self) -> None:
        self._path: Path = SERVICES_CONFIG_PATH

        logger.debug(f"Config loader initialized with path: {self._path}")

    def locate(self):
        """
        Locates the services.arc.yaml file.
        Intended to be extendable for auto-locate feature in the future.
        """
        if not self._path.exists():
            # TODO: add auto-locate functionalities

            logger.error(f"Service configuration not found should be at: {self._path}")

            raise ConfigNotFoundError(
                "Could not find the 'services.arc.yaml' file.\nThis file is needed for system startup!"
            )

        return self._path

    def load(self) -> dict[str, ServiceConfig]:
        """Load the Service-Configurations"""
        with self.locate().open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        services: dict[str, ServiceConfig] = {}

        for name, cfg in data.get("services", {}).items():
            services[name] = ServiceConfig(
                name=name,
                module=cfg["module"],
                restart=cfg.get("restart", "never"),
                health=cfg.get("health", "ignore"),
                depends_on=cfg.get("depends", []),
            )

        return services

    @staticmethod
    def sort_to_tree(
        services: dict[str, ServiceConfig],
    ) -> ServiceTree:

        levels: ServiceTree = []

        remaining = set(services.keys())
        completed = set()  # pyright: ignore[reportUnknownVariableType]

        while remaining:
            current_level: list[ServiceNode] = []

            for name in remaining:
                service = services[name]

                if all(dep in completed for dep in service.depends_on):
                    current_level.append((name, service))

            if not current_level:
                raise RuntimeError("Circular dependency detected or missing dependency")

            levels.append(sorted(current_level, key=lambda x: x[0]))

            completed.update(name for name, _ in current_level)  # pyright: ignore[reportUnknownMemberType]

            remaining -= {name for name, _ in current_level}

        logger.debug(
            "Startup order: %s",
            [[name for name, _ in level] for level in levels],
        )

        return levels

    def run(self) -> ServiceTree:
        """Fetch the Service-Tree from the services.arc.yaml"""
        logger.info("Building service startup tree")
        services = self.load()
        service_tree = self.sort_to_tree(services)
        logger.info("Service startup tree ready")

        return service_tree
