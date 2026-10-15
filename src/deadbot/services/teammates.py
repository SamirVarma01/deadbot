from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deadbot.config import Settings
from deadbot.data.cache import Cache
from deadbot.data.db import repo
from deadbot.services.catalog import Catalog
from deadbot.services.errors import NotSyncedError
from deadbot.services.synergy import shrunk_rate

REPORT_SIZE = 25
_PAIRING_PRIOR = 10.0  # a 3-0 combo shouldn't outrank 70% over 40 games
_NEVER = datetime.min.replace(tzinfo=UTC)


class TeammateSort(StrEnum):
    GAMES = "games"
    SYNERGY = "synergy"
    RECENT = "recent"


class HeroPairing(BaseModel):
    my_hero: str
    mate_hero: str
    games: int
    wins: int


class TeammateEntry(BaseModel):
    account_id: int
    name: str
    discord_id: int | None
    games: int
    wins: int
    expected_wr: float | None
    synergy: float | None
    premade_games: int
    steam_friends: bool
    last_played: datetime | None
    best_pairing: HeroPairing | None

    @property
    def win_rate(self) -> float:
        return self.wins / self.games


class TeammatesReport(BaseModel):
    account_id: int
    persona_name: str | None
    baseline_wr: float | None
    total_mates: int
    min_games: int
    entries: list[TeammateEntry]  # top by sort order
    linked: list[TeammateEntry]  # every mate who has linked Discord, regardless of rank


def best_pairings(
    rows: list[tuple[int, int, int, int, int]],
    pair_wr: dict[int, float],
    names: dict[int, str],
    min_games: int,
) -> dict[int, HeroPairing]:
    """Per mate, the hero combo with the best win rate shrunk toward the pair's overall rate."""
    best: dict[int, tuple[float, HeroPairing]] = {}
    for mate, h1, h2, games, wins in rows:
        if games < min_games or mate not in pair_wr:
            continue
        score = shrunk_rate(wins, games, pair_wr[mate], _PAIRING_PRIOR)
        if mate not in best or score > best[mate][0]:
            best[mate] = (
                score,
                HeroPairing(
                    my_hero=names.get(h1, f"Hero {h1}"),
                    mate_hero=names.get(h2, f"Hero {h2}"),
                    games=games,
                    wins=wins,
                ),
            )
    return {m: p for m, (_, p) in best.items()}


class TeammatesService:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        cache: Cache,
        catalog: Catalog,
    ):
        self._s = settings
        self._sessions = sessions
        self._cache = cache
        self._catalog = catalog

    async def build(
        self, account_id: int, sort: TeammateSort = TeammateSort.GAMES, min_games: int | None = None
    ) -> TeammatesReport:
        min_games = max(min_games or self._s.teammates_min_games, self._s.teammates_min_games)
        async with self._sessions() as s:
            player = await repo.get_player(s, account_id)
            if player is None or player.last_synced_at is None:
                raise NotSyncedError("Teammates are still syncing. Try again in a minute or two.")
            key = (
                f"report:teammates:{account_id}:{int(player.last_synced_at.timestamp())}"
                f":{sort}:{min_games}"
            )
            if cached := await self._cache.get_json(key):
                return TeammatesReport.model_validate(cached)

            pairs = await repo.pair_stats_for(s, account_id, min_games)
            discord_ids = await repo.discord_ids_for_accounts(s, (m for m, _ in pairs))
            sort_key = {
                TeammateSort.GAMES: lambda mp: (mp[1].games_together, mp[1].wins_together),
                TeammateSort.SYNERGY: lambda mp: mp[1].synergy or 0.0,
                TeammateSort.RECENT: lambda mp: mp[1].last_played_together or _NEVER,
            }[sort]
            pairs.sort(key=sort_key, reverse=True)
            shown = pairs[:REPORT_SIZE]
            shown_ids = {m for m, _ in shown}
            shown += [(m, p) for m, p in pairs if m in discord_ids and m not in shown_ids]
            mate_ids = [m for m, _ in shown]
            players = await repo.players_by_id(s, mate_ids)
            pairing_rows = await repo.hero_pairings(s, account_id, mate_ids)

        names = await self._catalog.hero_names()
        pairings = best_pairings(
            pairing_rows,
            {m: p.wins_together / p.games_together for m, p in shown},
            names,
            self._s.hero_pair_min_games,
        )
        entries = [
            TeammateEntry(
                account_id=m,
                name=(players[m].persona_name if m in players else None) or f"[U:1:{m}]",
                discord_id=discord_ids.get(m),
                games=p.games_together,
                wins=p.wins_together,
                expected_wr=p.expected_wr,
                synergy=p.synergy,
                premade_games=p.premade_games,
                steam_friends=p.steam_friends,
                last_played=p.last_played_together,
                best_pairing=pairings.get(m),
            )
            for m, p in shown
        ]
        report = TeammatesReport(
            account_id=account_id,
            persona_name=player.persona_name,
            baseline_wr=player.win_rate,
            total_mates=len(pairs),
            min_games=min_games,
            entries=entries[:REPORT_SIZE],
            linked=[e for e in entries if e.discord_id is not None],
        )
        await self._cache.set_json(key, report.model_dump(mode="json"), self._s.ttl_reports)
        return report
