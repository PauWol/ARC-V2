"""
Fork-based process wrapper for ARC services.

Why fork instead of subprocess.Popen:
    A forked child is created via copy-on-write cloning of Pulse's own
    memory image, not a fresh interpreter. It inherits every already-imported
    module (all of `arc.*`, third-party deps), the parsed `.env` values, and
    Pulse's logging configuration -- without re-importing or re-parsing any
    of it. It's still a real, independent OS process: a crash or unhandled
    exception in the child cannot take down Pulse.

Platform note:
    This module requires `os.fork()` and is therefore POSIX-only
    (Linux/macOS). It will raise at import/context-creation time on Windows.

The two things fork does NOT give you for free, and how this module handles
them:
    1. Open file descriptors get duplicated into the child (e.g. a
       RotatingFileHandler's file handle). Two processes independently
       rotating the same log file corrupts it. -> Children never keep
       inherited handlers; see `_rewire_logging` and `log_relay.py`.
    2. Forking mid-event-loop can hand the child a half-cloned event loop
       (inherited fds, pending callbacks that belong to the parent). ->
       Every child discards whatever loop existed at fork time and starts
       a brand new one in `_child_main`.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import multiprocessing as mp
import os
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import TypeAlias

from arc.foundation.service import BaseContext, Service

# All ServiceProcess instances share one "fork" context. Using a named
# context (rather than the bare `multiprocessing` module) makes the start
# method explicit and avoids ever silently falling back to "spawn"/"forkserver"
# on a platform where the default differs.
_CTX = mp.get_context("fork")

Entrypoint: TypeAlias = Callable[[BaseContext], Awaitable[None]]

# Result pipe: children only ever send a ProcessResult, parents only ever
# receive one -- typed explicitly rather than left as Connection[Any, Any].
_ResultConn: TypeAlias = "Connection[ProcessResult, ProcessResult]"


class ProcessOutcome(str, Enum):
    """How a child's run ended, reported back over the result pipe."""

    STOPPED = "stopped"  # entrypoint's run() coroutine returned normally
    CRASHED = "crashed"  # entrypoint raised an exception
    CANCELLED = "cancelled"  # entrypoint was cancelled (graceful stop)


@dataclass
class ProcessResult:
    outcome: ProcessOutcome
    error: str | None = None
    traceback: str | None = None


def resolve_entrypoint(
    module_path: str,
    attr: str | None = None,
) -> Entrypoint:
    module = importlib.import_module(module_path)

    # Explicitly requested symbol
    if attr is not None:
        target = getattr(module, attr)

        if inspect.iscoroutinefunction(target):
            return target

        if inspect.isclass(target) and issubclass(target, Service):
            return _wrap_service_class(target)

    # Automatic module-level start()
    start = getattr(module, "start", None)

    if inspect.iscoroutinefunction(start):
        return start

    # Automatic Service subclass discovery
    for target in vars(module).values():
        if (
            inspect.isclass(target)
            and issubclass(target, Service)
            and target is not Service
        ):
            return _wrap_service_class(target)

    raise TypeError(
        f"No valid service entrypoint found in '{module_path}'. "
        "Expected async start(ctx) or a Service subclass."
    )


def _wrap_service_class(
    service_cls: type[Service],
) -> Entrypoint:

    async def run_service(ctx: BaseContext) -> None:
        service = service_cls()  # pyright: ignore[reportCallIssue]
        await service.start(ctx)

    return run_service


def _make_base_context(service_name: str) -> BaseContext:
    logger = logging.getLogger(service_name)
    return BaseContext(
        logger=logger,
        env=dict(os.environ),
        service_name=service_name,
        process_name=mp.current_process().name,
    )


def _child_main(
    entrypoint: Entrypoint, result_conn: _ResultConn, service_name: str
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ctx = _make_base_context(service_name)

    try:
        loop.run_until_complete(entrypoint(ctx))
    except asyncio.CancelledError:
        result_conn.send(ProcessResult(ProcessOutcome.CANCELLED))
    except BaseException as exc:
        result_conn.send(
            ProcessResult(
                ProcessOutcome.CRASHED,
                error=str(exc),
                traceback=traceback.format_exc(),
            )
        )
    else:
        result_conn.send(ProcessResult(ProcessOutcome.STOPPED))
    finally:
        result_conn.close()
        loop.close()


class ServiceProcess:
    """
    A single crash-isolated child process running one service's entrypoint.

    Lifecycle:
        start()  -> spawn the forked child
        is_alive() / poll_result() -> polled by the caller (Pulse.supervise)
        terminate() / kill() -> stop it
        close() -> release pipe file descriptors once fully done

    This class only knows how to run *one* entrypoint once. Restart policy,
    retry counting, and "what to do when it dies" all live one layer up in
    ServiceManager -- this class's job is exactly: spawn, report outcome,
    stop. Keeping that boundary is what makes it easy to extend later (e.g.
    swapping the transport ServiceProcess uses for results/logs without
    touching ServiceManager or Pulse at all).
    """

    def __init__(self, name: str, entrypoint: Entrypoint) -> None:
        self.name: str = name
        self._entrypoint: Entrypoint = entrypoint
        self.started_at: datetime | None = None

        self._result_conn: _ResultConn
        self._child_result_conn: _ResultConn
        self._result_conn, self._child_result_conn = _CTX.Pipe(duplex=False)  # pyright: ignore[reportAttributeAccessIssue]
        self._proc: BaseProcess | None = None

    def start(self) -> None:
        self._proc = _CTX.Process(
            target=_child_main,
            args=(self._entrypoint, self._child_result_conn, self.name),
            name=self.name,
            daemon=False,
        )
        self._proc.start()
        self.started_at = datetime.now()

    def poll_result(self) -> ProcessResult | None:
        """Non-blocking check for a stop/crash report from the child."""
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
        """Ask the child to stop; escalate to kill() if it misses the deadline."""
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
