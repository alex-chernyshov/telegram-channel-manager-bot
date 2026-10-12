import html
import json
from collections.abc import Mapping
from typing import Any

from loguru import logger
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from telegram_channel_manager_bot.config import FAQ_DB_PATH, TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_CHANNEL_ID
from telegram_channel_manager_bot.faq_agents import (
    ROOT_DIR,
    SUPPORTED_LANGUAGES,
    classify_comment,
    find_unknown_links,
    generate_answer,
    load_faq,
)
from telegram_channel_manager_bot.faq_store import (
    STATUS_APPROVING,
    STATUS_COMMENT_DELETED,
    STATUS_COMMENT_EDITED,
    STATUS_DECLINED,
    STATUS_FAILED,
    STATUS_NEW,
    STATUS_PENDING_APPROVAL,
    STATUS_POSTED,
    STATUS_SKIPPED,
    FaqStore,
)

STORE_KEY = "faq_store"
GROUP_ID_KEY = "faq_group_id"

# Own handler group, so FAQ handlers never compete with the /post ConversationHandler in group 0.
FAQ_HANDLER_GROUP = 1
WORKER_INTERVAL_SECONDS = 60
WORKER_FIRST_RUN_SECONDS = 10
WORKER_BATCH_SIZE = 20

CARD_COMMENT_MAX_CHARS = 1000
CALLBACK_PATTERN = r"^faq:(approve|decline):(\d+)$"
LANGUAGE_FLAGS = {"ru": "🇷🇺", "uk": "🇺🇦"}
REPLY_TARGET_MISSING = "message to be replied not found"

TOAST_ALREADY_HANDLED = "Уже обработано"
TOAST_RETRY = "Не удалось отправить, попробуйте ещё раз"
OUTCOME_DECLINED = "❌ Отклонено"
OUTCOME_POSTED = '✅ Опубликовано: <a href="{link}">ответ</a>'
OUTCOME_COMMENT_DELETED = "⚠️ Комментарий удалён, ответ не отправлен"
OUTCOME_SEND_FAILED = "⚠️ Ошибка отправки"
OUTCOME_SEND_UNCONFIRMED = "⚠️ Не удалось подтвердить отправку. Проверьте комментарии вручную."
OUTCOME_COMMENT_EDITED = "✏️ Комментарий изменён, ответ не отправлен. Ответьте вручную."


def _message_link(chat_id: int, message_id: int, thread_id: int) -> str:
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}?thread={thread_id}"


def _message_text(message: Message) -> str:
    return message.text or message.caption or ""


def _skip_reason(message: Message, group_id: int) -> str | None:
    """Chat and author filters shared by ingest and edits. Returns None for a comment we should track."""
    if message.message_thread_id is None:
        return "not a comment under a post"
    if message.is_automatic_forward:
        return "automatic forward of a channel post"
    if message.sender_chat is not None:
        if message.sender_chat.id in (TELEGRAM_CHANNEL_ID, group_id):
            return "sent on behalf of the channel or the group"
    elif message.from_user is None or message.from_user.is_bot:
        return "sent by a bot"
    # elif message.from_user.id == TELEGRAM_ADMIN_CHAT_ID:
    #     return "sent by the admin"
    return None


def _render_card(row: Mapping[str, Any]) -> str:
    comment = row["text"]
    if len(comment) > CARD_COMMENT_MAX_CHARS:
        comment = comment[:CARD_COMMENT_MAX_CHARS] + "…"
    faq_ids = ", ".join(f"#{faq_id}" for faq_id in json.loads(row["faq_ids"]))
    meta = f"📚 FAQ {faq_ids} · {LANGUAGE_FLAGS[row['language']]} {row['language']}"
    if row["partial"]:
        meta += " · ⚠️ частичный ответ"
    link = _message_link(row["chat_id"], row["message_id"], row["thread_id"])
    return (
        "💬 Вопрос в комментариях\n"
        f"👤 {html.escape(row['author_name'])}\n"
        f'🔗 <a href="{link}">Открыть комментарий</a>\n'
        f"❓ «{html.escape(comment)}»\n"
        f"{meta}\n\n"
        "✍️ Предлагаемый ответ:\n"
        f"{html.escape(row['answer'])}"
    )


async def _show_outcome(bot: Bot, row: Mapping[str, Any], outcome: str) -> None:
    """Edits the card to append the outcome. Omitting reply_markup removes the buttons."""
    await bot.edit_message_text(
        chat_id=TELEGRAM_ADMIN_CHAT_ID,
        message_id=row["card_message_id"],
        text=f"{_render_card(row)}\n\n{outcome}",
        parse_mode=ParseMode.HTML,
    )


async def _post_reply(bot: Bot, row: Mapping[str, Any]) -> Message:
    """The ONLY place the bot sends anything to the discussion group.

    Call it only from the approval callback, after the atomic pending_approval → approving transition.
    """
    return await bot.send_message(
        chat_id=row["chat_id"],
        text=row["answer"],
        reply_parameters=ReplyParameters(message_id=row["message_id"], allow_sending_without_reply=False),
    )


