import logging
import discord
from discord import app_commands
from bot.db import Database

logger = logging.getLogger("bot.commands")


def register_commands(tree: app_commands.CommandTree, db: Database) -> None:
    async def _set_mode(interaction: discord.Interaction, mode: str, description: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the **Manage Server** permission to change the bot mode.",
                ephemeral=True,
            )
            return

        await db.set_guild_mode(interaction.guild.id, mode)
        logger.info("Guild %d mode set to '%s'", interaction.guild.id, mode)
        await interaction.response.send_message(
            f"Server mode set to **{mode}** ({description}).",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tree.command(name="off", description="Disable all bot replies in this server.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def off_cmd(interaction: discord.Interaction) -> None:
        await _set_mode(interaction, "off", "bot will ignore all messages")

    @tree.command(name="mention", description="Bot replies only when @mentioned.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def mention_cmd(interaction: discord.Interaction) -> None:
        await _set_mode(interaction, "mention", "bot replies only when @mentioned")

    @tree.command(name="chat", description="Bot replies to @mentions and randomly chimes in.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def chat_cmd(interaction: discord.Interaction) -> None:
        await _set_mode(interaction, "chat", "bot replies to @mentions and randomly chimes in")
