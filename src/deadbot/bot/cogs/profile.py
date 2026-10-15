import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.bot.embeds import profile_embed


class Profile(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    @app_commands.command(name="profile", description="Playstyle summary for a linked player")
    @app_commands.describe(user="Whose profile (defaults to you)")
    @app_commands.checks.cooldown(5, 60.0)
    async def profile(
        self, interaction: discord.Interaction, user: discord.User | None = None
    ) -> None:
        target = user or interaction.user
        # Resolve before deferring so "not linked" stays ephemeral and instant.
        account_id = await self.bot.app.linking.account_for(target.id, self_lookup=user is None)
        await interaction.response.defer(thinking=True)
        report = await self.bot.app.profile.build(account_id)
        await interaction.followup.send(embed=profile_embed(report))


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Profile(bot))
