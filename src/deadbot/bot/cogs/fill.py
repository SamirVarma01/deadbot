from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.bot.common import human_member_ids, presence, recommendation_lines, require_guild
from deadbot.bot.embeds import COLOR
from deadbot.services.errors import UserFacingError
from deadbot.services.lfg import MAX_PARTY


class Fill(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    def _party_from_voice(self, member: discord.Member) -> list[int] | None:
        if member.voice and member.voice.channel:
            others = [m.id for m in member.voice.channel.members if not m.bot and m != member]
            return [member.id, *others]
        return None

    @app_commands.command(name="fill", description="Who should join your voice channel or party?")
    @app_commands.describe(
        count="How many suggestions (defaults to open slots)",
        mode="Ranked or casual (filters out people who prefer the other)",
        share="Post the suggestions publicly",
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(3, 60.0)
    async def fill(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 5] | None = None,
        mode: Literal["ranked", "casual"] | None = None,
        share: bool = False,
    ) -> None:
        app = self.bot.app
        guild = require_guild(interaction)
        member = guild.get_member(interaction.user.id)
        party_ids = self._party_from_voice(member) if member else None
        source = "your voice channel"
        if party_ids is None:
            state = await app.lfg.active_for(guild.id, interaction.user.id)
            if state:
                party_ids, source = state.members, f"LFG post #{state.post.id}"
                mode = mode or state.post.mode
            else:
                party_ids, source = [interaction.user.id], "just you"
        open_slots = MAX_PARTY - len(party_ids)
        if open_slots <= 0:
            raise UserFacingError("Your party is already full (6/6).")

        await interaction.response.defer(ephemeral=not share, thinking=True)
        party, picks = await app.recommend.recommend(
            guild_id=guild.id,
            member_ids=human_member_ids(guild),
            party_ids=party_ids,
            slots=min(count or open_slots, open_slots),
            mode=mode,
            online=presence(guild, self.bot.settings.enable_presence_intent),
        )
        e = discord.Embed(title="Who to add", color=COLOR)
        unlinked = f" · {len(party.unlinked)} not linked" if party.unlinked else ""
        e.description = f"Party from {source}: {len(party_ids)}/{MAX_PARTY}{unlinked}\n\n" + (
            recommendation_lines(picks)
            or "No eligible players right now. Suggestions only include server members who "
            "linked (and verified, if this server requires it) and haven't opted out."
        )
        e.set_footer(text="Fit blends synergy, hero-type gaps, play hours, and rank.")
        await interaction.followup.send(embed=e, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Fill(bot))
