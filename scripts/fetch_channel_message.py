"""Standalone script: fetches specific channel messages by their t.me link and
dumps their text to a .txt file.

Run manually on demand (not part of the bot runtime). Works by forwarding each
message to a private chat with the bot owner (TELEGRAM_OWNER_CHAT_ID) and
reading its text back — Telegram's Bot API has no direct "get message" call.
"""

import argparse
import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from telegram import Bot

from telegram_channel_manager_bot.config import (
    LOG_LEVEL,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_OWNER_CHAT_ID,
)
from telegram_channel_manager_bot.logging_config import setup_logging

DUMPS_DIR = Path(__file__).resolve().parent.parent / "dumps"
LINK_RE = re.compile(r"t\.me/(?P<username>\w+)/(?P<message_id>\d+)")


def parse_link(link: str) -> tuple[str, int]:
    match = LINK_RE.search(link)
    if not match:
        raise ValueError(f"Could not parse Telegram message link: {link}")
    return f"@{match['username']}", int(match["message_id"])


async def fetch_message_text(bot: Bot, channel: str, message_id: int) -> str:
    forwarded = await bot.forward_message(
        chat_id=TELEGRAM_OWNER_CHAT_ID, from_chat_id=channel, message_id=message_id
    )
    return forwarded.text or forwarded.caption or ""


async def fetch_all(links: list[str]) -> list[str]:
    bot = Bot(TELEGRAM_BOT_TOKEN)
    texts = []
    async with bot:
        for link in links:
            try:
                channel, message_id = parse_link(link)
                text = await fetch_message_text(bot, channel, message_id)
            except Exception:  # noqa: BLE001 - one bad link shouldn't abort the whole batch
                logger.exception("Failed to fetch {}, skipping", link)
                continue
            logger.info("Fetched {} ({} chars)", link, len(text))
            texts.append(text)
    return texts


def main() -> None:
    setup_logging(LOG_LEVEL)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "links", nargs="+", help="Telegram message link(s), e.g. https://t.me/channel/85"
    )
    args = parser.parse_args()

    texts = asyncio.run(fetch_all(args.links))

    DUMPS_DIR.mkdir(exist_ok=True)
    output_file = DUMPS_DIR / f"fetched_{datetime.now(UTC):%Y%m%d_%H%M%S}.txt"
    output_file.write_text("\n\n".join(texts), encoding="utf-8")
    logger.success("Wrote {} messages to {}", len(texts), output_file)


if __name__ == "__main__":
    main()
