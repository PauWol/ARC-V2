# Building an ARC Service

An ARC service is an independent long-running capability managed by **ARC Pulse**.

Every service runs in its own process and receives a `BaseContext` when it starts. The context gives the service access to the environment and infrastructure it needs without forcing every service to initialize those things itself.

There are two supported ways to implement a service:

1. **Simple function service** — `async def start(ctx)`
2. **Class-based service** — inherit from `Service`

Both are valid. Choose the simplest one that fits the service.

---

## 1. The `BaseContext`

Every service receives a `BaseContext` when it starts:

```python
from arc.foundation.service import BaseContext
```

The context currently contains:

```python
@dataclass(slots=True)
class BaseContext:
    logger: Logger
    env: Mapping[str, str] = field(default_factory=dict)
    service_name: str = ""
    process_name: str = ""
```

The context is created by ARC's service process infrastructure and injected into the service when it starts.

Conceptually:

```text
ARC Pulse
    │
    │ starts service
    ▼
ServiceProcess
    │
    │ forks
    ▼
Service Process
    │
    │ creates
    ▼
BaseContext
    │
    ├── logger
    ├── env
    ├── service_name
    └── process_name
    │
    ▼
Your Service
```

The purpose of `BaseContext` is to provide **common runtime infrastructure** to every service.

For example:

### `ctx.logger`

Use this for service logging:

```python
ctx.logger.info("Runtime started")
ctx.logger.warning("Model is taking longer than expected")
ctx.logger.error("Failed to load model")
```

You should generally use `ctx.logger` instead of creating your own logger in every service.

This ensures that service logs go through ARC's centralized logging architecture.

---

### `ctx.env`

Contains the environment available to the service:

```python
model_path = ctx.env.get("LLM_MODEL_STORE")
```

For example:

```python
api_key = ctx.env.get("API_KEY")
```

This allows services to access configuration without each service independently loading `.env`.

ARC loads the environment once during boot, and the service receives the environment through its context.

---

### `ctx.service_name`

The name ARC assigned to the running service:

```python
ctx.logger.info(
    "Starting service: %s",
    ctx.service_name,
)
```

For example:

```text
runtime
kernel
vision
voice
```

This is useful when writing generic service code that needs to know its own identity.

---

### `ctx.process_name`

The operating-system process name:

```python
ctx.logger.info(
    "Running in process %s",
    ctx.process_name,
)
```

This can be useful for diagnostics and debugging.

---

# 2. Simple Service: `start(ctx)`

For a small or straightforward service, the simplest option is to expose:

```python
async def start(ctx: BaseContext) -> None:
```

Example:

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

            ctx.logger.info(
                "Test service tick %d",
                tick,
            )

            await asyncio.sleep(2)

    except asyncio.CancelledError:
        ctx.logger.info("Test service cancelled")
        raise

    finally:
        ctx.logger.info("Test service stopped")
```

This is ideal for simple services that only need to run one main loop.

The lifecycle is essentially:

```text
Pulse
  │
  ▼
fork process
  │
  ▼
create BaseContext
  │
  ▼
start(ctx)
  │
  ▼
service runs
  │
  ▼
function returns
  │
  ▼
process exits
```

The `start()` function is the service's entrypoint.

As long as the function does not return, the service continues running.

For example:

```python
async def start(ctx: BaseContext) -> None:
    while True:
        ...
```

If `start()` returns:

```python
async def start(ctx: BaseContext) -> None:
    ctx.logger.info("Done")
```

the service process will finish.

This style is therefore good for services where the entire lifecycle can be represented by one coroutine.

---

# 3. Class-Based Service

For more complex services, use the `Service` base class:

```python
from arc.foundation.service import Service
```

A class-based service looks like this:

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

        self.ctx.logger.info(
            "Runtime service started"
        )

        tick = 0

        while not self._stop_event.is_set():
            tick += 1

            self.ctx.logger.info(
                "Runtime tick %d",
                tick,
            )

            await asyncio.sleep(2)

        self.ctx.logger.info(
            "Runtime service run loop ended"
        )

    async def stop(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info(
            "Runtime service stopping"
        )

        self._stop_event.set()
```

The important part is:

```python
class RuntimeService(Service):
```

This tells ARC that the service follows the standard ARC service lifecycle.

The base class provides:

```python
async def start(self, ctx: BaseContext) -> None:
    self.ctx = ctx
    await self.run()
```

So ARC calls:

```python
await service.start(ctx)
```

The base class stores the context:

```python
self.ctx = ctx
```

and then invokes:

```python
await self.run()
```

The service can therefore access its context anywhere in the class:

```python
self.ctx.logger.info(...)
```

---

# 4. `run()` vs `start()`

This is an important distinction.

For a simple service:

```python
async def start(ctx: BaseContext) -> None:
```

