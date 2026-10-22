import pytest

from deadbot.services.matching import (
    PairInfo,
    PlayerFeatures,
    Scales,
    Weights,
    candidate_fit,
    greedy_fill,
    hour_overlap,
    party_fit,
    rank_ordinal,
    role_gap_fill,
)

W, S = Weights(), Scales()
EVENINGS = tuple((1 / 4 if 18 <= h < 22 else 0.0) for h in range(24))
MORNINGS = tuple((1 / 4 if 6 <= h < 10 else 0.0) for h in range(24))


def p(aid, *, roles=None, rank=None, hours=None, online=None):
    return PlayerFeatures(
        account_id=aid,
        discord_id=aid * 10,
        roles=roles or {},
        rank_badge=rank,
        hours=hours,
        online=online,
    )


def test_rank_ordinal():
    assert rank_ordinal(115) == 11 * 6 + 5
    assert rank_ordinal(11) == 7  # Initiate 1
    assert rank_ordinal(0) is None and rank_ordinal(None) is None


def test_hour_overlap_bounds():
    assert hour_overlap(EVENINGS, EVENINGS) == pytest.approx(1.0)
    assert hour_overlap(EVENINGS, MORNINGS) == 0.0


def test_role_gap_fill_rewards_missing_types():
    party = [{"assassin": 1.0}]
    assert role_gap_fill({"brawler": 1.0}, party) == (pytest.approx(1.0), "brawler")
    assert role_gap_fill({"assassin": 1.0}, party) == (pytest.approx(0.0), None)
    assert role_gap_fill({}, party) == (0.5, None)  # unknown is neutral


def test_unknown_everything_is_neutral():
    fit = candidate_fit(p(2), [p(1)], {}, W, S, now_hour=12)
    assert fit.score == pytest.approx(0.5)
    assert fit.reason == "no shared history yet"


def test_positive_synergy_raises_score_and_explains():
    pairs = {(1, 2): PairInfo(synergy=0.10, games=40)}
    fit = candidate_fit(p(2), [p(1)], pairs, W, S, now_hour=12)
    neutral = candidate_fit(p(3), [p(1)], {}, W, S, now_hour=12)
    assert fit.score > neutral.score
    assert "+10% synergy" in fit.reason


def test_negative_synergy_is_reported_honestly():
    pairs = {(1, 2): PairInfo(synergy=-0.05, games=40)}
    fit = candidate_fit(p(2), [p(1)], pairs, W, S, now_hour=12)
    assert fit.score < 0.5
    assert "-5% synergy so far" in fit.reason


def test_unknown_pairs_dilute_synergy_across_party():
    pairs = {(1, 3): PairInfo(0.10, 40)}
    one = candidate_fit(p(3), [p(1)], pairs, W, S, 12)
    two = candidate_fit(p(3), [p(1), p(2)], pairs, W, S, 12)
    assert one.score > two.score > 0.5
    assert two.synergy_known == 1


def test_schedule_overlap_and_plays_now():
    party = [p(1, hours=EVENINGS)]
    evening = candidate_fit(p(2, hours=EVENINGS), party, {}, W, S, now_hour=19)
    morning = candidate_fit(p(3, hours=MORNINGS), party, {}, W, S, now_hour=19)
    assert evening.score > morning.score
    assert "usually on at this hour" in evening.reason
    assert morning.hour_overlap == 0.0


def test_presence_overrides_usual_hours():
    party = [p(1, hours=EVENINGS)]
    online = candidate_fit(p(2, hours=MORNINGS, online=True), party, {}, W, S, 19)
    offline = candidate_fit(p(3, hours=EVENINGS, online=False), party, {}, W, S, 19)
    assert "online now" in online.reason
    assert "usually on" not in offline.reason


def test_rank_gap_penalty_and_reason():
    party = [p(1, rank=115)]
    close = candidate_fit(p(2, rank=114), party, {}, W, S, 12)
    far = candidate_fit(p(3, rank=95), party, {}, W, S, 12)  # 2 tiers below
    assert close.score > far.score
    assert "similar rank" in close.reason
    assert "~2 tiers apart" in far.reason


def test_role_coverage_reason():
    fit = candidate_fit(
        p(2, roles={"brawler": 0.9, "assassin": 0.1}), [p(1, roles={"assassin": 1.0})], {}, W, S, 12
    )
    assert fit.reason.startswith("covers Brawler")


def test_greedy_fill_rescores_after_each_pick():
    owner = p(1, roles={"assassin": 1.0})
    cands = [
        p(2, roles={"brawler": 1.0}),
        p(3, roles={"brawler": 1.0}),
        p(4, roles={"mystic": 1.0}),
    ]
    picks = greedy_fill(cands, [owner], 2, {}, W, S, 12)
    # First brawler wins the tie (lowest id); then the mystic gap beats a second brawler.
    assert [r.player.account_id for r in picks] == [2, 4]
    assert all(r.fit.reason for r in picks)


def test_greedy_fill_skips_party_members_and_handles_small_pools():
    owner = p(1)
    picks = greedy_fill([p(1), p(2)], [owner], 3, {}, W, S, 12)
    assert [r.player.account_id for r in picks] == [2]


def test_party_fit_summary():
    members = [
        p(1, roles={"brawler": 1.0}, rank=115, hours=EVENINGS),
        p(2, roles={"mystic": 1.0}, rank=113, hours=EVENINGS),
        p(3, roles={"marksman": 1.0}, rank=111, hours=EVENINGS),
    ]
    pairs = {(1, 2): PairInfo(0.06, 50)}
    fit = party_fit(members, pairs, W, S)
    assert len(fit.pairs) == 3
    assert fit.synergy == pytest.approx(0.02)
    assert fit.rank_spread == 4
    assert fit.hour_overlap == pytest.approx(1.0)
    assert 18 <= fit.best_hour_utc < 22
    assert 0.5 < fit.score <= 1.0
    lopsided = party_fit([p(4, roles={"brawler": 1.0}), p(5, roles={"brawler": 1.0})], {}, W, S)
    assert lopsided.role_balance == pytest.approx(0.0)
