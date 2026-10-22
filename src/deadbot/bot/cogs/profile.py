import asyncio
import io
import logging

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.bot.embeds import profile_embed
from deadbot.services.card import render_card
from deadbot.services.errors import UserFacingError

log = logging.getLogger(__name__)


class Profile(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    async def card_file(self, account_id: int) -> discord.File:
        app = self.bot.app
        report = await app.profile.build(account_id)
        try:
            mates = await app.teammates.build(account_id)
        except UserFacingError:
            mates = None
        avatar = None
        if report.avatar_url:
            try:
                r = await app.http.get(report.avatar_url, timeout=10)
                avatar = r.content if r.is_success else None
            except httpx.HTTPError:
                log.info("avatar fetch failed for %s", account_id)
        png = await asyncio.to_thread(render_card, report, mates, avatar)
        return discord.File(fp=io.BytesIO(png), filename=f"deadlock-{account_id}.png")

    @app_commands.command(name="profile", description="Playstyle summary for a linked player")
    @app_commands.describe(user="Whose profile (defaults to you)", as_image="Send as an image card")
    @app_commands.checks.cooldown(5, 60.0)
    async def profile(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        as_image: bool = False,
    ) -> None:
        target = user or interaction.user
        # Resolve before deferring so "not linked" stays ephemeral and instant.
        account_id = await self.bot.app.linking.account_for(target.id, self_lookup=user is None)
        await interaction.response.defer(thinking=True)
        if as_image:
            await interaction.followup.send(file=await self.card_file(account_id))
            return
        report = await self.bot.app.profile.build(account_id)
        await interaction.followup.send(embed=profile_embed(report))

    @app_commands.command(name="card", description="Shareable image of a profile and top teammates")
    @app_commands.describe(user="Whose card (defaults to you)")
    @app_commands.checks.cooldown(3, 60.0)
    async def card(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        target = user or interaction.user
        account_id = await self.bot.app.linking.account_for(target.id, self_lookup=user is None)
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(file=await self.card_file(account_id))


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Profile(bot))
