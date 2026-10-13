import json
import os
from typing import Any

from langsmith.wrappers import wrap_openai
from loguru import logger
from openai import AsyncOpenAI
from openai.types.responses import Response

from telegram_channel_manager_bot.config import OPENAI_API_KEY

env = os.getenv("ENV", "dev")

openai_client = wrap_openai(AsyncOpenAI(api_key=OPENAI_API_KEY), tracing_extra={"tags": [env]})


def parse_json_output(response: Response) -> dict[str, Any]:
    """
    Parses the first output_text block.
    Response.output_text joins all blocks, and models sometimes repeat the JSON.
    """
    texts = [
        content.text
        for item in response.output
        if item.type == "message"
        for content in item.content
        if content.type == "output_text"
    ]
    if not texts:
        raise ValueError(f"Response {response.id} has no output_text")
    if len(texts) > 1:
        logger.warning("Response {} has {} output_text blocks, using the first", response.id, len(texts))
    result: dict[str, Any] = json.loads(texts[0])
    return result
