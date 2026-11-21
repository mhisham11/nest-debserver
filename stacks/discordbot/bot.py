"""Discord AI bot — configuration comes from a .env file next to this script."""
import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import discord
import httpx
from discord import app_commands
from ddgs import DDGS
from dotenv import load_dotenv

# ---------- environment ----------
# Anchor everything to this file, not the process CWD, so behaviour is identical
# whether the bot is started by systemd, Docker, or a bare shell in another dir.
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(os.getenv("ENV_FILE") or BASE_DIR / ".env")

# override=False: a real environment variable (systemd EnvironmentFile, Docker
# --env-file, a Kubernetes secret, CI) always beats the .env file. That is what
# you want in prod — .env is a local-dev convenience, not the source of truth.
load_dotenv(dotenv_path=ENV_FILE, override=False)


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read an env var, trimming stray whitespace, and fail loudly if required."""
    value = os.getenv(name, default)
    if isinstance(value, str):
        value = value.strip()
    if required and not value:
        raise SystemExit(
            f"Missing required environment variable {name!r}.\n"
            f"Expected it in {ENV_FILE} (copy .env.example -> .env) or in the "
            f"process environment."
        )
    return value


TOKEN = env("DISCORD_TOKEN", required=True)
API_KEY = env("HACKCLUB_API_KEY", required=True)
API_BASE = env("API_BASE", "https://ai.hackclub.com/proxy/v1").rstrip("/")
CONFIG_FILE = str(BASE_DIR / env("CONFIG_FILE", "config.json"))
INSTRUCTIONS_FILE = str(BASE_DIR / env("INSTRUCTIONS_FILE", "instructions.txt"))

logging.basicConfig(
    level=env("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("discordbot")

# Cheap leak check: a secrets file readable by group/other is a finding.
if ENV_FILE.exists() and ENV_FILE.stat().st_mode & 0o077:
    log.warning("%s is readable by other users — run: chmod 600 %s", ENV_FILE, ENV_FILE)

# Everything tunable lives in config.json (hot-reloaded, no restart needed).
DEFAULTS = {
    "model": "google/gemma-3-4b-it",
    "fallback_models": [
        "google/gemma-3n-e4b-it",
        "google/gemma-3-12b-it",
        "meta-llama/llama-3.1-8b-instruct",
        "meta-llama/llama-3.2-3b-instruct",
        "mistralai/mistral-small-3.2-24b-instruct",
        "openai/gpt-4o-mini",
    ],
    "history_messages": 40,          # how many recent messages the bot sees
    "history_max_age_minutes": 720,  # ignore messages older than this
    "message_max_chars": 600,        # truncate each message in context
    "max_tokens": 350,               # cap on reply length
    "temperature": 0.8,
    "chime_chance": 0.03,            # chance per message (0.03 = 3%)
    "chime_cooldown_sec": 900,       # min seconds between chime-ins per channel
    "chime_min_chars": 20,           # ignore very short messages for chime-ins
    "user_cooldown_sec": 4,          # min seconds between requests per user
    "default_mode": "chat",          # off | mentions | chat
    "channels": {},                  # {"channel_id": "off|mentions|chat"} overrides
}

_cfg = {"mtime": 0.0, "data": dict(DEFAULTS)}


def save_cfg(data: dict):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def cfg() -> dict:
    try:
        mt = os.path.getmtime(CONFIG_FILE)
        if mt != _cfg["mtime"]:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                _cfg["data"] = {**DEFAULTS, **json.load(f)}
            _cfg["mtime"] = mt
    except FileNotFoundError:
        save_cfg(DEFAULTS)
    except Exception as e:
        print("config error (keeping previous settings):", e)
    return _cfg["data"]


def load_instructions() -> str:
    try:
        with open(INSTRUCTIONS_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "You are a friendly, casual Discord chat bot. Keep replies short."


class Bot(discord.Client):
    async def setup_hook(self):
        await tree.sync()


intents = discord.Intents.default()
intents.message_content = True
client = Bot(intents=intents)
tree = app_commands.CommandTree(client)
http = httpx.AsyncClient(timeout=60)

last_chime: dict[int, float] = {}
last_user: dict[int, float] = {}

SEARCH_RE = re.compile(r"\b(search(?:\s+(?:up|for|the web for|online for))?|look\s?up|google)\b", re.I)
MENTION_RE = re.compile(r"<@!?\d+>")
THINK_RE = re.compile(r"<think>.*?</think>", re.S)

# ---------- model selection ----------
_model_cache = {"key": None, "model": None, "at": 0.0}


async def pick_model(c: dict) -> str:
    """Use the configured model; if Hack Club doesn't offer it, fall back down the list."""
    key = (c["model"], tuple(c["fallback_models"]))
    if _model_cache["key"] == key and time.time() - _model_cache["at"] < 600:
        return _model_cache["model"]
    chosen = c["model"]
    try:
        r = await http.get(f"{API_BASE}/models")
        r.raise_for_status()
        ids = {m["id"] for m in r.json()["data"]}
        for m in [c["model"], *c["fallback_models"]]:
            if m in ids:
                chosen = m
                break
        else:
            print("WARNING: none of the configured models are available. Available:", sorted(ids))
        if chosen != c["model"]:
            print(f"Model {c['model']} not available, using {chosen}")
    except Exception as e:
        print("model list error:", e)
    _model_cache.update(key=key, model=chosen, at=time.time())
    return chosen


