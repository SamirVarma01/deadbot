from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from deadbot.bot.client import DeadBot
from deadbot.bot.common import require_guild
from deadbot.data.db import guilds
from deadbot.data.db.models import GuildConfig


def describe(cfg: GuildConfig) -> str:
    channel = f"<#{cfg.lfg_channel_id}>" if cfg.lfg_channel_id else "wherever /lfg is used"
    role = f"<@&{cfg.ping_role_id}>" if cfg.ping_role_id else "none"
    verified = "verified links only" if cfg.require_verified else "any linked member"
    return f"**LFG channel:** {channel}\n**Ping role:** {role}\n**Suggest:** {verified}"


class Setup(commands.Cog):
    def __init__(self, bot: DeadBot):
        self.bot = bot

    @app_commands.command(name="setup", description="Configure deadbot for this server")
    @app_commands.describe(
        lfg_channel="Channel where /lfg posts go",
        ping_role="Role to ping on new LFG posts",
        require_verified="Only suggest members who verified their Steam link",
        clear_channel="Post LFG wherever /lfg is used",
        clear_role="Stop pinging a role",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def setup_cmd(
        self,
        interaction: discord.Interaction,
        lfg_channel: discord.TextChannel | None = None,
        ping_role: discord.Role | None = None,
        require_verified: bool | None = None,
        clear_channel: bool = False,
        clear_role: bool = False,
    ) -> None:
        guild = require_guild(interaction)
        fields: dict[str, Any] = {}
        if lfg_channel is not None:
            fields["lfg_channel_id"] = lfg_channel.id
        if clear_channel:
            fields["lfg_channel_id"] = None
        if ping_role is not None:
            fields["ping_role_id"] = ping_role.id
        if clear_role:
            fields["ping_role_id"] = None
        if require_verified is not None:
            fields["require_verified"] = require_verified

        async with self.bot.app.sessions.begin() as s:
            cfg = (
                await guilds.update_config(s, guild.id, **fields)
                if fields
                else await guilds.get_config(s, guild.id)
            )
            text = describe(cfg)

        warn = ""
        if lfg_channel and not lfg_channel.permissions_for(guild.me).send_messages:
            warn = f"\n\n⚠️ I can't send messages in {lfg_channel.mention} yet."
        if ping_role and not (ping_role.mentionable or guild.me.guild_permissions.mention_everyone):
            warn += f"\n\n⚠️ {ping_role.mention} isn't mentionable, so pings won't notify."
        await interaction.response.send_message(
            ("Saved.\n\n" if fields else "") + text + warn, ephemeral=True
        )


async def setup(bot: DeadBot) -> None:
    await bot.add_cog(Setup(bot))
