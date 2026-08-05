from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import multiprocessing as mp
import os
import traceback
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import TypeAlias

from arc.foundation.service import BaseContext, Service

_CTX = mp.get_context("fork")

_ResultConn: TypeAlias = "Connection[ProcessResult, ProcessResult]"
_ControlConn: TypeAlias = Connection


class ProcessOutcome(str, Enum):
    STOPPED = "stopped"
    CRASHED = "crashed"
    CANCELLED = "cancelled"


@dataclass
class ProcessResult:
    outcome: ProcessOutcome
    error: str | None = None
    traceback: str | None = None


@dataclass(slots=True)
class ServiceStatus:
    ready: bool
    ready_reason: str | None
    healthy: bool
    healthy_reason: str | None


def resolve_service_class(module_path: str) -> type[Service]:
    module = importlib.import_module(module_path)

    for target in vars(module).values():
        if (
            inspect.isclass(target)
            and issubclass(target, Service)
            and target is not Service
        ):
            return target

    raise TypeError(
        f"No Service subclass found in '{module_path}'. "
        "Status probing requires a Service subclass."
    )


def _make_base_context(service_name: str) -> BaseContext:
    logger = logging.getLogger(service_name)
    return BaseContext(
        logger=logger,
        env=dict(os.environ),
        service_name=service_name,
        process_name=mp.current_process().name,
    )


async def _child_runtime(
    service: Service,
    control_conn: _ControlConn,
    result_conn: _ResultConn,
    ctx: BaseContext,
) -> None:
    # Keep the service alive in the child while also answering status requests.
    run_task = asyncio.create_task(service.start(ctx))

    try:
        while True:
            if control_conn.poll():
                cmd = control_conn.recv()

                if cmd == "status":
                    try:
                        ready_ok, ready_msg = await service.ready()
                    except BaseException as exc:
                        control_conn.send(
                            ServiceStatus(
                                ready=False,
                                ready_reason=f"ready() failed: {exc}",
                                healthy=False,
                                healthy_reason=None,
                            )
                        )
                        continue

                    try:
                        healthy_ok, healthy_msg = await service.healthy()
                    except BaseException as exc:
                        control_conn.send(
                            ServiceStatus(
                                ready=ready_ok,
                                ready_reason=ready_msg,
                                healthy=False,
                                healthy_reason=f"healthy() failed: {exc}",
                            )
                        )
                        continue

                    control_conn.send(
                        ServiceStatus(
                            ready=ready_ok,
                            ready_reason=ready_msg,
                            healthy=healthy_ok,
                            healthy_reason=healthy_msg,
                        )
                    )

                elif cmd == "stop":
                    await service.stop()
                    run_task.cancel()
                    break

            if run_task.done():
                await run_task
                break

            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        result_conn.send(ProcessResult(ProcessOutcome.CANCELLED))
        raise
    except BaseException as exc:
        result_conn.send(
            ProcessResult(
                ProcessOutcome.CRASHED,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
        )
        raise
    else:
        result_conn.send(ProcessResult(ProcessOutcome.STOPPED))


def _child_main(
    service_cls: type[Service],
    control_conn: _ControlConn,
    result_conn: _ResultConn,
    service_name: str,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ctx = _make_base_context(service_name)
    service = service_cls()  # pyright: ignore[reportCallIssue]

    try:
        loop.run_until_complete(_child_runtime(service, control_conn, result_conn, ctx))
    finally:
        control_conn.close()
        result_conn.close()
        loop.close()


class ServiceProcess:
    def __init__(self, name: str, service_cls: type[Service]) -> None:
        self.name = name
        self._service_cls = service_cls
        self.started_at: datetime | None = None

        self._result_conn, self._child_result_conn = _CTX.Pipe(duplex=False)
        self._control_conn, self._child_control_conn = _CTX.Pipe(duplex=True)
        self._proc: BaseProcess | None = None

    def start(self) -> None:
        self._proc = _CTX.Process(
            target=_child_main,
            args=(
                self._service_cls,
                self._child_control_conn,
                self._child_result_conn,
                self.name,
            ),
            name=self.name,
            daemon=False,
        )
        self._proc.start()
        self.started_at = datetime.now()

        # Parent keeps only its ends.
        self._child_control_conn.close()
        self._child_result_conn.close()

    def status(self, timeout: float = 1.0) -> ServiceStatus | None:
        if self._proc is None or not self._proc.is_alive():
            return None

        self._control_conn.send("status")
        if self._control_conn.poll(timeout):
            return self._control_conn.recv()
        return None

    def poll_result(self) -> ProcessResult | None:
        if self._result_conn.poll():
            return self._result_conn.recv()
        return None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def exitcode(self) -> int | None:
        return self._proc.exitcode if self._proc else None

    def terminate(self, timeout: float = 10.0) -> None:
        if self._proc is None or not self._proc.is_alive():
            return
        self._proc.terminate()
        self._proc.join(timeout)
        if self._proc.is_alive():
            self.kill()

    def kill(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join()

    def close(self) -> None:
        self._result_conn.close()
        self._control_conn.close()
