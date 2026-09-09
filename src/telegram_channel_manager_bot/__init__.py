import asyncio
import time

from loguru import logger
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from telegram_channel_manager_bot.calculator import calculate_calories
from telegram_channel_manager_bot.config import LOG_LEVEL, TELEGRAM_BOT_TOKEN
from telegram_channel_manager_bot.formatting import (
    CALORIES_PER_PORTION_PLACEHOLDER,
    CALORIES_PLACEHOLDER,
    MACROS_PLACEHOLDER,
    format_post_stream,
)
from telegram_channel_manager_bot.logging_config import setup_logging

WAITING_FOR_DRAFT = 0
DRAFT_MIN_UPDATE_INTERVAL_SECONDS = 1.0
CALCULATION_FAILED_FALLBACK = "—"


def _apply_calculation(post: str, calculation: dict | None) -> str:
    if calculation is None:
        calories, macros, per_portion = (CALCULATION_FAILED_FALLBACK,) * 3
    else:
        calories = str(calculation["calories_total"])
        macros = calculation["macros_total"]
        per_portion = (
            str(calculation["calories_per_portion"])
            if calculation["calories_per_portion"] is not None
            else CALCULATION_FAILED_FALLBACK
        )
    return (
        post.replace(CALORIES_PLACEHOLDER, calories)
        .replace(MACROS_PLACEHOLDER, macros)
        .replace(CALORIES_PER_PORTION_PLACEHOLDER, per_portion)
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("/start from user_id={}", update.effective_user.id)
    await update.message.reply_text("Bot is up and running.")


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("/post from user_id={}", update.effective_user.id)
    await update.message.reply_text(
        "Пришлите черновой текст поста, и я оформлю его в стиле канала."
    )
    return WAITING_FOR_DRAFT


async def receive_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    draft_id = update.message.message_id
    raw_text = update.message.text
    last_sent = 0.0

    async def on_update(text: str) -> None:
        nonlocal last_sent
        now = time.monotonic()
        if now - last_sent < DRAFT_MIN_UPDATE_INTERVAL_SECONDS:
            return
        last_sent = now
        await context.bot.send_message_draft(chat_id=chat_id, draft_id=draft_id, text=text)

    await context.bot.send_message_draft(chat_id=chat_id, draft_id=draft_id, text="")

    formatted_post, calculation = await asyncio.gather(
        format_post_stream(raw_text, on_update),
        calculate_calories(raw_text),
        return_exceptions=True,
    )

    if isinstance(formatted_post, Exception):
        logger.opt(exception=formatted_post).error(
            "Failed to format draft for user_id={}", update.effective_user.id
        )
        await update.message.reply_text("Не получилось оформить пост, попробуйте ещё раз.")
        return ConversationHandler.END

    if isinstance(calculation, Exception):
        logger.opt(exception=calculation).error(
            "Failed to calculate calories for user_id={}", update.effective_user.id
        )
        calculation = None

    await update.message.reply_text(_apply_calculation(formatted_post, calculation))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("/cancel from user_id={}", update.effective_user.id)
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Запустить бота"),
            BotCommand("post", "Оформить пост"),
        ]
    )
    logger.info("Command menu registered")


def main() -> None:
    setup_logging(LOG_LEVEL)
    logger.info("Starting bot")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("post", post_command)],
            states={
                WAITING_FOR_DRAFT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_draft)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
    )
    app.run_polling()
