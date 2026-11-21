import io
import logging
import random
import re
import time
from typing import Any
import discord

from bot.budget import BudgetTracker
from bot.config import ConfigManager
from bot.db import Database
from bot.images import extract_image_data_urls
from bot.instructions import InstructionManager
from bot.llm import HackClubLLMClient
from bot.safety import (
    IMAGE_PROMPT_SYSTEM_PROMPT,
    build_system_prompt,
    redact_secrets,
    split_discord_message,
    wrap_search_results,
    wrap_user_message,
)
from bot.search import SearXNGClient

logger = logging.getLogger("bot.events")


def strip_bot_mention(content: str, bot_id: int) -> str:
    pattern = re.compile(rf"<@!?{bot_id}>")
    return pattern.sub("", content).strip()


def match_trigger(text: str, triggers: list[str]) -> str | None:
    lowered = text.lower()
    for trig in triggers:
        t = trig.strip().lower()
        if not t:
            continue
        if lowered.startswith(t):
            remainder = text[len(t) :].lstrip(" :,-")
            return remainder.strip()
    return None


class MessageHandler:
    def __init__(
        self,
        bot: discord.Client,
        config_mgr: ConfigManager,
        db: Database,
        budget: BudgetTracker,
        instructions: InstructionManager,
        llm: HackClubLLMClient,
        search_client: SearXNGClient,
        secrets: list[str],
    ) -> None:
        self.bot = bot
        self.config_mgr = config_mgr
        self.db = db
        self.budget = budget
        self.instructions = instructions
        self.llm = llm
        self.search_client = search_client
        self.secrets = secrets
        self._user_cooldowns: dict[int, float] = {}
        self._channel_cooldowns: dict[int, float] = {}

    def _check_user_cooldown(self, user_id: int, cooldown_sec: float) -> bool:
        now = time.monotonic()
        last = self._user_cooldowns.get(user_id, 0.0)
        if now - last < cooldown_sec:
            return False
        self._user_cooldowns[user_id] = now
        return True

    def _check_channel_cooldown(self, channel_id: int, cooldown_sec: float) -> bool:
        now = time.monotonic()
        last = self._channel_cooldowns.get(channel_id, 0.0)
        if now - last < cooldown_sec:
            return False
        self._channel_cooldowns[channel_id] = now
        return True

    async def _build_context_messages(
        self,
        message: discord.Message,
        limit: int,
        max_chars: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0 or message.guild is None:
            return []

        history: list[discord.Message] = []
        try:
            async for msg in message.channel.history(limit=limit, before=message):
                if msg.author.bot and (self.bot.user is None or msg.author.id != self.bot.user.id):
                    continue
                history.append(msg)
        except Exception as exc:
            logger.warning("Failed to fetch channel history in guild %d: %s", message.guild.id, type(exc).__name__)
            return []

        history.reverse()
        context_msgs: list[dict[str, Any]] = []
        for msg in history:
            text = (msg.clean_content or "").strip()
            if not text:
                continue
            if len(text) > max_chars:
                text = text[:max_chars] + "..."

            if self.bot.user and msg.author.id == self.bot.user.id:
                context_msgs.append({"role": "assistant", "content": text})
            else:
                formatted = f"{msg.author.display_name}: {text}"
                context_msgs.append({"role": "user", "content": wrap_user_message(formatted)})

        return context_msgs

    async def _send_reply(
        self,
        message: discord.Message,
        text: str,
        file: discord.File | None = None,
    ) -> None:
        safe_text = redact_secrets(text, self.secrets)
        chunks = split_discord_message(safe_text)
        no_pings = discord.AllowedMentions.none()
        guild_id = message.guild.id if message.guild else 0

        for idx, chunk in enumerate(chunks):
            try:
                if idx == 0:
                    if file is not None:
                        await message.reply(
                            content=chunk,
                            file=file,
                            allowed_mentions=no_pings,
                            mention_author=False,
                        )
                    else:
                        await message.reply(
                            content=chunk,
                            allowed_mentions=no_pings,
                            mention_author=False,
                        )
                else:
                    await message.channel.send(
                        content=chunk,
                        allowed_mentions=no_pings,
                    )
            except Exception as exc:
                logger.error("Failed to send message in guild %d: %s", guild_id, type(exc).__name__)
                break

    async def handle_message(self, message: discord.Message) -> None:
        # 1. Ignore bots, DMs, or empty guild
        if message.author.bot or message.guild is None or self.bot.user is None:
            return

        cfg = self.config_mgr.get()
        guild_id = message.guild.id
        mode = await self.db.get_guild_mode(guild_id, cfg.default_mode)
        if mode == "off":
            return

        # 2. Check mention vs random chat chime-in
        is_mentioned = self.bot.user in message.mentions
        is_random_chime = False

        if not is_mentioned:
            if mode == "mention":
                return
            if mode == "chat":
                if random.random() >= cfg.chat_mode.chance:
                    return
                if not self._check_channel_cooldown(
                    message.channel.id, cfg.chat_mode.channel_cooldown_seconds
                ):
                    return
                is_random_chime = True

        # 3. Apply per-user cooldown
        if not self._check_user_cooldown(message.author.id, cfg.limits.user_cooldown_seconds):
            return

        # 4. Strip mention and check keyword triggers (only on direct @mentions)
        cleaned_text = strip_bot_mention(message.content, self.bot.user.id)
        search_query: str | None = None
        image_request: str | None = None

        if is_mentioned:
            search_query = match_trigger(cleaned_text, cfg.triggers.search)
            if search_query is None:
                image_request = match_trigger(cleaned_text, cfg.triggers.image)

        budget_exceeded = await self.budget.is_exceeded()

        async with message.channel.typing():
            # Handle Image Generation flow
            if image_request is not None:
                await self._handle_image_generation(message, image_request, budget_exceeded)
                return

            # Handle Web Search or Normal Reply
            await self._handle_text_reply(
                message=message,
                cleaned_text=cleaned_text,
                is_random_chime=is_random_chime,
                search_query=search_query,
                budget_exceeded=budget_exceeded,
            )

    async def _handle_image_generation(
        self,
        message: discord.Message,
        image_request: str,
        budget_exceeded: bool,
    ) -> None:
        assert message.guild is not None
        guild_id = message.guild.id
        cfg = self.config_mgr.get()

        if budget_exceeded:
            await self._send_reply(
                message,
                "Image generation is currently paused because today's token budget has been reached. It will automatically resume at the daily reset!",
            )
            return

        if not image_request:
            await self._send_reply(
                message,
                "Please describe what you would like me to generate! For example: `@bot generate an image of a cozy cabin in the snow`.",
            )
            return

        # Step 1: Craft a detailed image prompt using the mention model list
        craft_messages = [
            {"role": "system", "content": IMAGE_PROMPT_SYSTEM_PROMPT},
            {"role": "user", "content": wrap_user_message(image_request)},
        ]
        prompt_resp = await self.llm.chat_completion(
            messages=craft_messages,
            models=cfg.models.mention,
            guild_id=guild_id,
        )
        if prompt_resp is None:
            await self._send_reply(
                message,
                "Sorry, I couldn't prepare an image prompt right now. Please try again later.",
            )
            return

        await self.budget.record_tokens(prompt_resp.tokens_used, images=0)

        # Step 2: Send crafted prompt to image model list
        img_resp = await self.llm.generate_image(
            prompt=prompt_resp.text,
            models=cfg.models.image,
            guild_id=guild_id,
        )
        if img_resp is None:
            await self._send_reply(
                message,
                "Sorry, all image generation models are currently unavailable. Please try again in a bit!",
            )
            return

        await self.budget.record_tokens(img_resp.tokens_used, images=1)

        # Step 3: Upload resulting image as a Discord attachment
        discord_file = discord.File(
            fp=io.BytesIO(img_resp.image_bytes),
            filename="generated.png",
        )
        caption = f"Here is your generated image! *(Prompt: {prompt_resp.text[:250]})*"
        await self._send_reply(message, caption, file=discord_file)

    async def _handle_text_reply(
        self,
        message: discord.Message,
        cleaned_text: str,
        is_random_chime: bool,
        search_query: str | None,
        budget_exceeded: bool,
    ) -> None:
        assert message.guild is not None
        guild_id = message.guild.id
        cfg = self.config_mgr.get()

        # Check for image attachments
        image_urls = await extract_image_data_urls(message.attachments, cfg)
        has_images = len(image_urls) > 0

        # Select model list according to FR-17, FR-26, FR-31
        if budget_exceeded:
            base_models = cfg.models.low_budget
        elif is_random_chime:
            base_models = cfg.models.chat
        else:
            base_models = cfg.models.mention

        if has_images and cfg.models.vision:
            seen: set[str] = set()
            selected_models: list[str] = []
            for m in cfg.models.vision + base_models:
                if m not in seen:
                    seen.add(m)
                    selected_models.append(m)
        else:
            selected_models = list(base_models)

        # Perform web search if triggered
        search_block = ""
        search_failed_notice = ""
        if search_query is not None:
            if not search_query:
                await self._send_reply(
                    message,
                    "Please provide a search query after the trigger keyword!",
                )
                return

            results = await self.search_client.search(search_query)
            if results is None:
                search_failed_notice = (
                    "[System Note: Live web search via SearXNG failed or timed out. "
                    "Inform the user that live search is currently unavailable and answer from your own knowledge, "
                    "clearly stating that no live web search was performed.]"
                )
            elif not results:
                search_block = wrap_search_results(
                    f"No web results found for query: {search_query}"
                )
            else:
                formatted_items = []
                for idx, r in enumerate(results, start=1):
                    formatted_items.append(
                        f"[{idx}] Title: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}"
                    )
                search_block = (
                    wrap_search_results("\n\n".join(formatted_items))
                    + "\n[Instruction: Answer the user's query using the search results above and cite your sources by URL.]"
                )

        # Build message chain
        personality = self.instructions.get_instructions(guild_id, message.guild.name)
        system_content = build_system_prompt(personality)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

        context_messages = await self._build_context_messages(
            message,
            limit=cfg.context.messages,
            max_chars=cfg.context.max_chars_per_message,
        )
        messages.extend(context_messages)

        user_text = cleaned_text or "(uploaded image)"
        if len(user_text) > cfg.context.max_chars_per_message:
            user_text = user_text[: cfg.context.max_chars_per_message] + "..."

        wrapped_trigger = wrap_user_message(f"{message.author.display_name}: {user_text}")
        if search_block:
            wrapped_trigger = f"{wrapped_trigger}\n\n{search_block}"
        if search_failed_notice:
            wrapped_trigger = f"{wrapped_trigger}\n\n{search_failed_notice}"

        if has_images:
            content_parts: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": f"{wrapped_trigger}\n<image_context>User attached {len(image_urls)} image(s).</image_context>",
                }
            ]
            for data_url in image_urls:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    }
                )
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": wrapped_trigger})

        resp = await self.llm.chat_completion(
            messages=messages,
            models=selected_models,
            guild_id=guild_id,
        )
        if resp is None:
            await self._send_reply(
                message,
                "Sorry, I'm having trouble reaching my language models right now. Please try again in a moment!",
            )
            return

        await self.budget.record_tokens(resp.tokens_used, images=0)
        await self._send_reply(message, resp.text)
