import uvicorn

from arc.foundation.constants import ARC_RUNTIME_PORT
from arc.foundation.service import Service
from arc.services.runtime.api import app
from arc.services.runtime.engine.config import EngineConfig
from arc.services.runtime.engine.engine import LlamaEngine


class RuntimeService(Service):
    def __init__(self) -> None:
        super().__init__(
            name="runtime",
            version="1.0.0",
            description="ARC LLM inference runtime",
        )

        self._server: uvicorn.Server | None = None
        self._engine: LlamaEngine | None = None

    async def run(self) -> None:
        assert self.ctx is not None

        self.ctx.logger.info("Loading runtime...")

        config = EngineConfig.from_env()
        self.ctx.logger.debug(f"Engine-config: {config}")
        self.ctx.logger.info("Config ready")

        self._engine = LlamaEngine(config)
        await self._engine.load()
        self.ctx.logger.info("Engine Ready")

        app.state.engine = self._engine

        self.ctx.logger.info(
            f"Model loaded: {self._engine.model_name} (n_gpu_layers={config.n_gpu_layers}, n_ctx={config.n_ctx})"
        )

        self.ctx.logger.info("Runtime ready")

        server_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=ARC_RUNTIME_PORT,
            log_level="info",
        )

        self._server = uvicorn.Server(server_config)

        await self._server.serve()

    async def ready(self):
        ok = (
            self._engine is not None
            and self._server is not None
            and self._server.started
        )
        return ok, None if ok else "runtime not ready yet"

    async def healthy(self):
        # server actually started and is alive
        if self._server is None:
            return False, "server not created"
        return (
            self._server.started,
            None if self._server.started else "server not started yet",
        )

    async def stop(self) -> None:

        if self._engine is not None:
            await self._engine.unload()

        if self._server:
            self._server.should_exit = True
