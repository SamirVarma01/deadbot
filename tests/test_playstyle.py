from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from deadbot.bot.embeds import hour_timestamp
from deadbot.services.playstyle import busiest_days, hero_lines, peak_window, role_mix


@dataclass
class Row:
    hero_id: int
    won: bool
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    start_time: datetime = datetime(2026, 10, 12, tzinfo=UTC)


def test_role_mix_shares_by_hero_type():
    types = {1: "marksman", 2: "brawler", 3: "brawler"}
    mix = role_mix({1: 2, 2: 3, 3: 5, 99: 10}, types)
    # unknown heroes count toward the total but aren't listed
    assert mix == [("brawler", pytest.approx(0.4)), ("marksman", pytest.approx(0.1))]
    assert role_mix({}, types) == []


def test_hero_lines_aggregate_and_sort():
    rows = [
        Row(1, True, 5, 1, 2),
        Row(2, False),
        Row(1, False, 1, 3, 0),
        Row(2, True),
        Row(2, True),
    ]
    lines = hero_lines(rows)
    assert [line["hero_id"] for line in lines] == [2, 1]
    one = next(line for line in lines if line["hero_id"] == 1)
    assert one == {"hero_id": 1, "games": 2, "wins": 1, "kills": 6, "deaths": 4, "assists": 2}


def _at(hour, day=12):
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def test_peak_window_wraps_midnight():
    times = [_at(23), _at(0), _at(1), _at(1), _at(12)]
    start, share = peak_window(times)
    assert start in (22, 23)
    assert share == pytest.approx(0.8)


def test_peak_window_empty():
    assert peak_window([]) is None


def test_busiest_days():
    times = [_at(10, 10), _at(11, 10), _at(10, 11), _at(10, 12)]  # Sat, Sat, Sun, Mon
    assert busiest_days(times)[0] == "Sat"


def test_hour_timestamp_uses_given_hour():
    now = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)
    ts = int(datetime(2026, 10, 14, 21, tzinfo=UTC).timestamp())
    assert hour_timestamp(21, now) == f"<t:{ts}:t>"
