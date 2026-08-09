"""
Telegram as an interface into the kernel — not a special case, just
another source pushing WakeEvents plus a registered delivery channel.

Inbound: every message from your chat_id becomes a USER_LIVE event
(top priority, skip_triage=True — a message you typed is never filtered).
Messages from OTHER chat_ids are dropped by default (see _authorized) —
Arc is not meant to respond to strangers who find the bot.

Outbound: register_delivery_channel("telegram", self.send) lets the
kernel's _deliver() reach back into this same chat, including for
autonomous wakeups (cron/random/dream) via CONFIG.telegram.primary_chat_id.
"""

from __future__ import annotations
import logging

from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

from arc.services.kernel.config import CONFIG
from arc.services.kernel.events import EventQueue, Priority

log = logging.getLogger("arc.telegram")


class TelegramInterface:
    def __init__(self, queue: EventQueue) -> None:
        self.queue = queue
        self.app = Application.builder().token(CONFIG.telegram.bot_token).build()
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

    def _authorized(self, chat_id: int) -> bool:
        # single-user assistant by default — only your configured chat_id
        # can wake it. Extend to a set of ids if you want multi-user later.
        return chat_id == CONFIG.telegram.primary_chat_id

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        text = update.message.text

        if not self._authorized(chat_id):
            log.warning("Ignored message from unauthorized chat_id=%s", chat_id)
            return

        log.info("Telegram inbound from %s: %s", chat_id, text[:100])
        await self.queue.push(
            priority=Priority.USER_LIVE,
            reason="telegram_message",
            payload={"message": text, "channel": "telegram", "chat_id": chat_id},
            skip_triage=True,
        )

    async def send(self, chat_id, text: str) -> None:
        """Registered as the 'telegram' delivery channel — the kernel
        calls this from _deliver()."""
        await self.app.bot.send_message(chat_id=chat_id, text=text)

    async def start(self) -> None:
        """Runs the bot's polling loop. Call as an asyncio task alongside
        the kernel — this method does not return until stopped."""
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        log.info("Telegram interface polling started")

    async def stop(self) -> None:
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
