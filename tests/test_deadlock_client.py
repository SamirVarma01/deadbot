import asyncio
import time

import httpx
import pytest
import respx

from deadbot.data.cache import Cache
from deadbot.data.deadlock_api.client import (
    DeadlockAPI,
    DeadlockAPIError,
    ProtectedAccountError,
    SlidingWindowLimiter,
)

BASE = "https://api.test"

HISTORY_ROW = {
    "account_id": 42,
    "match_id": 1001,
    "hero_id": 13,
    "hero_level": 30,
    "start_time": 1_790_000_000,
    "game_mode": 1,
    "match_mode": 4,
    "player_team": 1,
    "player_kills": 5,
    "player_deaths": 2,
    "player_assists": 9,
    "denies": 3,
    "net_worth": 30000,
    "last_hits": 120,
    "match_duration_s": 1800,
    "match_result": 1,
    "objectives_mask_team0": 0,
    "objectives_mask_team1": 0,
    "player_match_outcome": 1,
    "some_future_field": "ignored",
}


@pytest.fixture
def api(settings, cache, fake_sleep):
    return DeadlockAPI(settings, cache, sleep=fake_sleep)


@respx.mock
async def test_match_history_parses_and_sends_api_key(api):
    route = respx.get(f"{BASE}/v1/players/42/match-history").respond(json=[HISTORY_ROW])
    rows = await api.match_history(42)
    assert len(rows) == 1
    assert rows[0].won is True
    assert route.calls.last.request.headers["X-API-KEY"] == "test-key"


@respx.mock
async def test_list_params_are_comma_separated(api):
    route = respx.get(f"{BASE}/v1/players/steam").respond(json=[])
    await api.steam_profiles([3, 1, 2, 1])
    assert route.calls.last.request.url.params["account_ids"] == "1,2,3"


@respx.mock
async def test_bool_params_are_lowercase(api):
    route = respx.get(f"{BASE}/v1/players/42/mate-stats").respond(json=[])
    await api.mate_stats(42, same_party=True)
    assert route.calls.last.request.url.params["same_party"] == "true"
    assert "min_matches_played" not in route.calls.last.request.url.params


@respx.mock
async def test_429_retries_using_retry_after(api, sleeps):
    route = respx.get(f"{BASE}/v1/players/42/rank").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"badge": 115, "rank": 11, "subrank": 5}),
        ]
    )
    rank = await api.rank(42)
    assert rank.badge == 115
    assert route.call_count == 2
    assert sleeps == [7.0]


@respx.mock
async def test_503_without_header_uses_exponential_backoff(api, sleeps):
    respx.get(f"{BASE}/v1/players/42/rank").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"badge": 0, "rank": 0, "subrank": 0}),
        ]
    )
    await api.rank(42)
    assert len(sleeps) == 2
    assert 1 <= sleeps[0] < 1.5 and 2 <= sleeps[1] < 2.5


@respx.mock
async def test_transport_errors_are_retried(api, sleeps):
    respx.get(f"{BASE}/v1/players/42/rank").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"badge": 0, "rank": 0, "subrank": 0}),
        ]
    )
    await api.rank(42)
    assert len(sleeps) == 1


@respx.mock
async def test_gives_up_after_max_retries(api, sleeps, settings):
    route = respx.get(f"{BASE}/v1/players/42/rank").respond(429)
    with pytest.raises(DeadlockAPIError) as exc:
        await api.rank(42)
    assert exc.value.status == 429
    assert route.call_count == settings.http_max_retries + 1
    assert len(sleeps) == settings.http_max_retries


@respx.mock
async def test_match_history_429_with_body_is_accepted(api, sleeps):
    respx.get(f"{BASE}/v1/players/42/match-history").respond(429, json=[HISTORY_ROW])
    rows = await api.match_history(42)
    assert [r.match_id for r in rows] == [1001]
    assert sleeps == []


@respx.mock
async def test_403_is_protected_account(api):
    respx.get(f"{BASE}/v1/players/42/rank").respond(403, text="protected")
    with pytest.raises(ProtectedAccountError):
        await api.rank(42)


@respx.mock
async def test_400_is_not_retried(api, sleeps):
    respx.get(f"{BASE}/v1/players/42/rank").respond(400, text="bad")
    with pytest.raises(DeadlockAPIError) as exc:
        await api.rank(42)
    assert exc.value.status == 400
    assert sleeps == []


@respx.mock
async def test_steam_404_means_no_profiles(api):
    respx.get(f"{BASE}/v1/players/steam").respond(404)
    assert await api.steam_profiles([42]) == []


@respx.mock
async def test_responses_are_cached(api):
    route = respx.get(f"{BASE}/v1/players/42/match-history").respond(json=[HISTORY_ROW])
    await api.match_history(42)
    await api.match_history(42)
    assert route.call_count == 1


@respx.mock
async def test_works_without_redis(settings, fake_sleep):
    api = DeadlockAPI(settings, Cache(None), sleep=fake_sleep)
    route = respx.get(f"{BASE}/v1/players/42/match-history").respond(json=[HISTORY_ROW])
    await api.match_history(42)
    await api.match_history(42)
    assert route.call_count == 2


@respx.mock
async def test_bulk_metadata_chunks_and_filters_players(settings, cache, fake_sleep):
    settings.bulk_metadata_chunk = 2
    api = DeadlockAPI(settings, cache, sleep=fake_sleep)
    payload = {
        "match_id": 1,
        "start_time": "2026-09-30 10:51:56",
        "winning_team": "Team1",
        "duration_s": 1400,
        "players": [{"account_id": 7, "hero_id": 79, "team": "Team1", "kills": 1}],
    }
    route = respx.get(f"{BASE}/v1/matches/metadata").respond(json=[payload])
    out = await api.bulk_metadata([3, 1, 2], [7])
    assert route.call_count == 2
    params = route.calls[0].request.url.params
    assert params["match_ids"] == "1,2"
    assert params["only_filtered_players"] == "true"
    assert params["account_ids"] == "7"
    assert out[0].players[0].team == "Team1"


async def test_sliding_window_limiter_blocks_when_full():
    limiter = SlidingWindowLimiter(limit=2, window=0.2)
    start = time.monotonic()
    await asyncio.gather(*(limiter.acquire() for _ in range(3)))
    assert time.monotonic() - start >= 0.19


@respx.mock
async def test_bulk_metadata_404_means_none_available(api):
    respx.get(f"{BASE}/v1/matches/metadata").respond(404, json={"error": "No matches found"})
    assert await api.bulk_metadata([1, 2], [7]) == []
