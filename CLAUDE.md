# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Python 3.12, managed with `uv`. All common tasks go through the Makefile:

```bash
make install      # uv sync
make run          # run the bot locally (long polling, reads .env)
make lint         # ruff check
make format       # ruff format
make check        # lint + format --check (run before committing)
make up / down / logs / restart   # docker compose lifecycle for the `bot` service
make deploy       # git pull + rebuild + restart (used on the droplet)
make dump-channel N=50                       # scripts/dump_channel_messages.py
make fetch-message LINKS="https://t.me/x/85" # scripts/fetch_channel_message.py
```

There is no test suite. Ruff line length is 120.

Configuration is read from `.env` (see `.env.example`) by `config.py` at import time. `ENABLE_CALORIE_CALCULATION=true` turns on the calculator; otherwise only formatting runs. `ENABLE_FAQ_ANSWERS=true` turns on the FAQ auto-answers (below).

## Architecture

A Telegram bot (`python-telegram-bot`, long polling) that turns an author's rough recipe draft into a post in the channel's style, and optionally fills in calorie/macro numbers. The user-facing text, the prompts and the style guide are in Russian.

**Request flow** (`__init__.py`, `receive_draft`): `/post` opens a `ConversationHandler`, and the next text message is the draft. Two OpenAI calls then run concurrently:

1. **Formatter** (`formatting.py`): streams a Responses API call whose system prompt wraps `prompts/style_guide.md`. Partial output is pushed to the user live with `bot.send_message_draft` (throttled to one update per second), then sent as a real message.
2. **Calculator** (`calculator.py`): a non-streaming Responses API call with strict JSON-schema output (`CALCULATION_SCHEMA`), using `prompts/calculator_agent_prompt.md` as instructions and the products table as an attached `input_file`.

After both finish, `_apply_calculation` substitutes the numbers into the formatted post and edits the sent message. If the calculation fails, `—` goes in place of the numbers, so the post is never lost.

**Placeholder contract:** the formatter never writes numbers. It emits the literal tags `<calories_placeholder>`, `<macros_placeholder>` and `<calories_per_portion_placeholder>`, and code replaces them. These tag strings are defined in `formatting.py`, specified in `style_guide.md` §0, and relied on by the calculator prompt. A change to the tags or to the calculator's output fields must be made in the Python constants, `CALCULATION_SCHEMA`, and both prompt files together.

**Prompts are runtime assets:** `style_guide.md` and `calculator_agent_prompt.md` are read from disk on every request, with paths resolved relative to the repo root (`Path(__file__).parent.parent.parent / "prompts/..."`). The Dockerfile copies `prompts/` into the image for this reason. `style_guide_analysis.md` is the prompt that was used to generate `style_guide.md` from channel dumps; the bot does not use it at runtime.

**Products table:** `prompts/products.md` is **not** read by the code. It is uploaded to OpenAI once, and its file ID is passed through `OPENAI_PRODUCTS_FILE_ID`. Editing the local file has no effect until it is re-uploaded and the env var is updated.

**OpenAI client** (`openai_client.py`): a single shared `AsyncOpenAI` wrapped with LangSmith `wrap_openai` for tracing, tagged with the `ENV` value.

**Errors:** the global `error_handler` logs transient `NetworkError`s as warnings and everything else, including `BadRequest`, as errors. Handlers catch broad exceptions only where they need to tell the user something or fall back (each such catch has a `# noqa: BLE001` with the reason).

**Scripts** (`scripts/`) are standalone tools for collecting channel posts into `dumps/` (gitignored) to build the style guide. They are not part of the bot. The Bot API can't read channel history, so `dump_channel_messages.py` listens for new/pending `channel_post` updates (the bot must be a channel admin), and `fetch_channel_message.py` forwards each linked message to `TELEGRAM_ADMIN_CHAT_ID` and reads the text back.

**FAQ auto-answers** (`faq_flow.py`, `faq_agents.py`, `faq_store.py`; design in `docs/faq_answers_plan.md`): `setup_faq` runs from `post_init`, resolves the channel's linked discussion group and registers handlers in handler group 1 plus a `JobQueue` worker. The ingest handler stores each new comment (`UNIQUE(chat_id, message_id)` in SQLite at `FAQ_DB_PATH`) with no LLM call and no send. Every 60 s the worker classifies `new` rows against `prompts/often_q_a.md`, drafts an answer for matches, checks that every link in it appears in the matched FAQ entries, and sends an approval card with ✅/❌ to `TELEGRAM_ADMIN_CHAT_ID`. Statuses: `new → skipped | pending_approval | failed` (plus `comment_edited` if an edit lands while the card is being sent), `pending_approval → approving → posted | comment_deleted | failed`, `pending_approval → declined | comment_edited`. Every transition is an atomic `UPDATE ... WHERE id=? AND status=?`. **Invariant:** `_post_reply` is the only code that sends to the discussion group, and it runs only from the ✅ callback after `pending_approval → approving`. The bot never writes to the channel. A row left in `approving` (crash or `TimedOut` mid-send) is never auto-resolved, because the reply may already be posted. With the flag off, no FAQ handler or job is registered. `/start`, `/post` and `/cancel` answer only in private chats, because the bot also sits in the public discussion group.

**FAQ file contract:** each entry in `often_q_a.md` starts with a line matching `^\d+\.\s`, and that number is its stable ID (shown on cards, used by the classifier). The entry runs until the next such line, so a line inside an answer must not start with `N. `. Text before the first entry is ignored. The file and both FAQ prompts (`faq_classifier_prompt.md`, `faq_answer_prompt.md`) are read from disk on every call.

Logging uses `loguru` throughout (`logger.info(f"... {value}")` with f-strings, not loguru's `"{}", value` brace formatting), configured by `setup_logging`. Never pass extra args or kwargs alongside an f-string message: loguru would then call `.format()` on it, and braces in user text (comments, drafts) would break it.

## Deployment

Deployed with Docker Compose on a DigitalOcean Ubuntu droplet (step-by-step in README.md). The image runs `uv sync --frozen --no-dev`, so `uv.lock` must be kept in sync with `pyproject.toml`.
