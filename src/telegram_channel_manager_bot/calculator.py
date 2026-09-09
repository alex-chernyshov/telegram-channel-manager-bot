import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from telegram_channel_manager_bot.config import OPENAI_CALCULATOR_MODEL
from telegram_channel_manager_bot.openai_client import openai_client

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CALCULATOR_PROMPT_PATH = ROOT_DIR / "prompts/calculator_agent_prompt.md"
PRODUCTS_TABLE_PATH = ROOT_DIR / "prompts/products.md"

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

_products_file_id: str | None = None
_products_file_lock = asyncio.Lock()


async def _get_products_file_id() -> str:
    global _products_file_id
    async with _products_file_lock:
        if _products_file_id is None:
            uploaded = await openai_client.files.create(
                file=PRODUCTS_TABLE_PATH.open("rb"), purpose="user_data"
            )
            _products_file_id = uploaded.id
            logger.info("Uploaded products table as file_id={}", _products_file_id)
    return _products_file_id


async def calculate_calories(raw_text: str) -> dict[str, Any]:
    logger.info("Calculating calories for draft ({} chars)", len(raw_text))
    instructions = CALCULATOR_PROMPT_PATH.read_text(encoding="utf-8")
    file_id = await _get_products_file_id()

    response = await openai_client.responses.create(
        model=OPENAI_CALCULATOR_MODEL,
        instructions=instructions,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": raw_text},
                    {"type": "input_file", "file_id": file_id},
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
    result: dict[str, Any] = json.loads(response.output_text)

    logger.info(
        "Calculated {} kcal, {} (needs_review={})",
        result["calories_total"],
        result["macros_total"],
        result["needs_review"],
    )
    if result["unmatched"]:
        logger.warning("Unmatched ingredients: {}", result["unmatched"])
    return result
