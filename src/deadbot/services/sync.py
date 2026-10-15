"""Pulls a linked player's history into Postgres and recomputes their pair stats."""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.db import repo
from deadbot.data.deadlock_api import DeadlockAPI, ProtectedAccountError
from deadbot.data.deadlock_api.models import (
    GameMode,
    MatchHistoryEntry,
    MatchMetadata,
    MatchMode,
    MateStats,
    team_from_name,
)
from deadbot.services.premade import MatchRef, ordered, party_links, session_premades
from deadbot.services.synergy import pair_synergy

log = logging.getLogger(__name__)

COUNTED_MODES = {int(MatchMode.RANKED), int(MatchMode.UNRANKED)}


def counted(entries: list[MatchHistoryEntry]) -> list[MatchHistoryEntry]:
    """Standard 6v6 ranked/unranked games only (no bots, lobbies, or Street Brawl)."""
    return [e for e in entries if e.game_mode == GameMode.NORMAL and e.match_mode in COUNTED_MODES]


def _ts(unix: int) -> datetime:
    return datetime.fromtimestamp(unix, tz=UTC)


@dataclass(frozen=True)
class SharedRecord:
    mate_id: int
    match_ids: list[int]
    wins: int

    @property
    def games(self) -> int:
        return len(self.match_ids)


def shared_records(
    mates: list[MateStats], history: dict[int, MatchHistoryEntry], min_games: int
) -> list[SharedRecord]:
    """Restrict mate-stats to counted matches. Teammates share the result, so the player's own
    `won` flag gives the pair's wins (mate-stats has no match_mode filter)."""
    out = []
    for m in mates:
        ids = [mid for mid in m.matches if mid in history]
        if len(ids) >= min_games:
            out.append(SharedRecord(m.mate_id, ids, sum(history[mid].won for mid in ids)))
    return sorted(out, key=lambda r: r.games, reverse=True)


def detail_to_fetch(
    top: list[SharedRecord], have: set[tuple[int, int]], last_seen: int
) -> set[int]:
    """Shared matches still lacking the mate's row. After the first sync, only new matches and
    mates new to the top list are requested: some matches have no upstream metadata, and
    re-asking for them every sync would burn the 30 req/min bulk-metadata budget."""
    known_mates = {aid for _, aid in have}
    return {
        mid
        for m in top
        for mid in m.match_ids
        if (mid, m.mate_id) not in have and (mid > last_seen or m.mate_id not in known_mates)
    }


@dataclass(frozen=True)
class SyncResult:
    account_id: int
    matches: int
    new_matches: int
    mates: int


