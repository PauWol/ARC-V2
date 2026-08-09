# ARC Services

An **ARC Service** is an independent, long-running process managed by **ARC Pulse**.

Each service runs in its own isolated operating system process and is supervised by Pulse throughout its lifecycle. Services may depend on one another, allowing Pulse to start the system in dependency order and wait until required services become **ready** before starting dependent services.

---

> [!IMPORTANT]
> **Function-based services are no longer supported.**
>
> ARC previously supported services implemented as `async def start(ctx)`. This API has been removed.
>
> Every service must inherit from `Service`. This allows Pulse to monitor service readiness and health, supervise failures, and coordinate dependency startup.

---

# Service Configuration

Services are registered in `services.arc.yaml`.

```yaml
services:
  runtime:
    module: arc.services.runtime.main
    restart: on-failure

  kernel:
    module: arc.services.kernel.main
    restart: always
    depends:
      - runtime
```

## `module`

The Python module containing the service implementation.

```yaml
module: arc.services.runtime.main
```

which corresponds to

```text
arc/services/runtime/main.py
```

Pulse automatically discovers the `Service` subclass inside the module.

---

## `restart`

Determines how Pulse responds when a service process exits.

```yaml
restart: always
```

Available policies:

| Value        | Description                            |
| ------------ | -------------------------------------- |
| `always`     | Restart whenever the service exits.    |
| `on-failure` | Restart only after a crash or failure. |
| `never`      | Never restart automatically.           |

---

## `depends`

Defines service startup dependencies.

```yaml
kernel:
  depends:
    - runtime
```

Pulse guarantees that:

* dependencies are started first
* dependencies become **ready**
* only then are dependent services started

Services on the same dependency level are started concurrently.

---

# Service Lifecycle

Every service follows the same lifecycle.

```text
Process Created
       │
       ▼
start(ctx)
       │
       ▼
run()
       │
       ├────────► ready()
       │
       ├────────► healthy()
       │
       ▼
stop()
```

The framework provides `start()` automatically. Service implementations only define the remaining lifecycle methods.

| Method      | Purpose                                             |
| ----------- | --------------------------------------------------- |
| `run()`     | Main service execution.                             |
| `ready()`   | Reports whether initialization has completed.       |
| `healthy()` | Reports whether the service is operating correctly. |
| `stop()`    | Performs a graceful shutdown.                       |

---

# Creating a Service

Every ARC service must inherit from `Service`.

```python
from arc.foundation.service import Service

class MyService(Service):
    ...
```

The base class injects a `BaseContext` before `run()` is executed.

```python
async def start(self, ctx: BaseContext) -> None:
    self.ctx = ctx
    await self.run()
```

`start()` should never be overridden.

---

# BaseContext

Every service receives a shared execution context.

```python
@dataclass(slots=True)
class BaseContext:
    logger: Logger
    env: Mapping[str, str]
    service_name: str
    process_name: str
```

## `ctx.logger`

Service-specific logger managed by Pulse.

```python
self.ctx.logger.info("Runtime started")
```

Always use this logger instead of creating your own.

---

## `ctx.env`

Environment variables inherited from Pulse.

```python
model_path = self.ctx.env["LLM_MODEL_STORE"]
```

Services do not need to load `.env` files themselves.

---

## `ctx.service_name`

Configured service name.

Example:

```text
runtime
kernel
vision
```

---

## `ctx.process_name`

Operating system process name assigned by Pulse.

Useful for diagnostics and debugging.

---

# Readiness & Health

Pulse distinguishes between a running process and an operational service.

A process can exist while still initializing, and a running service can later become unhealthy.

## `ready()`

`ready()` reports whether the service has completed initialization.

Pulse waits until every dependency reports readiness before starting dependent services.

Typical readiness conditions include:

* model finished loading
* HTTP server listening
* database connected
* worker pool initialized
* caches populated

Example:

```python
async def ready(self) -> tuple[bool, str | None]:
    if self._ready:
        return True, None

    return False, "still starting"
```

---

## `healthy()`

`healthy()` reports whether the service is functioning correctly.

Unlike `ready()`, this represents runtime health rather than startup progress.

Typical health failures include:

* disconnected database
* failed worker thread
* unloaded model
* unrecoverable internal error

Example:

```python
async def healthy(self) -> tuple[bool, str | None]:
    if self._stop_event.is_set():
        return False, "service is stopping"

    return True, None
```

---

# Complete Example

```python
from __future__ import annotations

import asyncio

from arc.foundation.service import Service


class TestService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="test",
            version="0.1.0",
            description="A simple Pulse test service",
        )

        self._stop_event = asyncio.Event()
        self._ready = False
        self._ticks = 0

    async def run(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info("Test service started")

        # Simulate startup work.
        await asyncio.sleep(1)

        self._ready = True
        self.ctx.logger.info("Test service ready")

        try:
            while not self._stop_event.is_set():
                self._ticks += 1
                self.ctx.logger.info("Tick %d", self._ticks)
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            raise

        finally:
            self.ctx.logger.info("Test service stopped")

    async def stop(self) -> None:
        self._stop_event.set()

    async def ready(self) -> tuple[bool, str | None]:
        if self._ready:
            return True, None

        return False, "still starting"

    async def healthy(self) -> tuple[bool, str | None]:
        if self._stop_event.is_set():
            return False, "service is stopping"

        return True, None
```

---

# Best Practices

* Inherit from `Service` for every service implementation.
* Keep `run()` alive until shutdown.
* Store long-lived resources as instance attributes.
* Use `ready()` only to report startup completion.
* Use `healthy()` only to report runtime health.
* Keep `ready()` and `healthy()` lightweight; Pulse may poll them frequently.
* Use `ctx.logger` for all logging.
* Release resources gracefully in `stop()`.
* Avoid blocking the event loop during normal operation.
* Design services to be independent and restartable.
