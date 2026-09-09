.PHONY: install run lint format check clean up down logs restart deploy dump-channel fetch-message

install:
	uv sync

run:
	uv run telegram-channel-manager-bot

lint:
	uv run ruff check .

format:
	uv run ruff format .

check: lint
	uv run ruff format --check .

clean:
	rm -rf .venv build dist *.egg-info

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f bot

restart:
	docker compose restart bot

deploy:
	git pull
	docker compose up -d --build

N ?= 1
dump-channel:
	uv run python scripts/dump_channel_messages.py --count $(N)

fetch-message:
	uv run python scripts/fetch_channel_message.py $(LINKS)
