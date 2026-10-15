import logging

import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot

log = logging.getLogger(__name__)


class Linking(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    @app_commands.command(name="link", description="Link your Steam account to the bot")
    @app_commands.describe(
        account="Steam profile URL, custom URL name, or SteamID64",
    )
    @app_commands.checks.cooldown(3, 60.0)
    async def link(self, interaction: discord.Interaction, account: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.app.linking.link(interaction.user.id, account)
        name = result.persona_name or f"[U:1:{result.account_id}]"
        base = (
            f"✅ Linked to **{name}** ({result.matches:,} ranked/unranked matches found).\n"
            "Importing your match history and teammates now…"
        )
        await interaction.edit_original_response(content=base)
        self.bot.spawn(self._sync_then_report(interaction, result.account_id, base))

    async def _sync_then_report(
        self, interaction: discord.Interaction, account_id: int, base: str
    ) -> None:
        try:
            await self.bot.app.sync.sync_account(account_id)
            status = "Import done. Try `/profile` and `/teammates`."
        except Exception:
            status = "⚠️ The import hit a snag; it will retry automatically within the hour."
        try:
            await interaction.edit_original_response(
                content=base.replace("Importing your match history and teammates now…", status)
            )
        except discord.HTTPException:  # interaction token expired (15 min) or message gone
            log.info("could not update /link message for %s", account_id)

    @app_commands.command(name="unlink", description="Remove your linked Steam account")
    async def unlink(self, interaction: discord.Interaction) -> None:
        removed = await self.bot.app.linking.unlink(interaction.user.id)
        await interaction.response.send_message(
            "Unlinked. You won't be suggested to anyone." if removed else "You weren't linked.",
            ephemeral=True,
        )


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Linking(bot))
