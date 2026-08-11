from typing import Any

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.ext._application import Application
from telegram.ext._contexttypes import (
    DEFAULT_TYPE,  # pyright: ignore[reportAttributeAccessIssue]
)
from telegram.ext._extbot import ExtBot
from telegram.ext._jobqueue import JobQueue

from arc.foundation.constants import TELEGRAM_BOT_TOKEN


class TelegramInterface:
    def __init__(self) -> None:
        self._app: Application[
            ExtBot[None],
            DEFAULT_TYPE,
            dict[Any, Any],
            dict[Any, Any],
            dict[Any, Any],
            JobQueue[DEFAULT_TYPE],
        ] = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    async def on_receive(self):
        pass

    async def send_text(self, text: str):
        pass

    async def send_file(self, path: str):
        pass

    async def send_voice(self, text: str):  # Involves text which is then transcribed
        pass

    # Commands used in chat ex. /start

    def register_commands(self):
        """Register all the commands used in chat"""

        # Other handlers can be easily added by creating a method with param-signature -> 'update: Update, context: ContextTypes.DEFAULT_TYPE'
        # then registered by creating an handler and appending it to the handler list

        stop_handler = CommandHandler("stop", self.stop)

        handler_list = [stop_handler]
        self._app.add_handlers(handler_list)  # pyright: ignore[reportUnknownMemberType]

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Stop a running agent if active

        :return: whether a agent was stopped or not
        """
