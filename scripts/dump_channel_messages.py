"""Standalone script: dumps the last N channel posts (newest to oldest) to a .txt
file, for feeding into an AI agent that learns the channel's writing style.

Run manually on demand (not part of the bot runtime). The bot must be added to
the channel as an admin. Telegram's Bot API has no way to fetch channel history,
so this drains whatever posts are sitting in the bot's pending-updates buffer and
then waits live for more until N have been captured.
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from telegram_channel_manager_bot.config import LOG_LEVEL, TELEGRAM_BOT_TOKEN
from telegram_channel_manager_bot.logging_config import setup_logging

DUMPS_DIR = Path(__file__).resolve().parent.parent / "dumps"


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.channel_post
    if message is None:
        return

    text = message.text or message.caption or ""
    if not text:
        logger.debug(f"Skipped channel post {message.message_id} with no text/caption")
        return

    context.bot_data["messages"].append(text)
    captured, target = len(context.bot_data["messages"]), context.bot_data["target"]
    logger.info(f"Captured message {captured}/{target}")
    if captured >= target:
        context.application.stop_running()


def main() -> None:
    setup_logging(LOG_LEVEL)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--count", type=int, default=50, help="Number of messages to capture (default: 50)")
    args = parser.parse_args()

    logger.info(f"Waiting for {args.count} channel posts")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.bot_data["messages"] = []
    app.bot_data["target"] = args.count
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, on_channel_post))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

    DUMPS_DIR.mkdir(exist_ok=True)
    output_file = DUMPS_DIR / f"channel_dump_{datetime.now(UTC):%Y%m%d_%H%M%S}.txt"
    messages = reversed(app.bot_data["messages"])
    output_file.write_text("\n\n".join(messages), encoding="utf-8")
    logger.success(f"Wrote {len(app.bot_data['messages'])} messages to {output_file}")


if __name__ == "__main__":
    main()
