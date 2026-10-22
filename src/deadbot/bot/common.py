"""Small Discord helpers shared by cogs."""

import discord

from deadbot.services.errors import UserFacingError
from deadbot.services.matching import Recommendation


def require_guild(interaction: discord.Interaction) -> discord.Guild:
    if interaction.guild is None:
        raise UserFacingError("This command only works inside a server.")
    return interaction.guild


def human_member_ids(guild: discord.Guild) -> list[int]:
    return [m.id for m in guild.members if not m.bot]


def presence(guild: discord.Guild, enabled: bool) -> dict[int, bool | None]:
    """discord id -> online? Empty (unknown) unless the presence intent is enabled."""
    if not enabled:
        return {}
    return {m.id: m.status is not discord.Status.offline for m in guild.members if not m.bot}


def recommendation_lines(picks: list[Recommendation], limit: int = 10) -> str:
    return "\n".join(
        f"`{i}.` <@{r.player.discord_id}> · **{r.fit.score * 100:.0f}** fit\n      {r.fit.reason}"
        for i, r in enumerate(picks[:limit], 1)
    )
