import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from discord.ext import commands

from deadbot.app import AppContext
from deadbot.config import Settings
from deadbot.jobs.scheduler import start_scheduler
from deadbot.services.errors import UserFacingError

log = logging.getLogger(__name__)

EXTENSIONS = (
    "deadbot.bot.cogs.linking",
    "deadbot.bot.cogs.profile",
    "deadbot.bot.cogs.teammates",
    "deadbot.bot.cogs.settings",
    "deadbot.bot.cogs.setup",
    "deadbot.bot.cogs.lfg",
    "deadbot.bot.cogs.fill",
    "deadbot.bot.cogs.synergy",
)

GENERIC_ERROR = "Something went wrong on our side. Please try again in a bit."


def make_intents(settings: Settings) -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True  # privileged: find linked teammates in this server
    intents.voice_states = True  # Phase 2 /fill reads voice channel members
    intents.presences = settings.enable_presence_intent  # privileged, optional "online now"
    return intents


async def send_error(interaction: discord.Interaction, message: str) -> None:
    """Errors are always ephemeral, even if the command already deferred publicly."""
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
            return
        original = await interaction.original_response()
        if not original.flags.ephemeral:
            await interaction.delete_original_response()
        await interaction.followup.send(message, ephemeral=True)
    except discord.HTTPException:
        log.warning("could not deliver error message to interaction %s", interaction.id)


class DeadBot(commands.Bot):
    app: AppContext
    scheduler: AsyncIOScheduler

    def __init__(self, settings: Settings):
        super().__init__(command_prefix=commands.when_mentioned, intents=make_intents(settings))
        self.settings = settings
        self._background: set[asyncio.Task[Any]] = set()

    async def setup_hook(self) -> None:
        self.app = AppContext.create(self.settings)
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        # Persistent buttons: routed by custom_id, so they keep working after restarts.
        from deadbot.bot.cogs.linking import VerifyButton
        from deadbot.bot.lfg_ui import LfgButton

        self.add_dynamic_items(VerifyButton, LfgButton)
        self.tree.on_error = self.on_app_command_error

        if self.settings.dev_guild_id:
            guild = discord.Object(self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("synced %d app commands", len(synced))

        self.scheduler = start_scheduler(self.app)

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        """Fire-and-forget with a strong reference so the task isn't garbage collected."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def on_ready(self) -> None:
        log.info("logged in as %s in %d guild(s)", self.user, len(self.guilds))

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, UserFacingError):
            await send_error(interaction, str(original))
            return
        if isinstance(error, app_commands.CommandOnCooldown):
            await send_error(interaction, f"Slow down! Try again in {error.retry_after:.0f}s.")
            return
        log.error(
            "command %s failed", interaction.command and interaction.command.name, exc_info=original
        )
        await send_error(interaction, GENERIC_ERROR)

    async def close(self) -> None:
        if getattr(self, "scheduler", None):
            self.scheduler.shutdown(wait=False)
        for task in list(self._background):
            task.cancel()
        if getattr(self, "app", None):
            await self.app.aclose()
        await super().close()
