from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.data.db import repo
from deadbot.data.db.models import User
from deadbot.services.errors import NotLinkedError

REGIONS = Literal["na", "eu", "sa", "asia", "oce", "any"]


def describe(u: User) -> str:
    av = u.availability or {}
    if "start" in av:
        off = int(av.get("utc_offset", 0))
        hours = f"{av['start']:02d}:00–{av['end']:02d}:00 (UTC{off:+d})"
    else:
        hours = "from your match history"
    return (
        f"**Steam link:** {'verified ✅' if u.verified else 'unverified (use /verify)'}\n"
        f"**Suggested to others:** {'yes' if u.opt_in else 'no'}\n"
        f"**LFG pings:** {'yes' if u.allow_pings else 'no'}\n"
        f"**Preferred mode:** {u.preferred_mode or 'any'}\n"
        f"**Region:** {(u.region or 'any').upper()}\n"
        f"**Play hours:** {hours}"
    )


class SettingsCog(commands.GroupCog, group_name="settings"):
    """Your matching preferences. All replies are private."""

    def __init__(self, bot: DeadBot):
        self.bot = bot
        super().__init__()

    async def _update(self, interaction: discord.Interaction, **fields: Any) -> None:
        async with self.bot.app.sessions.begin() as s:
            user = await repo.update_user(s, interaction.user.id, **fields)
            if user is None:
                raise NotLinkedError("Link your Steam account with `/link` first.")
            text = describe(user)
        await interaction.response.send_message(f"Saved.\n\n{text}", ephemeral=True)

    @app_commands.command(name="view", description="Show your current settings")
    async def view(self, interaction: discord.Interaction) -> None:
        async with self.bot.app.sessions() as s:
            user = await repo.get_user(s, interaction.user.id)
        if user is None:
            raise NotLinkedError("Link your Steam account with `/link` first.")
        await interaction.response.send_message(describe(user), ephemeral=True)

    @app_commands.command(name="suggestions", description="Appear in /lfg and /fill suggestions")
    async def suggestions(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self._update(interaction, opt_in=enabled)

    @app_commands.command(name="pings", description="Allow LFG posts to ping you")
    async def pings(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self._update(interaction, allow_pings=enabled)

    @app_commands.command(name="mode", description="Which queue you usually want")
    async def mode(
        self, interaction: discord.Interaction, mode: Literal["ranked", "casual", "any"]
    ) -> None:
        await self._update(interaction, preferred_mode=None if mode == "any" else mode)

    @app_commands.command(name="region", description="Your server region")
    async def region(self, interaction: discord.Interaction, region: REGIONS) -> None:
        await self._update(interaction, region=None if region == "any" else region)

    @app_commands.command(
        name="availability",
        description="When you usually play (local time). Leave empty to use match history.",
    )
    @app_commands.describe(
        start_hour="Usually start at (0-23, your local time)",
        end_hour="Usually stop at (0-23, your local time)",
        utc_offset="Your UTC offset, e.g. -5 for New York in winter, 1 for Berlin",
    )
    async def availability(
        self,
        interaction: discord.Interaction,
        start_hour: app_commands.Range[int, 0, 23] | None = None,
        end_hour: app_commands.Range[int, 0, 23] | None = None,
        utc_offset: app_commands.Range[int, -12, 14] = 0,
    ) -> None:
        if start_hour is None or end_hour is None:
            await self._update(interaction, availability={})
            return
        await self._update(
            interaction,
            availability={"start": start_hour, "end": end_hour, "utc_offset": utc_offset},
        )


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(SettingsCog(bot))
