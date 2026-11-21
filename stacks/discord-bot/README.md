# Self-Hosted AI Discord Bot

A lightweight, self-hosted Discord bot powered by the Hack Club AI API (OpenAI-compatible) and an internal SearXNG instance for web search. Designed to run under 1.2 GB of RAM on a 2 GB Debian VPS using Docker Compose.

## 1. Creating the Discord Application & Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Open the **Bot** tab on the left:
   - Click **Reset Token** (or Copy Token) to get your `DISCORD_TOKEN`.
   - Scroll down to **Privileged Gateway Intents** and enable **Message Content Intent**, then click **Save Changes**.
3. Open the **OAuth2** -> **URL Generator** tab:
   - Under **Scopes**, check `bot` and `applications.commands`.
   - Under **Bot Permissions**, check:
     - `View Channels`
     - `Send Messages`
     - `Read Message History`
     - `Attach Files`
   - Copy the generated URL at the bottom and open it in your browser to invite the bot to your server.

## 2. Configuration Setup

### 2.1 Environment Variables (`.env`)
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
nano .env
```
```dotenv
DISCORD_TOKEN=your_discord_bot_token_here
HACKCLUB_API_KEY=your_hackclub_api_key_here
```

### 2.2 SearXNG Secret Key (`searxng/settings.yml`)
Generate a random 32-byte hex secret key and replace `CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING_BEFORE_RUNNING` inside `searxng/settings.yml`:
```bash
sed -i "s/CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING_BEFORE_RUNNING/$(openssl rand -hex 32)/" searxng/settings.yml
```

### 2.3 Model Names & Endpoints (`config.json`)
Before starting the bot, check the current Hack Club AI documentation (or call `GET /models` against `https://ai.hackclub.com/proxy/v1/models` with your `HACKCLUB_API_KEY`) to verify the currently available chat, vision, and image generation models and endpoint paths.

Edit `config.json` and replace the placeholder model names (`PRIMARY_MODEL`, `CHEAP_MODEL`, `IMAGE_MODEL`, etc.) with real model identifiers from the Hack Club AI docs.

> **Hot-Reloading:** You can edit `config.json` or any Markdown file in `data/instructions/` while the bot is running—changes are picked up automatically without restarting the container.

## 3. Customizing Personality Instructions

- **Default personality:** Edit `data/instructions/default.md`.
- **Per-server personality:** When the bot joins a server (or starts up), it creates a placeholder file at `data/instructions/servers/<guild_id>__<server-name>.md`. Add any custom Markdown instructions to that file to override `default.md` for that specific server.

## 4. Operating Commands (Docker Compose)

### Start the stack in the background
```bash
docker compose up -d --build
```

### View live logs
```bash
docker compose logs -f
```
*(View only bot logs: `docker compose logs -f bot`)*

### Stop the stack
```bash
docker compose down
```

### Update and rebuild after code changes
```bash
docker compose up -d --build
```

## 5. Discord Usage

- **Slash Commands** (requires **Manage Server** permission):
  - `/off` — Disable the bot in the current server.
  - `/mention` — Bot replies only when `@mentioned`.
  - `/chat` — Bot replies when `@mentioned` and randomly chimes in on channel messages based on `chat_mode.chance`.
- **Vision:** Upload up to 4 images in a message and `@mention` the bot with your question.
- **Web Search:** `@bot search for <your query>`
- **Image Generation:** `@bot generate an image <your idea>`
