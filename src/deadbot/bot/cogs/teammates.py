import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.bot.embeds import teammates_embed
from deadbot.services.teammates import TeammateSort


class Teammates(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    @app_commands.command(name="teammates", description="Who you've played with, and how it went")
    @app_commands.describe(
        user="Whose teammates (defaults to you)",
        sort="Order by games together, synergy, or most recent",
        min_games="Only count teammates with at least this many games together",
    )
    @app_commands.checks.cooldown(5, 60.0)
    async def teammates(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        sort: TeammateSort = TeammateSort.GAMES,
        min_games: app_commands.Range[int, 1, 500] | None = None,
    ) -> None:
        target = user or interaction.user
        account_id = await self.bot.app.linking.account_for(target.id, self_lookup=user is None)
        await interaction.response.defer(thinking=True)
        report = await self.bot.app.teammates.build(account_id, sort, min_games)
        await interaction.followup.send(
            embed=teammates_embed(report, interaction.guild),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Teammates(bot))
