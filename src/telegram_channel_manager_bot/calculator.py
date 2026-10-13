from pathlib import Path
from typing import Any

from loguru import logger

from telegram_channel_manager_bot.config import OPENAI_CALCULATOR_MODEL, OPENAI_PRODUCTS_FILE_ID
from telegram_channel_manager_bot.openai_client import openai_client, parse_json_output

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CALCULATOR_PROMPT_PATH = ROOT_DIR / "prompts/calculator_agent_prompt.md"

CALCULATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "calories_total": {"type": "integer"},
        "protein_total": {"type": "integer"},
        "fat_total": {"type": "integer"},
        "carbs_total": {"type": "integer"},
        "macros_total": {"type": "string"},
        "portions": {"type": ["integer", "null"]},
        "calories_per_portion": {"type": ["integer", "null"]},
        "breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ingredient": {"type": "string"},
                    "matched_row": {"type": "string"},
                    "grams": {"type": "number"},
                    "calories": {"type": "number"},
                    "protein": {"type": "number"},
                    "fat": {"type": "number"},
                    "carbs": {"type": "number"},
                },
                "required": [
                    "ingredient",
                    "matched_row",
                    "grams",
                    "calories",
                    "protein",
                    "fat",
                    "carbs",
                ],
                "additionalProperties": False,
            },
        },
        "substitutions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "unmatched": {"type": "array", "items": {"type": "string"}},
        "needs_review": {"type": "boolean"},
    },
    "required": [
        "calories_total",
        "protein_total",
        "fat_total",
        "carbs_total",
        "macros_total",
        "portions",
        "calories_per_portion",
        "breakdown",
        "substitutions",
        "assumptions",
        "unmatched",
        "needs_review",
    ],
    "additionalProperties": False,
}


async def calculate_calories(raw_text: str) -> dict[str, Any]:
    logger.info(f"Calculating calories for draft ({len(raw_text)} chars)")
    instructions = CALCULATOR_PROMPT_PATH.read_text(encoding="utf-8")

    response = await openai_client.responses.create(
        model=OPENAI_CALCULATOR_MODEL,
        instructions=instructions,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": raw_text},
                    {"type": "input_file", "file_id": OPENAI_PRODUCTS_FILE_ID},
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "calorie_calculation",
                "schema": CALCULATION_SCHEMA,
                "strict": True,
            }
        },
    )
    result = parse_json_output(response)

    logger.info(
        f"Calculated {result['calories_total']} kcal, {result['macros_total']} (needs_review={result['needs_review']})"
    )
    if result["unmatched"]:
        logger.warning(f"Unmatched ingredients: {result['unmatched']}")
    return result
