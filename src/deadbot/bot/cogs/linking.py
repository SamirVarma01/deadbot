import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.services.errors import UserFacingError
from deadbot.services.linking import LinkResult

log = logging.getLogger(__name__)


def verify_instructions(code: str, ttl_minutes: int) -> str:
    return (
        f"**Verify you own it:** add `{code}` anywhere in your Steam **profile name** or "
        f"**About me**, save, then press **Verify** (or run `/verify`). The code is valid for "
        f"{ttl_minutes} minutes; remove it afterwards."
    )


def link_message(r: LinkResult, ttl: int) -> str:
    name = r.persona_name or f"[U:1:{r.account_id}]"
    if not r.linked:
        return (
            f"**{name}** is already linked to another Discord user. If it's yours, verifying "
            f"will move it to you.\n\n" + verify_instructions(r.code or "", ttl)
        )
    head = f"✅ Linked to **{name}** ({r.matches:,} ranked/unranked matches found)."
    if r.verified:
        return head + " Verified."
    return (
        head
        + "\n\nUnverified links work for `/profile` and `/teammates`, but servers can hide you "
        "from LFG suggestions until you verify.\n" + verify_instructions(r.code or "", ttl)
    )


class VerifyButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"deadbot:verify:(?P<uid>\d+)"
):
    def __init__(self, user_id: int):
        super().__init__(
            discord.ui.Button(
                label="Verify",
                style=discord.ButtonStyle.primary,
                custom_id=f"deadbot:verify:{user_id}",
            )
        )
        self.user_id = user_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ) -> "VerifyButton":
        return cls(int(match["uid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("Linking")  # type: ignore[union-attr]
        await cog.run_verify(interaction)


class Linking(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    @app_commands.command(name="link", description="Link your Steam account to the bot")
    @app_commands.describe(account="Steam profile URL, custom URL name, or SteamID64")
    @app_commands.checks.cooldown(3, 60.0)
    async def link(self, interaction: discord.Interaction, account: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.app.linking.link(interaction.user.id, account)
        ttl = self.bot.settings.verify_code_ttl_minutes
        view = None
        if result.code:
            view = discord.ui.View(timeout=None)
            view.add_item(VerifyButton(interaction.user.id))
        text = link_message(result, ttl)
        if result.linked:
            text += "\n\n⏳ Importing your match history and teammates…"
        await interaction.edit_original_response(content=text, view=view)
        if result.linked:
            self.bot.spawn(self._sync_then_report(interaction, result.account_id, text, view))

    async def _sync_then_report(
        self,
        interaction: discord.Interaction,
        account_id: int,
        text: str,
        view: discord.ui.View | None,
    ) -> None:
        try:
            await self.bot.app.sync.sync_account(account_id)
            status = "✅ Import done. Try `/profile` and `/teammates`."
        except Exception:
            status = "⚠️ The import hit a snag; it will retry automatically within the hour."
        try:
            await interaction.edit_original_response(
                content=text.replace("⏳ Importing your match history and teammates…", status),
                view=view,
            )
        except discord.HTTPException:  # interaction token expired (15 min) or message gone
            log.info("could not update /link message for %s", account_id)

    async def run_verify(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.bot.app.linking.verify(interaction.user.id)
        except UserFacingError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        if result.account_changed:
            self.bot.spawn(self.bot.app.sync.sync_account(result.account_id))
        moved = " It was moved from the previous holder." if result.evicted_discord_id else ""
        await interaction.followup.send(
            f"✅ Verified!{moved} You can remove the code from your Steam profile now.",
            ephemeral=True,
        )

    @app_commands.command(name="verify", description="Check your Steam profile for your code")
    @app_commands.checks.cooldown(3, 60.0)
    async def verify(self, interaction: discord.Interaction) -> None:
        await self.run_verify(interaction)

    @app_commands.command(name="unlink", description="Remove your linked Steam account")
    async def unlink(self, interaction: discord.Interaction) -> None:
        removed = await self.bot.app.linking.unlink(interaction.user.id)
        await interaction.response.send_message(
            "Unlinked. You won't be suggested to anyone." if removed else "You weren't linked.",
            ephemeral=True,
        )


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Linking(bot))