async def ask_llm(messages: list[dict], c: dict) -> str:
    model = await pick_model(c)
    r = await http.post(
        f"{API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"model": model, "messages": messages, "max_tokens": c["max_tokens"], "temperature": c["temperature"]},
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"] or ""
    return THINK_RE.sub("", text).strip()


# ---------- helpers ----------
async def web_search(query: str) -> str | None:
    def run():
        with DDGS() as d:
            return list(d.text(query, max_results=5))

    try:
        results = await asyncio.to_thread(run)
    except Exception as e:
        print("search error:", e)
        return None
    if not results:
        return None
    return "\n".join(f"- {r['title']}: {r['body'][:200]} ({r['href']})" for r in results)


async def build_history(message: discord.Message, c: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=c["history_max_age_minutes"])
    msgs = [m async for m in message.channel.history(limit=c["history_messages"], before=message)]
    msgs.reverse()
    msgs.append(message)
    out: list[dict] = []
    for m in msgs:
        if m is not message and m.created_at < cutoff:
            continue
        text = m.clean_content[: c["message_max_chars"]].strip()
        if not text:
            continue
        if m.author == client.user:
            role, content = "assistant", text
        elif m.author.bot:
            continue
        else:
            role, content = "user", f"{m.author.display_name}: {text}"
        # Small models are picky: merge same-role neighbours so roles strictly alternate.
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + content
        else:
            out.append({"role": role, "content": content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def channel_mode(channel, c: dict) -> str:
    ids = [str(channel.id)]
    if isinstance(channel, discord.Thread):
        ids.append(str(channel.parent_id))
    for i in ids:
        if i in c["channels"]:
            return c["channels"][i]
    return c["default_mode"]


async def send_chunks(channel, text: str, reply_to: discord.Message | None):
    none = discord.AllowedMentions.none()
    chunks = [text[i:i + 1990] for i in range(0, len(text), 1990)] or ["..."]
    for i, chunk in enumerate(chunks):
        if reply_to and i == 0:
            await reply_to.reply(chunk, mention_author=False, allowed_mentions=none)
        else:
            await channel.send(chunk, allowed_mentions=none)


# ---------- events ----------
@client.event
async def on_ready():
    c = cfg()
    print(f"Logged in as {client.user}; model {await pick_model(c)}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    c = cfg()
    mode = channel_mode(message.channel, c)
    if mode == "off":
        return

    mentioned = client.user in message.mentions
    ref = message.reference
    replied = bool(ref and isinstance(ref.resolved, discord.Message) and ref.resolved.author == client.user)
    direct = mentioned or replied
    now = time.time()
    chime = False

    if direct:
        if now - last_user.get(message.author.id, 0) < c["user_cooldown_sec"]:
            return
        last_user[message.author.id] = now
    else:
        if (
            mode == "chat"
            and len(message.content) >= c["chime_min_chars"]
            and random.random() < c["chime_chance"]
            and now - last_chime.get(message.channel.id, 0) > c["chime_cooldown_sec"]
        ):
            chime = True
            last_chime[message.channel.id] = now
        else:
            return

    system = [load_instructions()]
    if chime:
        system.append(
            "You are jumping into this conversation unprompted. Only reply if you have something genuinely "
            "funny or useful to add, in one or two short sentences. Otherwise reply with exactly: SKIP"
        )

    if direct:  # web search only on explicit request
        raw = MENTION_RE.sub("", message.content).strip()
        m = SEARCH_RE.search(raw)
        if m:
            query = (raw[m.end():].strip(" :,-") or raw)[:200]
            results = await web_search(query)
            if results:
                system.append(f"Web search results for '{query}'. Use them to answer, and mention sources naturally:\n{results}")
            else:
                system.append("A web search was requested but failed or found nothing. Say so briefly.")

    try:
        async with message.channel.typing():
            history = await build_history(message, c)
            reply = await ask_llm([{"role": "system", "content": "\n\n".join(system)}] + history, c)
    except httpx.HTTPStatusError as e:
        print("API error:", e.response.status_code, e.response.text[:300])
        if direct:
            msg = "I'm being rate limited, try again in a bit." if e.response.status_code == 429 else "My brain glitched, try again."
            await message.reply(msg, mention_author=False)
        return
    except Exception as e:
        print("error:", e)
        return

 # Remove a leading "BotName:" the model copied from the chat format
    for n in {client.user.name, message.guild.me.display_name}:
        reply = re.sub(rf"^\s*\**{re.escape(n)}\**\s*:\s*", "", reply, flags=re.I)

    if not reply or "SKIP" in reply.upper()[:12]:
        return
    await send_chunks(message.channel, reply, message if direct else None)


# ---------- slash commands (need Manage Server) ----------
ai = app_commands.Group(
    name="ai", description="Tune the AI bot", guild_only=True,
    default_permissions=discord.Permissions(manage_guild=True),
)
tree.add_command(ai)
ADMIN = app_commands.checks.has_permissions(manage_guild=True)


def edit_cfg(fn):
    data = json.loads(json.dumps(cfg()))  # deep copy
    fn(data)
    save_cfg(data)


@ai.command(name="status", description="Show current settings")
@ADMIN
async def status(interaction: discord.Interaction):
    c = cfg()
    lines = [
        f"**Model:** `{c['model']}`",
        f"**Default mode:** {c['default_mode']}",
        f"**Chime-in:** {c['chime_chance'] * 100:g}% per message, {c['chime_cooldown_sec'] / 60:g} min cooldown per channel",
        f"**Context:** last {c['history_messages']} messages (max {c['history_max_age_minutes']} min old)",
    ]
    overrides = [f"<#{cid}>: {m}" for cid, m in c["channels"].items()]
    lines.append("**Channel overrides:** " + (", ".join(overrides) if overrides else "none"))
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@ai.command(name="chime", description="Set how often the bot chimes in unprompted")
@app_commands.describe(percent="Chance per message, 0-100 (0 = never)", cooldown_minutes="Minimum minutes between chime-ins per channel")
@ADMIN
async def chime_cmd(
    interaction: discord.Interaction,
    percent: app_commands.Range[float, 0, 100],
    cooldown_minutes: Optional[app_commands.Range[float, 0, 1440]] = None,
):
    def apply(d):
        d["chime_chance"] = percent / 100
        if cooldown_minutes is not None:
            d["chime_cooldown_sec"] = int(cooldown_minutes * 60)

    edit_cfg(apply)
    await interaction.response.send_message(f"Chime-in chance set to {percent:g}%.", ephemeral=True)


@ai.command(name="channel", description="Set how the bot behaves in a channel")
@app_commands.describe(
    mode="off = silent, mentions = only when pinged, chat = pings + random chime-ins, default = use the server default",
    channel="Defaults to the channel you're in",
)
@ADMIN
async def channel_cmd(
    interaction: discord.Interaction,
    mode: Literal["off", "mentions", "chat", "default"],
    channel: Optional[discord.TextChannel] = None,
):
    target = channel or interaction.channel

    def apply(d):
        if mode == "default":
            d["channels"].pop(str(target.id), None)
        else:
            d["channels"][str(target.id)] = mode

    edit_cfg(apply)
    await interaction.response.send_message(f"<#{target.id}> is now: **{mode}**", ephemeral=True)


@ai.command(name="default", description="Set the default behaviour for channels without an override")
@ADMIN
async def default_cmd(interaction: discord.Interaction, mode: Literal["off", "mentions", "chat"]):
    edit_cfg(lambda d: d.__setitem__("default_mode", mode))
    await interaction.response.send_message(f"Default mode is now **{mode}**.", ephemeral=True)


@tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You need the Manage Server permission for that."
    else:
        print("command error:", error)
        msg = "Something went wrong."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


client.run(TOKEN)