class SyncService:
    def __init__(
        self, settings: Settings, api: DeadlockAPI, sessions: async_sessionmaker[AsyncSession]
    ):
        self._s = settings
        self._api = api
        self._sessions = sessions
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def is_syncing(self, account_id: int) -> bool:
        return self._locks[account_id].locked()

    async def sync_account(self, account_id: int) -> SyncResult:
        async with self._locks[account_id]:
            try:
                return await self._sync(account_id)
            except Exception as e:
                log.exception("sync failed for %s", account_id)
                async with self._sessions.begin() as s:
                    await repo.mark_synced(s, account_id, datetime.now(UTC), error=str(e)[:500])
                raise

    async def _sync(self, aid: int) -> SyncResult:
        s = self._s
        entries = counted(await self._api.match_history(aid))
        history = {e.match_id: e for e in entries}
        mates = shared_records(
            await self._api.mate_stats(aid, min_matches_played=s.teammates_min_games),
            history,
            s.teammates_min_games,
        )
        party = {
            m.mate_id: [mid for mid in m.matches if mid in history]
            for m in await self._api.mate_stats(aid, same_party=True)
        }
        try:
            rank_badge = (await self._api.rank(aid)).badge or None
        except ProtectedAccountError:
            rank_badge = None

        mate_ids = [m.mate_id for m in mates]
        totals: dict[int, list[int]] = defaultdict(lambda: [0, 0])
        for hs in await self._api.hero_stats(mate_ids):
            totals[hs.account_id][0] += hs.matches_played
            totals[hs.account_id][1] += hs.wins

        top = mates[: s.sync_detail_mates]
        top_ids = [m.mate_id for m in top]
        profiles = {p.account_id: p for p in await self._api.steam_profiles([aid, *top_ids])}

        async with self._sessions() as session:
            player = await repo.get_player(session, aid)
            last_seen = (player.last_match_id if player else None) or 0
            have = await repo.existing_player_matches(
                session, top_ids, {mid for m in top for mid in m.match_ids}
            )
        missing = detail_to_fetch(top, have, last_seen)
        metadata = await self._api.bulk_metadata(sorted(missing), top_ids) if missing else []

        new_entries = [e for e in entries if e.match_id > last_seen]
        own_wins = sum(e.won for e in entries)
        now = datetime.now(UTC)

        async with self._sessions.begin() as session:
            me = profiles.get(aid)
            await repo.upsert_players(
                session,
                [
                    {
                        "account_id": aid,
                        "persona_name": me.personaname if me else None,
                        "avatar_url": me.avatarfull if me else None,
                        "rank_badge": rank_badge,
                        "total_games": len(entries),
                        "total_wins": own_wins,
                        "last_match_id": max(history, default=None),
                    }
                ]
                + [
                    {
                        "account_id": mid,
                        "persona_name": profiles[mid].personaname if mid in profiles else None,
                        "avatar_url": profiles[mid].avatarfull if mid in profiles else None,
                        "total_games": totals[mid][0] if mid in totals else None,
                        "total_wins": totals[mid][1] if mid in totals else None,
                    }
                    for mid in mate_ids
                ],
            )
            await repo.upsert_matches(
                session,
                [
                    {
                        "match_id": e.match_id,
                        "start_time": _ts(e.start_time),
                        "duration_s": e.match_duration_s,
                        "game_mode": e.game_mode,
                        "match_mode": e.match_mode,
                        "winning_team": e.match_result,
                        "average_badge": None,
                    }
                    for e in new_entries
                ],
            )
            await repo.upsert_match_players(
                session,
                [
                    {
                        "match_id": e.match_id,
                        "account_id": aid,
                        "team": e.player_team,
                        "hero_id": e.hero_id,
                        "kills": e.player_kills,
                        "deaths": e.player_deaths,
                        "assists": e.player_assists,
                        "net_worth": e.net_worth,
                        "last_hits": e.last_hits,
                        "won": e.won,
                        "start_time": _ts(e.start_time),
                    }
                    for e in new_entries
                ]
                + self._mate_rows(metadata, history, set(top_ids)),
            )

            together = await repo.teammates_by_match(session, aid, top_ids)
            session_links = session_premades(
                (
                    MatchRef(e.match_id, e.start_time, e.match_duration_s, e.player_team)
                    for e in entries
                ),
                together,
                s.session_gap_minutes * 60,
            )
            links = party_links(aid, party, session_links)
            await repo.upsert_party_links(
                session,
                [
                    {"match_id": mid, "account_a": a, "account_b": b, "sources": src}
                    for (mid, a, b), src in links.items()
                ],
            )
            premades = await repo.party_link_counts(session, aid)

            pair_rows = []
            for m in mates:
                syn = pair_synergy(
                    pair_wins=m.wins,
                    pair_games=m.games,
                    a_wins=own_wins,
                    a_games=len(entries),
                    b_wins=totals[m.mate_id][1] if m.mate_id in totals else None,
                    b_games=totals[m.mate_id][0] if m.mate_id in totals else None,
                    strength=s.synergy_prior_strength,
                )
                a, b = ordered(aid, m.mate_id)
                pair_rows.append(
                    {
                        "account_a": a,
                        "account_b": b,
                        "games_together": m.games,
                        "wins_together": m.wins,
                        # Friends flags cover every shared game, so only session links
                        # count as per-match premade evidence.
                        "premade_games": premades.get(m.mate_id, (0, 0))[1],
                        "steam_friends": premades.get(m.mate_id, (0, 0))[0] > 0,
                        "expected_wr": syn.expected,
                        "synergy": syn.synergy,
                        "last_played_together": _ts(
                            max(history[mid].start_time for mid in m.match_ids)
                        ),
                    }
                )
            await repo.upsert_pair_stats(session, pair_rows)
            await repo.mark_synced(session, aid, now)

        log.info(
            "synced %s: %d matches (%d new), %d mates, %d detail matches",
            aid,
            len(entries),
            len(new_entries),
            len(mates),
            len(metadata),
        )
        return SyncResult(aid, len(entries), len(new_entries), len(mates))

    @staticmethod
    def _mate_rows(
        metadata: list[MatchMetadata], history: dict[int, MatchHistoryEntry], wanted: set[int]
    ) -> list[dict]:
        """Mates' rows from bulk metadata. Only fetched for counted matches in `history`."""
        rows = []
        for md in metadata:
            own = history.get(md.match_id)
            if own is None:
                continue
            winner = team_from_name(md.winning_team)
            for p in md.players:
                team = team_from_name(p.team)
                if p.account_id not in wanted or team is None:
                    continue
                rows.append(
                    {
                        "match_id": md.match_id,
                        "account_id": p.account_id,
                        "team": team,
                        "hero_id": p.hero_id,
                        "kills": p.kills,
                        "deaths": p.deaths,
                        "assists": p.assists,
                        "net_worth": None,
                        "last_hits": None,
                        "won": team == (winner if winner is not None else own.match_result),
                        "start_time": _ts(own.start_time),
                    }
                )
        return rows
