class Kernel:
    def __init__(
        self,
        queue: EventQueue,
        state: StateStore,
        router: ModelRouter,
        actions: ActionRegistry,
    ) -> None:
        self.queue = queue
        self.state = state
        self.router = router
        self.actions = actions
        self._last_dream_at = datetime.utcnow() - timedelta(days=1)
        self._shutdown = asyncio.Event()
        # channel name -> async fn(chat_id, text). Registered by interfaces
        # (Telegram, CLI, etc) via register_delivery_channel. This is what
        # lets _deliver actually reach the user instead of just logging.
        self._delivery_channels: dict[str, Any] = {}
