from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from telegram_channel_manager_bot.config import OPENAI_FORMATTER_MODEL
from telegram_channel_manager_bot.openai_client import openai_client

STYLE_GUIDE_PATH = Path(__file__).resolve().parent.parent.parent / "prompts/style_guide.md"

# Contract defined in style_guide.md §0 "Разделение ролей" - the FormatterAgent
# writes these tags literally, the CalculatorAgent's numbers get substituted in.
CALORIES_PLACEHOLDER = "<calories_placeholder>"
MACROS_PLACEHOLDER = "<macros_placeholder>"
CALORIES_PER_PORTION_PLACEHOLDER = "<calories_per_portion_placeholder>"

SYSTEM_PROMPT_TEMPLATE = """# ЛИЧНОСТЬ
Ты — редактор Telegram-канала. Тебе присылают черновой, неотформатированный текст поста от автора канала.

# ЗАДАЧА
Твоя задача — переписать этот черновик набело для публикации в канале, строго следуя руководству по авторскому стилю ниже.

# ПРАВИЛА
- Сохраняй мысль, факты и последовательность автора. Не придумывай ингредиенты, шаги или цифры, которых нет в черновике.
- Оформляй результат в точности по руководству: структура блоков, эмодзи, пунктуация, хэштеги и т.д.
- Если в черновике не хватает данных для какого-то блока руководства — не выдумывай их, просто пропусти блок.
- В ответе выдай только готовый текст поста, без вступлений, комментариев и пояснений.

{style_guide}
"""


async def format_post_stream(raw_text: str, on_update: Callable[[str], Awaitable[None]]) -> str:
    logger.info(f"Formatting draft ({len(raw_text)} chars) with model={OPENAI_FORMATTER_MODEL}")
    style_guide = STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    instructions = SYSTEM_PROMPT_TEMPLATE.format(style_guide=style_guide)

    accumulated = ""
    stream = await openai_client.responses.create(
        model=OPENAI_FORMATTER_MODEL,
        instructions=instructions,
        input=raw_text,
        reasoning={"effort": "medium"},
        stream=True,
        langsmith_extra={"name": "Formatter"},
    )
    async with stream:
        async for event in stream:
            if event.type == "response.output_text.delta":
                accumulated += event.delta
                await on_update(accumulated)

    logger.info(f"Formatted post ready ({len(accumulated)} chars)")
    return accumulated
