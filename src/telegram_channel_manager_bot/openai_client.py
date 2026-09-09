from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

from telegram_channel_manager_bot.config import OPENAI_API_KEY

openai_client = wrap_openai(AsyncOpenAI(api_key=OPENAI_API_KEY))
