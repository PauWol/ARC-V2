# ARC Services

An **ARC Service** is an independent capability managed by **ARC Pulse**. Each service runs in its own process and receives a `BaseContext`.

ARC supports two service styles:

* **Function-based** — simple services using `async def start(ctx)`
* **Class-based** — more complex services using `Service`

---

## Service Configuration

Services are defined in `services.arc.yaml`:

```yaml
services:
  runtime:
    module: arc.services.runtime.main
    restart: always

  kernel:
    module: arc.services.kernel.main
    restart: always
    depends:
      - runtime
```

### `module`

Points to the Python module containing the service:

```yaml
module: arc.services.runtime.main
```

This corresponds to:

```text
arc/services/runtime/main.py
```

### `restart`

Controls what Pulse does when a service process exits:

```python
RESTART_ALWAYS = "always"
RESTART_ON_FAILURE = "on-failure"
RESTART_NEVER = "never"
```

* `always` — always restart the service when it exits.
* `on-failure` — restart only if the service crashes or fails.
* `never` — do not automatically restart the service.

### `depends`

Defines startup dependencies:

```yaml
kernel:
  depends:
    - runtime
```

This means `runtime` must be started before `kernel`.

```text
runtime
   │
   ▼
kernel
```

Services on the same dependency level are started concurrently.

---

# Function-Based Service

Use this for simple services that only need a single entrypoint and do not require significant internal state.

```python
# arc/services/test/main.py

from __future__ import annotations

import asyncio

from arc.foundation.service import BaseContext


async def start(ctx: BaseContext) -> None:
    ctx.logger.info("Test service started")

    tick = 0

    try:
        while True:
            tick += 1
            ctx.logger.info("Test service tick %d", tick)
            await asyncio.sleep(2)

    except asyncio.CancelledError:
        ctx.logger.info("Test service cancelled")
        raise

    finally:
        ctx.logger.info("Test service stopped")
```

The function:

```python
async def start(ctx: BaseContext)
```

is the service's entrypoint.

ARC calls it inside the service process:

```text
ServiceProcess
      │
      ▼
start(ctx)
      │
      ▼
Service runs
```

This style is ideal for small services, workers, listeners, polling loops, and test services.

---

# Class-Based Service

Use this for services that have internal state or need a more structured lifecycle.

```python
# arc/services/runtime/main.py

from __future__ import annotations

import asyncio

from arc.foundation.service import Service


class RuntimeService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="runtime",
            version="1.0.0",
            description="ARC runtime service",
        )

        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info("Runtime service started")

        tick = 0

        while not self._stop_event.is_set():
            tick += 1
            self.ctx.logger.info("Runtime tick %d", tick)
            await asyncio.sleep(2)

    async def stop(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info("Runtime service stopping")
        self._stop_event.set()
```

The base `Service` handles context injection:

```python
async def start(self, ctx: BaseContext) -> None:
    self.ctx = ctx
    await self.run()
```

The flow is:

```text
ServiceProcess
      │
      ▼
Service.start(ctx)
      │
      ├── self.ctx = ctx
      │
      ▼
Service.run()
```

Use this style when the service needs state such as:

```python
self.model
self.worker
self.database
self._tasks
self._stop_event
```

---

# `BaseContext`

Every service receives a `BaseContext`:

```python
@dataclass(slots=True)
class BaseContext:
    logger: Logger
    env: Mapping[str, str] = field(default_factory=dict)
    service_name: str = ""
    process_name: str = ""
```

It provides common ARC infrastructure to the service.

### `ctx.logger`

The service's logger:

```python
ctx.logger.info("Runtime started")
```

Services should use this instead of setting up their own logging.

### `ctx.env`

The environment available to the service:

```python
model_path = ctx.env.get("LLM_MODEL_STORE")
```

Services do not need to load `.env` themselves.

### `ctx.service_name`

The configured ARC service name:

```python
ctx.service_name
```

For example:

```text
runtime
kernel
```

### `ctx.process_name`

The name of the process running the service, useful for diagnostics.

The context's purpose is to give every service the **common infrastructure it needs** without requiring every service to initialize it independently.

---

# Which Style Should I Use?

### Simple service

```python
async def start(ctx: BaseContext) -> None:
    ...
```

Use when the service is small and its entire behavior fits into one function.

### Complex service

```python
class RuntimeService(Service):
    async def run(self) -> None:
        ...

    async def stop(self) -> None:
        ...
```

Use when the service has state or needs a structured lifecycle.

Both styles are valid and are executed by the same `ServiceProcess` infrastructure.

---

## Current Lifecycle Status

The `Service` base class already defines functionality for:

```python
start()
run()
stop()
healthy()
ready()
```

However, **currently only the start/run path is actively used**.

`healthy()` and `ready()` are planned for future health and readiness monitoring but are **not implemented into Pulse's supervision logic yet**.

The planned distinction is:

```text
Process is running
       ≠
Service is ready
       ≠
Service is healthy
```

This will eventually allow dependencies such as:

```text
runtime
   │
   │ ready
   ▼
kernel
```

rather than starting `kernel` merely because the `runtime` process exists.
