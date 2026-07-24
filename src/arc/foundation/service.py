from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import subprocess
from typing import TypeAlias


@dataclass
class ServiceConfig:
    name: str
    module: str
    restart: str = "never"
    depends_on: list[str] = field(default_factory=list)


ServiceNode: TypeAlias = tuple[str, ServiceConfig]
ServiceTree: TypeAlias = list[list[ServiceNode]]


class ServiceState(Enum):
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    version: str
    description: str = ""


#
# This is the base service contract for pulse to use for handling a service instance
#
class Service(ABC):
    _info: ServiceInfo

    def __init__(
        self,
        name: str,
        version: str,
        description: str = "",
    ) -> None:

        self._info = ServiceInfo(
            name=name,
            version=version,
            description=description,
        )

    @property
    def info(self) -> ServiceInfo:
        return self._info

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    async def healthy(self) -> tuple[bool, str | None]:
        return True, None

    async def ready(self) -> tuple[bool, str | None]:
        return True, None


@dataclass
class ServiceInstance:
    config: ServiceConfig

    service: Service | None = None

    pid: int | None = None
    process: subprocess.Popen[bytes] | None = None

    state: ServiceState = ServiceState.CREATED

    started_at: datetime | None = None

    restart_count: int = 0

    last_error: str | None = None
