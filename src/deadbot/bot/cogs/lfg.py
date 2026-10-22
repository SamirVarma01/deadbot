import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks

from deadbot.bot.client import DeadBot
from deadbot.bot.common import require_guild
from deadbot.bot.lfg_ui import lfg_embed, lfg_view, render_post, suggestions
from deadbot.data.db import guilds
from deadbot.services.errors import UserFacingError

log = logging.getLogger(__name__)


class Lfg(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot
        self.expire_loop.start()

    async def cog_unload(self) -> None:
        self.expire_loop.cancel()

    @app_commands.command(
        name="lfg", description="Post a looking-for-group call; I'll suggest fits"
    )
    @app_commands.describe(
        size="How many more players you need",
        mode="Ranked or casual",
        note="Anything people should know (heroes, voice, vibe)",
        expires_in="Minutes until the post closes",
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(2, 60.0)
    async def lfg(
        self,
        interaction: discord.Interaction,
        size: app_commands.Range[int, 1, 5],
        mode: Literal["ranked", "casual"] = "ranked",
        note: app_commands.Range[str, 1, 200] | None = None,
        expires_in: app_commands.Range[int, 10, 240] | None = None,
    ) -> None:
        app = self.bot.app
        guild = require_guild(interaction)
        await app.linking.account_for(interaction.user.id, self_lookup=True)
        async with app.sessions() as s:
            cfg = await guilds.get_config(s, guild.id)
        channel = (guild.get_channel(cfg.lfg_channel_id) if cfg.lfg_channel_id else None) or (
            interaction.channel
        )
        if not isinstance(channel, discord.TextChannel | discord.Thread | discord.VoiceChannel):
            raise UserFacingError("I can't post LFG messages in this kind of channel.")
        if not channel.permissions_for(guild.me).send_messages:
            raise UserFacingError(f"I don't have permission to post in {channel.mention}.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        state = await app.lfg.create(
            guild_id=guild.id,
            channel_id=channel.id,
            owner_id=interaction.user.id,
            slots=size,
            mode=mode,
            note=note,
            expires_minutes=expires_in or self.bot.settings.lfg_default_expiry_minutes,
        )
        try:
            picks = await suggestions(self.bot, guild, state)
            pinged = await app.recommend.claim_pings(
                [p.player.discord_id for p in picks if p.player.discord_id],
                self.bot.settings.lfg_max_pings,
            )
            role = guild.get_role(cfg.ping_role_id) if cfg.ping_role_id else None
            content = " ".join(
                [f"<@{interaction.user.id}> is looking for {size} ({mode})."]
                + ([role.mention] if role else [])
                + [f"<@{d}>" for d in pinged]
            )
            message = await channel.send(
                content,
                embed=lfg_embed(state, picks),
                view=lfg_view(state),
                allowed_mentions=discord.AllowedMentions(
                    users=[discord.Object(d) for d in pinged],
                    roles=[role] if role else False,
                ),
            )
        except Exception:
            await app.lfg.close(state.post.id, interaction.user.id)
            raise
        await app.lfg.attach_message(state.post.id, message.id)
        await interaction.followup.send(f"Posted: {message.jump_url}", ephemeral=True)

    @tasks.loop(minutes=1)
    async def expire_loop(self) -> None:
        try:
            for state in await self.bot.app.lfg.expire_due():
                await render_post(self.bot, state)
        except Exception:
            log.exception("LFG expiry sweep failed")

    @expire_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Lfg(bot))
