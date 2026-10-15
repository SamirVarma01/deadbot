from deadbot.data.deadlock_api.models import MatchHistoryEntry, MatchMetadata, MateStats
from deadbot.services.sync import (
    SharedRecord,
    SyncService,
    counted,
    detail_to_fetch,
    shared_records,
)
from deadbot.services.teammates import best_pairings


def entry(mid, *, won=True, game_mode=1, match_mode=1, team=0, start=1_790_000_000):
    return MatchHistoryEntry(
        account_id=42,
        match_id=mid,
        hero_id=1,
        hero_level=30,
        start_time=start,
        game_mode=game_mode,
        match_mode=match_mode,
        player_team=team,
        player_kills=1,
        player_deaths=1,
        player_assists=1,
        denies=0,
        net_worth=1,
        last_hits=1,
        match_duration_s=1800,
        match_result=team if won else 1 - team,
    )


def test_counted_keeps_only_6v6_ranked_and_unranked():
    rows = [
        entry(1, match_mode=1),
        entry(2, match_mode=4),
        entry(3, match_mode=3),  # bots
        entry(4, match_mode=2),  # private lobby
        entry(5, game_mode=4),  # street brawl
    ]
    assert [r.match_id for r in counted(rows)] == [1, 2]


def test_shared_records_use_counted_matches_and_own_result():
    history = {e.match_id: e for e in [entry(1), entry(2, won=False), entry(3), entry(4)]}
    mates = [
        MateStats(mate_id=7, wins=99, matches_played=5, matches=[1, 2, 3, 900, 901]),
        MateStats(mate_id=8, wins=2, matches_played=2, matches=[1, 4]),
        MateStats(mate_id=9, wins=4, matches_played=4, matches=[1, 2, 3, 4]),
    ]
    out = shared_records(mates, history, min_games=3)
    assert [(r.mate_id, r.games, r.wins) for r in out] == [(9, 4, 3), (7, 3, 2)]


def test_mate_rows_from_metadata():
    history = {10: entry(10, team=1, won=True)}
    md = [
        MatchMetadata.model_validate(
            {
                "match_id": 10,
                "start_time": "2026-09-30 10:51:56",
                "winning_team": "Team1",
                "duration_s": 1800,
                "players": [
                    {"account_id": 7, "hero_id": 15, "team": "Team1", "kills": 3},
                    {"account_id": 8, "hero_id": 16, "team": "Team0"},
                    {"account_id": 99, "hero_id": 17, "team": "Team1"},  # not requested
                ],
            }
        ),
        MatchMetadata.model_validate(  # not in counted history
            {"match_id": 11, "start_time": "x", "duration_s": 1, "players": []}
        ),
    ]
    rows = SyncService._mate_rows(md, history, {7, 8})
    by_id = {r["account_id"]: r for r in rows}
    assert set(by_id) == {7, 8}
    assert by_id[7]["won"] is True and by_id[7]["team"] == 1 and by_id[7]["hero_id"] == 15
    assert by_id[8]["won"] is False
    assert by_id[7]["net_worth"] is None


def test_best_pairings_shrinks_and_respects_min_games():
    names = {1: "Haze", 2: "Bebop", 3: "Dynamo"}
    rows = [
        (7, 1, 2, 3, 3),  # 3-0: lucky streak, shrinks hard
        (7, 1, 3, 40, 28),  # 70% over 40 games
        (7, 2, 3, 2, 2),  # below min games
        (8, 1, 2, 5, 1),
    ]
    best = best_pairings(rows, {7: 0.5, 8: 0.5}, names, min_games=3)
    assert (best[7].my_hero, best[7].mate_hero, best[7].games) == ("Haze", "Dynamo", 40)
    assert best[8].games == 5


def test_detail_to_fetch_first_sync_requests_everything_missing():
    top = [SharedRecord(7, [1, 2, 3], 2)]
    assert detail_to_fetch(top, {(1, 7)}, last_seen=0) == {2, 3}


def test_detail_to_fetch_incremental_skips_known_unavailable():
    top = [SharedRecord(7, [1, 2, 10], 2), SharedRecord(8, [1, 2], 1)]
    # match 2 never had metadata for mate 7; mate 8 just entered the top list
    have = {(1, 7)}
    assert detail_to_fetch(top, have, last_seen=5) == {10, 1, 2}
    assert detail_to_fetch(top[:1], have, last_seen=5) == {10}
