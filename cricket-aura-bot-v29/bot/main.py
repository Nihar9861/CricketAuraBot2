"""
Entry point. Run with: python -m bot.main
"""

import logging

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

from . import config
from .lobby import (
    start_command,
    mode_callback,
    deliveries_callback,
    style_callback,
    joingame_command,
    leavegame_command,
    extend_command,
    forcestart_command,
    endgame_command,
    endgame_callback,
    restore_command,
)
from .match_engine import handle_dm_number, handle_group_number, score_command

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def dm_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the bowler's plain-number replies sent in the bot's private chat."""
    consumed = await handle_dm_number(update, context)
    if not consumed:
        # Not a recognised game move -- keep it low-key, don't spam the user.
        pass


async def group_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the batter's plain-number replies sent in the group chat."""
    consumed = await handle_group_number(update, context)
    if not consumed:
        # Ordinary group chat, or not this player's turn -- ignore silently.
        pass


def build_app() -> Application:
    if not config.BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. Copy .env.example to .env and fill in your token from @BotFather."
        )
    if not config.BOT_USERNAME:
        raise RuntimeError(
            "BOT_USERNAME is not set. Add it to your .env (without the @)."
        )

    app = Application.builder().token(config.BOT_TOKEN).build()

    # Lobby / setup commands (group + DM /start with deep-link payload)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("joingame", joingame_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("leavegame", leavegame_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("extend", extend_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("forcestart", forcestart_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("score", score_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("endgame", endgame_command, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("restore", restore_command, filters=filters.ChatType.GROUPS))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(mode_callback, pattern=r"^mode:"))
    app.add_handler(CallbackQueryHandler(deliveries_callback, pattern=r"^deliveries:"))
    app.add_handler(CallbackQueryHandler(style_callback, pattern=r"^style:"))
    app.add_handler(CallbackQueryHandler(endgame_callback, pattern=r"^endgame:"))

    # DM number input (bowling move) -- only in private chat, text only
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, dm_text_handler)
    )

    # Group number input (batting move) -- only in group chats, text only
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, group_text_handler)
    )

    return app


def main():
    app = build_app()
    logger.info("Cricket Aura bot starting (polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