async def ingest_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    store: FaqStore = context.bot_data[STORE_KEY]
    text = _message_text(message)

    reason = _skip_reason(message, context.bot_data[GROUP_ID_KEY]) or (None if text else "no text")
    if reason:
        logger.info("Skipped group message_id={}: {}", message.message_id, reason)
        return

    if message.sender_chat is not None:
        author_id, author_name = None, message.sender_chat.title
    else:
        author_id, author_name = message.from_user.id, message.from_user.full_name

    row_id = store.insert_new(
        message.chat_id, message.message_id, message.message_thread_id, author_id, author_name, text
    )
    if row_id is None:
        logger.info("Duplicate comment message_id={}, already stored", message.message_id)
    else:
        logger.info("Saved comment id={} message_id={} from {}", row_id, message.message_id, author_name)


async def _process_comment(bot: Bot, store: FaqStore, row: Mapping[str, Any]) -> None:
    row_id, message_id, text = row["id"], row["message_id"], row["text"]

    def text_changed() -> bool:
        # An edit may have updated the text while we were awaiting the LLM or Telegram.
        if store.get(row_id)["text"] == text:
            return False
        logger.info("Comment id={} message_id={} was edited meanwhile, dropping this result", row_id, message_id)
        return True

    faq = load_faq()
    classification = await classify_comment(text, faq)
    faq_ids, language, partial = (
        classification["matched_faq_ids"],
        classification["language"],
        classification["partial"],
    )
    logger.info(
        "Classified comment id={} message_id={}: is_question={} faq_ids={} language={} partial={} reason={}",
        row_id,
        message_id,
        classification["is_question"],
        faq_ids,
        language,
        partial,
        classification["reason"],
    )
    if not faq_ids or language not in SUPPORTED_LANGUAGES:
        if not text_changed() and store.transition(row_id, STATUS_NEW, STATUS_SKIPPED, language=language):
            logger.info("Skipped comment id={} message_id={}", row_id, message_id)
        return

    entries = [faq[faq_id] for faq_id in faq_ids]
    answer = await generate_answer(text, language, entries, partial)
    logger.info("Generated answer for comment id={} message_id={} ({} chars)", row_id, message_id, len(answer))

    unknown_links = find_unknown_links(answer, entries)
    if unknown_links:
        error = f"link guard: {' '.join(unknown_links)}"
        store.transition(row_id, STATUS_NEW, STATUS_FAILED, last_error=error, answer=answer)
        logger.error("Comment id={} message_id={} failed the {}", row_id, message_id, error)
        return

    if text_changed():
        return

    fields = {"answer": answer, "language": language, "faq_ids": json.dumps(faq_ids), "partial": int(partial)}
    card_row = {**row, **fields}
    card = await bot.send_message(
        chat_id=TELEGRAM_ADMIN_CHAT_ID,
        text=_render_card(card_row),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Одобрить", callback_data=f"faq:approve:{row_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"faq:decline:{row_id}"),
                ]
            ]
        ),
    )
    fields["card_message_id"] = card.message_id
    logger.info("Sent card for comment id={} message_id={}", row_id, message_id)

    # The card is out, so an edit that landed during the send is handled like an edit of a pending card.
    if text_changed():
        store.transition(row_id, STATUS_NEW, STATUS_COMMENT_EDITED, **fields)
        await _show_outcome(bot, {**card_row, **fields}, OUTCOME_COMMENT_EDITED)
        return
    if not store.transition(row_id, STATUS_NEW, STATUS_PENDING_APPROVAL, **fields):
        logger.warning("Comment id={} message_id={} left the new status unexpectedly", row_id, message_id)


async def process_new_comments(context: ContextTypes.DEFAULT_TYPE) -> None:
    store: FaqStore = context.bot_data[STORE_KEY]
    for row in store.list_by_status(STATUS_NEW, WORKER_BATCH_SIZE):
        try:
            await _process_comment(context.bot, store, row)
        except Exception as error:  # noqa: BLE001 - one bad comment must not stop the batch; it is retried
            status = store.record_failure(row["id"], repr(error))
            logger.exception(
                "Failed to process comment id={} message_id={}, status is now {}", row["id"], row["message_id"], status
            )


async def _decline(bot: Bot, store: FaqStore, row_id: int) -> str | None:
    if not store.transition(row_id, STATUS_PENDING_APPROVAL, STATUS_DECLINED):
        return TOAST_ALREADY_HANDLED
    row = store.get(row_id)
    logger.info("Declined comment id={} message_id={}", row_id, row["message_id"])
    await _show_outcome(bot, row, OUTCOME_DECLINED)
    return None


