import re
from pathlib import Path
from typing import Any

from loguru import logger

from telegram_channel_manager_bot.config import OPENAI_FAQ_ANSWER_MODEL, OPENAI_FAQ_CLASSIFIER_MODEL
from telegram_channel_manager_bot.openai_client import openai_client, parse_json_output

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
FAQ_PATH = ROOT_DIR / "prompts/often_q_a.md"
CLASSIFIER_PROMPT_PATH = ROOT_DIR / "prompts/faq_classifier_prompt.md"
ANSWER_PROMPT_PATH = ROOT_DIR / "prompts/faq_answer_prompt.md"

# FAQ file contract: each entry starts with a line like "12. ", and that number is the entry's stable ID.
FAQ_ENTRY_RE = re.compile(r"^(\d+)\.\s", re.MULTILINE)
URL_RE = re.compile(r"(?:https?://|t\.me/)[^\s<>«»\"']+")
URL_TRAILING_PUNCTUATION = ".,;:!?)]"

SUPPORTED_LANGUAGES = ("ru", "uk")

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_question": {"type": "boolean"},
        "language": {"type": "string", "enum": ["ru", "uk", "other"]},
        "matched_faq_ids": {"type": "array", "items": {"type": "integer"}},
        "partial": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_question", "language", "matched_faq_ids", "partial", "reason"],
    "additionalProperties": False,
}

# "answered" goes first so the model decides whether the FAQ covers the comment before writing the answer.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answered": {"type": "boolean"},
        "answer": {"type": "string"},
    },
    "required": ["answered", "answer"],
    "additionalProperties": False,
}


def load_faq() -> dict[int, str]:
    """Parses the FAQ file into {id: entry text}. Text before the first entry (the title) is ignored."""
    text = FAQ_PATH.read_text(encoding="utf-8")
    starts = list(FAQ_ENTRY_RE.finditer(text))
    entries: dict[int, str] = {}
    for start, next_start in zip(starts, [*starts[1:], None], strict=True):
        faq_id = int(start[1])
        if faq_id in entries:
            logger.error(f"Duplicate FAQ id {faq_id} in {FAQ_PATH}, keeping the first entry")
            continue
        entries[faq_id] = text[start.start() : next_start.start() if next_start else len(text)].strip()
    return entries


async def classify_comment(text: str, faq: dict[int, str]) -> dict[str, Any]:
    instructions = CLASSIFIER_PROMPT_PATH.read_text(encoding="utf-8") + "\n\n" + "\n\n".join(faq.values())

    response = await openai_client.responses.create(
        model=OPENAI_FAQ_CLASSIFIER_MODEL,
        instructions=instructions,
        input=f"<comment>\n{text}\n</comment>",
        reasoning={"effort": "minimal"},
        text={
            "format": {
                "type": "json_schema",
                "name": "faq_classification",
                "schema": CLASSIFICATION_SCHEMA,
                "strict": True,
            }
        },
        langsmith_extra={"name": "FAQ Classifier"},
    )
    result = parse_json_output(response)

    unknown_ids = [faq_id for faq_id in result["matched_faq_ids"] if faq_id not in faq]
    if unknown_ids:
        logger.warning(f"Classifier returned unknown FAQ ids {unknown_ids}, dropping them")
    result["matched_faq_ids"] = [faq_id for faq_id in dict.fromkeys(result["matched_faq_ids"]) if faq_id in faq]
    return result


async def generate_answer(text: str, language: str, entries: list[str], partial: bool) -> str | None:
    """Returns the answer, or None when the matched entries don't answer the comment (a classifier false positive)."""
    instructions = ANSWER_PROMPT_PATH.read_text(encoding="utf-8")
    faq_block = "\n\n".join(entries)
    prompt_input = (
        f"Язык ответа: {language}\n"
        f"Частичный ответ: {'да' if partial else 'нет'}\n\n"
        f"<faq>\n{faq_block}\n</faq>\n\n"
        f"<comment>\n{text}\n</comment>"
    )

    response = await openai_client.responses.create(
        model=OPENAI_FAQ_ANSWER_MODEL,
        instructions=instructions,
        input=prompt_input,
        text={
            "format": {
                "type": "json_schema",
                "name": "faq_answer",
                "schema": ANSWER_SCHEMA,
                "strict": True,
            }
        },
        langsmith_extra={"name": "FAQ Answer"},
    )
    result = parse_json_output(response)
    if not result["answered"]:
        return None
    answer = result["answer"].strip()
    if not answer:
        raise ValueError("Answerer returned an empty answer")
    return answer


def _extract_urls(text: str) -> list[str]:
    return [match.group().rstrip(URL_TRAILING_PUNCTUATION) for match in URL_RE.finditer(text)]


def find_unknown_links(answer: str, entries: list[str]) -> list[str]:
    """Link guard: returns the URLs in the answer that aren't exactly one of the matched FAQ entries' URLs."""
    allowed = set(_extract_urls("\n".join(entries)))
    return [url for url in _extract_urls(answer) if url not in allowed]
