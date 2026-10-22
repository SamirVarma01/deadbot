import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.bot.common import presence
from deadbot.bot.embeds import COLOR, hour_timestamp, pct, signed_pct
from deadbot.services.errors import UserFacingError
from deadbot.services.matching import SUBRANKS_PER_TIER, PartyFit


def synergy_embed(fit: PartyFit, unlinked: list[int]) -> discord.Embed:
    e = discord.Embed(title=f"Party fit: {fit.score * 100:.0f}/100", color=COLOR)
    lines = []
    for line in sorted(fit.pairs, key=lambda p: -(p.info.games if p.info else 0))[:15]:
        pair = f"<@{line.a.discord_id}> + <@{line.b.discord_id}>"
        if line.info:
            lines.append(
                f"{pair}: {line.info.games} games · synergy {signed_pct(line.info.synergy)}"
            )
        else:
            lines.append(f"{pair}: no games together yet")
    e.add_field(
        name=f"Pairs · avg synergy {signed_pct(fit.synergy)}",
        value="\n".join(lines)[:1024] or "-",
        inline=False,
    )
    if fit.role_mix:
        mix = " · ".join(f"{t.title()} {pct(s)}" for t, s in fit.role_mix.items() if s > 0)
        e.add_field(name=f"Hero types · balance {pct(fit.role_balance)}", value=mix, inline=False)
    if fit.hour_overlap is not None and fit.best_hour_utc is not None:
        e.add_field(
            name="Schedules",
            value=f"{pct(fit.hour_overlap)} overlap · best time together "
            f"{hour_timestamp(fit.best_hour_utc)}",
            inline=False,
        )
    if fit.rank_spread is not None:
        e.add_field(
            name="Rank spread",
            value=f"{fit.rank_spread / SUBRANKS_PER_TIER:.1f} tiers between highest and lowest",
            inline=False,
        )
    if unlinked:
        e.set_footer(text=f"{len(unlinked)} player(s) not linked and left out of the score.")
    return e


class Synergy(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    @app_commands.command(name="synergy", description="How well would this group play together?")
    @app_commands.describe(include_me="Include yourself in the group")
    @app_commands.checks.cooldown(5, 60.0)
    async def synergy(
        self,
        interaction: discord.Interaction,
        player1: discord.User,
        player2: discord.User | None = None,
        player3: discord.User | None = None,
        player4: discord.User | None = None,
        player5: discord.User | None = None,
        include_me: bool = True,
    ) -> None:
        picked = [player1, player2, player3, player4, player5]
        users = ([interaction.user] if include_me else []) + [u for u in picked if u]
        ids = list(dict.fromkeys(u.id for u in users if not u.bot))
        if len(ids) < 2:
            raise UserFacingError("Pick at least two different people (bots don't count).")

        await interaction.response.defer(thinking=True)
        online = (
            presence(interaction.guild, self.bot.settings.enable_presence_intent)
            if interaction.guild
            else {}
        )
        party, fit = await self.bot.app.recommend.evaluate(ids, online)
        if len(party.members) < 2:
            raise UserFacingError("At least two of those players need to `/link` first.")
        await interaction.followup.send(
            embed=synergy_embed(fit, party.unlinked),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Synergy(bot))
