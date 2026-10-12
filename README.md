# Telegram Channel Manager Bot

A Telegram bot that formats draft posts in a channel's style and calculates
calorie/macro info, using OpenAI and `python-telegram-bot`. Runs as a long-polling
process (no inbound webhook/port needed).

## Deploying on a fresh DigitalOcean droplet (Ubuntu, latest LTS)

### 1. Create the droplet

- Create a droplet with the latest Ubuntu LTS image (any size is fine — the bot is
  lightweight, the smallest plan works).
- Add your SSH key during creation so you can connect without a password.

### 2. Connect and do basic setup

```bash
ssh root@YOUR_DROPLET_IP
```

```bash
apt update && apt upgrade -y
```

### 3. Install Docker Engine + Compose plugin

```bash
apt install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

### 4. Create a non-root user (recommended)

```bash
adduser bot
usermod -aG docker bot
su - bot
```

Run the rest of the steps as this `bot` user.

### 5. Get the code onto the droplet

```bash
sudo apt install -y git make
git clone <YOUR_REPO_URL> telegram-channel-manager-bot
cd telegram-channel-manager-bot
```

### 6. Configure environment variables

```bash
cp .env.example .env
nano .env
```

Fill in real values:

```
TELEGRAM_BOT_TOKEN=<token from @BotFather>
TELEGRAM_ADMIN_CHAT_ID=<your Telegram user id>
TELEGRAM_CHANNEL_ID=<channel id, e.g. -1004325241527>
OPENAI_API_KEY=<your OpenAI API key>
OPENAI_FORMATTER_MODEL=gpt-5-mini
OPENAI_CALCULATOR_MODEL=gpt-5-mini
OPENAI_PRODUCTS_FILE_ID=<file id of the uploaded prompts/products.md>
OPENAI_FAQ_CLASSIFIER_MODEL=gpt-5-nano
OPENAI_FAQ_ANSWER_MODEL=gpt-6.1-sol
FAQ_DB_PATH=data/faq.sqlite3
LOG_LEVEL=INFO
ENABLE_CALORIE_CALCULATION=false
ENABLE_FAQ_ANSWERS=false
```

`TELEGRAM_ADMIN_CHAT_ID` receives the FAQ approval cards and is the only user
whose button taps are accepted. With `ENABLE_FAQ_ANSWERS=true` the bot must be a
member of the channel's discussion group; it reads new comments there and, only
after the admin approves, replies to matching questions from `prompts/often_q_a.md`.
Its SQLite queue lives in `data/` (mounted as a volume, so it survives rebuilds).

### 7. Build and start the bot

```bash
make up
```

This runs `docker compose up -d --build`, which builds the image from the
`Dockerfile` and starts the `bot` service in the background with
`restart: unless-stopped` (it will survive droplet reboots and crashes once
Docker's own service is enabled, which is the default on Ubuntu).

### 8. Check it's running

```bash
make logs
```

You should see `Starting bot` and `Command menu registered` in the log output.
Press `Ctrl+C` to stop following logs (the container keeps running). Then message
your bot on Telegram with `/start` to confirm it responds.

## Day-to-day operations

From the project directory on the droplet:

```bash
make restart   # restart the bot container
make down      # stop and remove the container
make deploy    # git pull latest code + rebuild + restart
```

## Updating the bot later

```bash
cd telegram-channel-manager-bot
make deploy
```