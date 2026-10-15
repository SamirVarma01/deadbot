from datetime import UTC, datetime

import discord

from deadbot.services.profile import ProfileReport
from deadbot.services.teammates import TeammateEntry, TeammatesReport

COLOR = discord.Color.from_rgb(201, 166, 97)
TEAMMATES_SHOWN = 10


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def signed_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def hour_timestamp(hour_utc: int, now: datetime | None = None) -> str:
    """A Discord timestamp at today's `hour_utc`, so each viewer sees their own local time."""
    now = now or datetime.now(UTC)
    at = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    return f"<t:{int(at.timestamp())}:t>"


def profile_embed(r: ProfileReport) -> discord.Embed:
    name = r.persona_name or f"[U:1:{r.account_id}]"
    e = discord.Embed(title=f"{name}'s Deadlock profile", color=COLOR)
    if r.avatar_url:
        e.set_thumbnail(url=r.avatar_url)

    rank = r.rank_label or "Unranked / in placements"
    record = f"{r.total_games:,} games · {pct(r.total_wins / r.total_games)} WR"
    e.description = f"**Rank:** {rank}\n**All-time:** {record}"
    if r.window_days:
        e.description += (
            f"\n**Last {r.window_days} days:** {r.window_games:,} games · "
            f"{pct(r.window_wins / r.window_games)} WR"
        )

    if r.top_heroes:
        e.add_field(
            name="Top heroes",
            value="\n".join(
                f"**{h.name}**: {h.games} games · {pct(h.win_rate)} · {h.kda:.1f} KDA"
                for h in r.top_heroes
            ),
            inline=False,
        )
    if r.roles:
        e.add_field(
            name="Hero types played",
            value=" · ".join(f"{t.title()} {pct(s)}" for t, s in r.roles),
            inline=False,
        )
    if r.peak_start_hour_utc is not None and r.peak_share is not None:
        start = r.peak_start_hour_utc
        e.add_field(
            name="Usually plays",
            value=(
                f"{hour_timestamp(start)} – {hour_timestamp((start + 4) % 24)} your time "
                f"({pct(r.peak_share)} of games)\nBusiest days (UTC): {', '.join(r.busiest_days)}"
            ),
            inline=False,
        )
    basis = f"last {r.window_days} days" if r.window_days else "all-time"
    e.set_footer(text=f"Heroes, types and hours use {basis} · Data synced")
    e.timestamp = r.last_synced_at
    return e


def _teammate_line(i: int, t: TeammateEntry, baseline: float | None, in_guild: bool) -> str:
    who = f"<@{t.discord_id}>" if in_guild and t.discord_id else f"**{t.name}**"
    vs = f" ({signed_pct(t.win_rate - baseline)} vs you)" if baseline is not None else ""
    parts = [f"{t.games} games · {pct(t.win_rate)} WR{vs}"]
    if t.synergy is not None:
        parts.append(f"synergy {signed_pct(t.synergy)}")
    if t.premade_games:
        parts.append(f"premade ×{t.premade_games}")
    if t.steam_friends:
        parts.append("Steam friends")
    line = f"`{i:>2}.` {who}: " + " · ".join(parts)
    if t.best_pairing:
        p = t.best_pairing
        line += (
            f"\n      ↳ best duo: {p.my_hero} + {p.mate_hero} "
            f"({p.games} games, {pct(p.wins / p.games)})"
        )
    return line


def teammates_embed(r: TeammatesReport, guild: discord.Guild | None) -> discord.Embed:
    def in_guild(t: TeammateEntry) -> bool:
        return bool(guild and t.discord_id and guild.get_member(t.discord_id))

    name = r.persona_name or f"[U:1:{r.account_id}]"
    e = discord.Embed(title=f"{name}'s teammates", color=COLOR)
    baseline = f" · overall WR {pct(r.baseline_wr)}" if r.baseline_wr is not None else ""
    header = f"{r.total_mates:,} teammates with ≥{r.min_games} games together{baseline}"
    if not r.entries:
        e.description = (
            f"No teammates with ≥{r.min_games} games together yet. Queue with some friends!"
        )
        return e

    lines = [
        _teammate_line(i, t, r.baseline_wr, in_guild(t))
        for i, t in enumerate(r.entries[:TEAMMATES_SHOWN], 1)
    ]
    e.description = header + "\n\n" + "\n".join(lines)

    shown = {t.account_id for t in r.entries[:TEAMMATES_SHOWN]}
    here = [t for t in r.linked if in_guild(t) and t.account_id not in shown]
    if here:
        e.add_field(
            name="Also in this server",
            value="\n".join(
                f"<@{t.discord_id}>: {t.games} games · {pct(t.win_rate)} WR" for t in here[:10]
            )[:1024],
            inline=False,
        )
    e.set_footer(
        text="Synergy = win rate together minus what your solo records predict, "
        "shrunk toward 0 for small samples. Premade = teammates in back-to-back games."
    )
    return e