async def _approve(bot: Bot, store: FaqStore, row_id: int) -> str | None:
    if not store.transition(row_id, STATUS_PENDING_APPROVAL, STATUS_APPROVING):
        return TOAST_ALREADY_HANDLED
    row = store.get(row_id)
    message_id = row["message_id"]
    logger.info("Approved comment id={} message_id={}, posting the reply", row_id, message_id)

    try:
        reply = await _post_reply(bot, row)
    except BadRequest as error:
        if REPLY_TARGET_MISSING in error.message.lower():
            store.transition(row_id, STATUS_APPROVING, STATUS_COMMENT_DELETED, last_error=error.message)
            logger.warning("Comment id={} message_id={} was deleted, reply not posted", row_id, message_id)
            await _show_outcome(bot, row, OUTCOME_COMMENT_DELETED)
        else:
            store.transition(row_id, STATUS_APPROVING, STATUS_FAILED, last_error=error.message)
            logger.error("Failed to post reply for comment id={} message_id={}: {}", row_id, message_id, error)
            await _show_outcome(bot, row, OUTCOME_SEND_FAILED)
        return None
    except TimedOut:
        # The reply may have been posted anyway, so it stays in approving, like a crash mid-send.
        logger.warning(
            "Timed out posting reply for comment id={} message_id={}, left in approving: check manually",
            row_id,
            message_id,
        )
        await _show_outcome(bot, row, OUTCOME_SEND_UNCONFIRMED)
        return None
    except NetworkError as error:
        store.transition(row_id, STATUS_APPROVING, STATUS_PENDING_APPROVAL, last_error=str(error))
        logger.warning("Network error posting reply for comment id={} message_id={}: {}", row_id, message_id, error)
        return TOAST_RETRY

    store.transition(row_id, STATUS_APPROVING, STATUS_POSTED, reply_message_id=reply.message_id)
    logger.info("Posted reply message_id={} for comment id={} message_id={}", reply.message_id, row_id, message_id)
    reply_link = _message_link(row["chat_id"], reply.message_id, row["thread_id"])
    await _show_outcome(bot, row, OUTCOME_POSTED.format(link=reply_link))
    return None


async def on_card_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != TELEGRAM_ADMIN_CHAT_ID:
        logger.warning("Ignored FAQ button {} from non-admin user_id={}", query.data, query.from_user.id)
        await query.answer()
        return

    action, row_id = context.match.group(1), int(context.match.group(2))
    store: FaqStore = context.bot_data[STORE_KEY]
    toast = None
    try:
        if action == "approve":
            toast = await _approve(context.bot, store, row_id)
        else:
            toast = await _decline(context.bot, store, row_id)
    finally:
        await query.answer(toast)


async def on_comment_edited(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.edited_message
    if _skip_reason(message, context.bot_data[GROUP_ID_KEY]):
        return

    store: FaqStore = context.bot_data[STORE_KEY]
    row = store.get_by_message(message.chat_id, message.message_id)
    if row is None:
        logger.info("Edit of untracked message_id={}, ignoring", message.message_id)
        return
    row_id, text = row["id"], _message_text(message)
    if text == row["text"]:
        logger.info("Edit of comment id={} message_id={} kept the text, ignoring", row_id, message.message_id)
        return

    if row["status"] == STATUS_NEW and store.update_new_text(row_id, text):
        logger.info("Updated text of new comment id={} message_id={}", row_id, message.message_id)
    elif row["status"] == STATUS_PENDING_APPROVAL and store.transition(
        row_id, STATUS_PENDING_APPROVAL, STATUS_COMMENT_EDITED
    ):
        logger.info("Comment id={} message_id={} edited while awaiting approval", row_id, message.message_id)
        await _show_outcome(context.bot, row, OUTCOME_COMMENT_EDITED)
    else:
        logger.info(
            "Edit of comment id={} message_id={} in status {}, ignoring", row_id, message.message_id, row["status"]
        )


async def setup_faq(app: Application) -> None:
    """Resolves the discussion group and registers the FAQ handlers and worker. Called from post_init."""
    try:
        channel = await app.bot.get_chat(TELEGRAM_CHANNEL_ID)
    except TelegramError:
        logger.exception("Failed to get channel {}, FAQ answers disabled", TELEGRAM_CHANNEL_ID)
        return
    group_id = channel.linked_chat_id
    if group_id is None:
        logger.error("Channel {} has no linked discussion group, FAQ answers disabled", TELEGRAM_CHANNEL_ID)
        return

    store = FaqStore(ROOT_DIR / FAQ_DB_PATH)
    for row in store.list_by_status(STATUS_APPROVING):
        logger.warning(
            "Comment id={} message_id={} is stuck in approving: the reply may have been posted, check manually",
            row["id"],
            row["message_id"],
        )
    app.bot_data[STORE_KEY] = store
    app.bot_data[GROUP_ID_KEY] = group_id

    group = filters.Chat(chat_id=group_id)
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & group, ingest_comment), group=FAQ_HANDLER_GROUP)
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE & group, on_comment_edited), group=FAQ_HANDLER_GROUP
    )
    app.add_handler(CallbackQueryHandler(on_card_button, pattern=CALLBACK_PATTERN), group=FAQ_HANDLER_GROUP)
    app.job_queue.run_repeating(process_new_comments, interval=WORKER_INTERVAL_SECONDS, first=WORKER_FIRST_RUN_SECONDS)
    logger.info("FAQ answers enabled for channel {} and discussion group {}", TELEGRAM_CHANNEL_ID, group_id)
