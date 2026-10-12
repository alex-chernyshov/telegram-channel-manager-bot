FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY prompts ./prompts

RUN uv sync --frozen --no-dev

CMD ["uv", "run", "telegram-channel-manager-bot"]
