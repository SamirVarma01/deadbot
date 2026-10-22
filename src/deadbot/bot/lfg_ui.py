"""LFG post rendering and its restart-safe buttons."""

import logging
import re
from typing import TYPE_CHECKING, ClassVar

import discord

from deadbot.bot.common import human_member_ids, presence, recommendation_lines
from deadbot.bot.embeds import COLOR
from deadbot.data.db.models import LfgStatus
from deadbot.services.errors import UserFacingError
from deadbot.services.lfg import LfgState
from deadbot.services.matching import Recommendation

if TYPE_CHECKING:
    from deadbot.bot.client import DeadBot

log = logging.getLogger(__name__)

_STATUS_TEXT = {
    LfgStatus.FULL: "✅ Party full. Have fun!",
    LfgStatus.CLOSED: "Closed by the owner.",
    LfgStatus.EXPIRED: "⌛ Expired.",
}


def lfg_embed(state: LfgState, picks: list[Recommendation]) -> discord.Embed:
    post = state.post
    active = post.status == LfgStatus.OPEN
    title = f"LFG · {post.mode.title()} · {len(state.members)}/{post.capacity}"
    e = discord.Embed(
        title=title, color=COLOR if active else discord.Color.dark_grey(), description=post.note
    )
    e.add_field(
        name="Party",
        value="\n".join(
            f"<@{m}>" + (" (host)" if m == post.owner_id else "") for m in state.members
        ),
        inline=True,
    )
    if active:
        e.add_field(
            name="Open slots",
            value=f"{state.open_slots}\nExpires <t:{int(post.expires_at.timestamp())}:R>",
            inline=True,
        )
        if picks:
            e.add_field(name="Suggested", value=recommendation_lines(picks)[:1024], inline=False)
    else:
        e.add_field(name="Status", value=_STATUS_TEXT.get(post.status, post.status), inline=True)
    e.set_footer(text=f"Post #{post.id} · /settings to opt out of suggestions")
    return e


class LfgButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"deadbot:lfg:(?P<action>join|leave|close):(?P<post>\d+)",
):
    LABELS: ClassVar[dict[str, tuple[str, discord.ButtonStyle]]] = {
        "join": ("Join", discord.ButtonStyle.success),
        "leave": ("Leave", discord.ButtonStyle.secondary),
        "close": ("Close", discord.ButtonStyle.danger),
    }

    def __init__(self, action: str, post_id: int, *, disabled: bool = False):
        label, style = self.LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"deadbot:lfg:{action}:{post_id}",
                disabled=disabled,
            )
        )
        self.action = action
        self.post_id = post_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ) -> "LfgButton":
        return cls(match["action"], int(match["post"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: DeadBot = interaction.client  # type: ignore[assignment]
        lfg = bot.app.lfg
        try:
            if self.action == "join":
                state, became_full = await lfg.join(self.post_id, interaction.user.id)
            elif self.action == "leave":
                state, became_full = await lfg.leave(self.post_id, interaction.user.id), False
            else:
                state, became_full = await lfg.close(self.post_id, interaction.user.id), False
        except UserFacingError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.defer()
        await render_post(bot, state, message=interaction.message)
        if became_full:
            await interaction.followup.send(
                "Party's full! " + " ".join(f"<@{m}>" for m in state.members) + " Queue up!",
                allowed_mentions=discord.AllowedMentions(users=True),
            )


def lfg_view(state: LfgState) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    active = state.post.status in (LfgStatus.OPEN, LfgStatus.FULL)
    for action in ("join", "leave", "close"):
        disabled = not active or (action == "join" and state.post.status != LfgStatus.OPEN)
        view.add_item(LfgButton(action, state.post.id, disabled=disabled))
    return view


async def suggestions(
    bot: "DeadBot", guild: discord.Guild, state: LfgState
) -> list[Recommendation]:
    if state.post.status != LfgStatus.OPEN or state.open_slots == 0:
        return []
    _, picks = await bot.app.recommend.recommend(
        guild_id=guild.id,
        member_ids=human_member_ids(guild),
        party_ids=state.members,
        slots=min(state.open_slots, bot.settings.lfg_suggestions),
        mode=state.post.mode,
        online=presence(guild, bot.settings.enable_presence_intent),
    )
    return picks


async def render_post(
    bot: "DeadBot", state: LfgState, *, message: discord.Message | None = None
) -> None:
    """Re-render the post (suggestions are recomputed for the current party)."""
    guild = bot.get_guild(state.post.guild_id)
    if guild is None:
        return
    picks = await suggestions(bot, guild, state)
    try:
        if message is None:
            channel = guild.get_channel(state.post.channel_id)
            if not isinstance(channel, discord.abc.Messageable) or not state.post.message_id:
                return
            message = await channel.fetch_message(state.post.message_id)
        await message.edit(embed=lfg_embed(state, picks), view=lfg_view(state))
    except discord.HTTPException as e:
        log.info("could not update LFG post %s: %s", state.post.id, e)