`start()` is the **entrypoint**.

For a class-based service:

```python
async def run(self) -> None:
```

`run()` is the **main service loop**.

The `Service` base class owns the `start()` method:

```text
ARC
 │
 │ start(ctx)
 ▼
Service.start(ctx)
 │
 ├── self.ctx = ctx
 │
 ▼
Service.run()
 │
 ▼
Your service logic
```

This allows the base class to perform common initialization before the actual service starts.

For example, in the future, `Service.start()` could handle:

```python
async def start(self, ctx: BaseContext) -> None:
    self.ctx = ctx

    await self.initialize()

    await self.run()
```

without every service having to implement that logic itself.

---

# 5. When Should I Use Which?

Use a **simple `start(ctx)` function** when the service is small and has no meaningful internal state.

For example:

```python
async def start(ctx: BaseContext) -> None:
    ctx.logger.info("Metrics service started")

    while True:
        collect_metrics()
        await asyncio.sleep(10)
```

Good examples:

* simple monitoring service
* small background worker
* temporary test service
* simple event listener
* basic polling service

Use a **`Service` class** when the service has meaningful state or lifecycle behavior.

For example:

```python
class RuntimeService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="runtime",
            version="1.0.0",
        )

        self.model = None
        self._stop_event = asyncio.Event()
```

This is better for services such as:

* Runtime
* Kernel
* Vision
* Voice
* Desktop
* complex plugin managers
* database-backed services
* services with multiple background tasks
* services with initialization and cleanup requirements

A class gives the service somewhere to keep its state:

```python
self.model
self.connection
self.worker
self._stop_event
self._tasks
```

instead of putting everything into local variables inside `start()`.

---

# 6. The `Service` Lifecycle

A class-based service follows this general model:

```text
                  ARC Pulse
                      │
                      │ create process
                      ▼
               ServiceProcess
                      │
                      │ fork
                      ▼
               Child Process
                      │
                      │ create context
                      ▼
                BaseContext
                      │
                      │ inject
                      ▼
             Service.start(ctx)
                      │
                      ├── self.ctx = ctx
                      │
                      ▼
                 Service.run()
                      │
                      │
                      ▼
                Running Service
                      │
                      │ stop requested
                      ▼
                Service.stop()
                      │
                      ▼
               Service stopped
```

The important thing is that **Pulse owns the process lifecycle**, while the service owns its **internal lifecycle**.

Pulse decides:

```text
START
STOP
RESTART
KILL
```

The service decides:

```text
initialize internal resources
run
handle internal state
cleanup resources
```

This separation is important for ARC.

---

# 7. Health and Readiness

Class-based services can also implement:

```python
async def healthy(self) -> tuple[bool, str | None]:
    ...
```

and:

```python
async def ready(self) -> tuple[bool, str | None]:
    ...
```

For example:

```python
class RuntimeService(Service):

    async def ready(self) -> tuple[bool, str | None]:
        if self.model is None:
            return False, "Model has not been loaded"

        return True, None
```

This allows ARC to distinguish between:

```text
Process exists
        ≠
Service is ready
        ≠
Service is healthy
```

For example:

```text
STARTING
    │
    ▼
Process exists
    │
    ▼
Loading model
    │
    ▼
READY
    │
    ▼
RUNNING
    │
    ▼
HEALTH CHECK
    │
    ├── healthy
    │
    └── unhealthy
```

This becomes particularly important for your `runtime -> kernel` dependency.

The Kernel may depend on Runtime, but simply having the Runtime process alive does not necessarily mean the Runtime is ready to accept inference requests.

---

# Recommended Rule for ARC

I would define the rule as:

> **Every ARC service must expose either an asynchronous `start(ctx: BaseContext)` function or a subclass of `Service`.**
>
> Simple services should use `start(ctx)`. Complex services should use the `Service` class.
>
> Regardless of implementation style, ARC normalizes both into the same internal service entrypoint and injects a `BaseContext` before execution.

That gives you a very simple developer experience:

```text
Simple Service
──────────────
async def start(ctx):
    ...
```

or:

```text
Complex Service
───────────────
class MyService(Service):
    async def run(self):
        ...

    async def stop(self):
        ...
```

while ARC itself only needs to deal with one normalized execution model:

```text
                 Service Definition
                         │
               ┌─────────┴─────────┐
               │                   │
        start(ctx)             Service class
               │                   │
               └─────────┬─────────┘
                         │
                         ▼
                  BaseContext
                         │
                         ▼
                  ServiceProcess
                         │
                         ▼
                     fork()
                         │
                         ▼
                   Child Process
                         │
                         ▼
                  Service Running
```

This is the architecture I would use for ARC: **simple services stay simple, complex services get the full lifecycle abstraction, and Pulse remains completely agnostic about which implementation style a service uses.**
