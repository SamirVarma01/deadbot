"""Pure helpers that summarize how someone plays from their match rows."""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class HeroRow(Protocol):
    hero_id: int
    won: bool
    kills: int
    deaths: int
    assists: int
    start_time: datetime


def role_mix(
    hero_games: Mapping[int, int], hero_types: Mapping[int, str]
) -> list[tuple[str, float]]:
    """Share of games per Valve hero_type (brawler/marksman/mystic/assassin), largest first."""
    by_type: Counter[str] = Counter()
    for hero_id, games in hero_games.items():
        by_type[hero_types.get(hero_id) or "unknown"] += games
    total = sum(by_type.values())
    if not total:
        return []
    return [(t, n / total) for t, n in by_type.most_common() if t != "unknown"]


def hero_lines(rows: Iterable[HeroRow]) -> list[dict[str, int]]:
    """Per-hero aggregates, most played first."""
    agg: dict[int, dict[str, int]] = {}
    for r in rows:
        a = agg.setdefault(
            r.hero_id,
            {"hero_id": r.hero_id, "games": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0},
        )
        a["games"] += 1
        a["wins"] += int(r.won)
        a["kills"] += r.kills
        a["deaths"] += r.deaths
        a["assists"] += r.assists
    return sorted(agg.values(), key=lambda a: (a["games"], a["wins"]), reverse=True)


def peak_window(times: Sequence[datetime], width: int = 4) -> tuple[int, float] | None:
    """Busiest `width`-hour window as (start hour in UTC, share of games). Wraps midnight."""
    if not times:
        return None
    hist = Counter(t.hour for t in times)
    best = max(range(24), key=lambda h: sum(hist[(h + i) % 24] for i in range(width)))
    return best, sum(hist[(best + i) % 24] for i in range(width)) / len(times)


def busiest_days(times: Sequence[datetime], top: int = 2) -> list[str]:
    hist = Counter(t.weekday() for t in times)
    return [WEEKDAYS[d] for d, _ in hist.most_common(top)]
