import asyncio
import logging
import os
import sys
import discord
from discord import app_commands
import httpx

from bot.budget import BudgetTracker
from bot.commands import register_commands
from bot.config import ConfigManager
from bot.db import Database
from bot.events import MessageHandler
from bot.instructions import InstructionManager
from bot.llm import HackClubLLMClient
from bot.search import SearXNGClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot.main")


class AIDiscordBot(discord.Client):
    def __init__(
        self,
        config_mgr: ConfigManager,
        db: Database,
        instructions: InstructionManager,
        http_client: httpx.AsyncClient,
        api_key: str,
        discord_token: str,
    ) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True

        super().__init__(
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.tree = app_commands.CommandTree(self)
        self.config_mgr = config_mgr
        self.db = db
        self.instructions = instructions
        self.http_client = http_client

        self.budget = BudgetTracker(db, config_mgr)
        self.llm = HackClubLLMClient(api_key, config_mgr, http_client)
        self.search_client = SearXNGClient(config_mgr, http_client)
        self.handler = MessageHandler(
            bot=self,
            config_mgr=config_mgr,
            db=db,
            budget=self.budget,
            instructions=instructions,
            llm=self.llm,
            search_client=self.search_client,
            secrets=[api_key, discord_token],
        )
        register_commands(self.tree, self.db)

    async def setup_hook(self) -> None:
        await self.db.connect()
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d global slash commands.", len(synced))
        except Exception as exc:
            logger.error("Failed to sync slash commands: %s", exc)

    async def close(self) -> None:
        await self.http_client.aclose()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info("Logged in as %s (ID: %d) in %d guilds.", self.user, self.user.id, len(self.guilds))
        default_mode = self.config_mgr.get().default_mode
        for guild in self.guilds:
            try:
                await self.db.get_guild_mode(guild.id, default_mode)
                self.instructions.ensure_guild_file(guild.id, guild.name)
            except Exception as exc:
                logger.error("Error initializing guild %d: %s", guild.id, exc)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        default_mode = self.config_mgr.get().default_mode
        logger.info("Joined guild %d; initializing with default mode '%s'.", guild.id, default_mode)
        await self.db.set_guild_mode(guild.id, default_mode)
        self.instructions.ensure_guild_file(guild.id, guild.name)

    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if before.name != after.name:
            self.instructions.ensure_guild_file(after.id, after.name)

    async def on_message(self, message: discord.Message) -> None:
        try:
            await self.handler.handle_message(message)
        except Exception as exc:
            logger.error("Unhandled exception in on_message: %s", type(exc).__name__)


async def main() -> None:
    discord_token = os.environ.get("DISCORD_TOKEN", "").strip()
    hackclub_api_key = os.environ.get("HACKCLUB_API_KEY", "").strip()

    if not discord_token or not hackclub_api_key:
        logger.critical("Missing DISCORD_TOKEN or HACKCLUB_API_KEY in environment (.env). Exiting.")
        sys.exit(1)

    try:
        config_mgr = ConfigManager()
    except Exception as exc:
        logger.critical("Failed to validate config.json on startup: %s", exc)
        sys.exit(1)

    db = Database()
    instructions = InstructionManager(config_mgr)
    http_client = httpx.AsyncClient(follow_redirects=False)

    bot = AIDiscordBot(
        config_mgr=config_mgr,
        db=db,
        instructions=instructions,
        http_client=http_client,
        api_key=hackclub_api_key,
        discord_token=discord_token,
    )

    async with bot:
        await bot.start(discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
