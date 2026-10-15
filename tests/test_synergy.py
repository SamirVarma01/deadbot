import pytest

from deadbot.services.synergy import (
    expected_pair_win_rate,
    leave_pair_out_rate,
    logit,
    pair_synergy,
    shrunk_rate,
    sigmoid,
)


@pytest.mark.parametrize("p", [0.1, 0.35, 0.5, 0.8])
def test_logit_sigmoid_inverse(p):
    assert sigmoid(logit(p)) == pytest.approx(p)


def test_logit_is_clipped_at_extremes():
    assert abs(logit(0.0)) < 5 and abs(logit(1.0)) < 5


def test_expected_neutral_partner_keeps_rate():
    assert expected_pair_win_rate(0.5, 0.5) == pytest.approx(0.5)
    assert expected_pair_win_rate(0.6, 0.5) == pytest.approx(0.6)


def test_expected_is_symmetric_and_compounds():
    assert expected_pair_win_rate(0.6, 0.55) == pytest.approx(expected_pair_win_rate(0.55, 0.6))
    assert expected_pair_win_rate(0.6, 0.6) > 0.6
    assert expected_pair_win_rate(0.4, 0.4) < 0.4


def test_expected_unknown_players_are_coinflips():
    assert expected_pair_win_rate(None, None) == pytest.approx(0.5)
    assert expected_pair_win_rate(0.6, None) == pytest.approx(0.6)


def test_leave_pair_out_removes_shared_games():
    assert leave_pair_out_rate(60, 100, 10, 20) == pytest.approx(50 / 80)


def test_leave_pair_out_needs_enough_remaining_games():
    assert leave_pair_out_rate(20, 25, 18, 20) is None  # 5 left
    assert leave_pair_out_rate(None, None, 1, 2) is None


def test_shrinkage_limits():
    assert shrunk_rate(0, 0, 0.53, 20) == pytest.approx(0.53)
    assert shrunk_rate(7000, 10000, 0.5, 20) == pytest.approx(0.7, abs=0.002)


def test_shrinkage_rejects_bad_records():
    with pytest.raises(ValueError):
        shrunk_rate(5, 3, 0.5, 20)


def _syn(pair_wins, pair_games, a=(500, 1000), b=(500, 1000), k=20.0):
    return pair_synergy(
        pair_wins=pair_wins,
        pair_games=pair_games,
        a_wins=a[0],
        a_games=a[1],
        b_wins=b[0],
        b_games=b[1],
        strength=k,
    )


def test_pair_at_expectation_has_zero_synergy():
    s = _syn(50, 100)
    assert s.expected == pytest.approx(0.5)
    assert s.synergy == pytest.approx(0.0)


def test_winning_pair_has_positive_synergy_and_losing_negative():
    assert _syn(70, 100).synergy > 0
    assert _syn(30, 100).synergy < 0


def test_small_samples_shrink_harder():
    small, big = _syn(4, 5), _syn(80, 100)  # both 80% together
    assert small.win_rate == big.win_rate
    assert 0 < small.synergy < big.synergy


def test_good_players_must_beat_a_higher_bar():
    # Two strong players winning 60% together is *below* what their solo records predict.
    s = _syn(60, 100, a=(650, 1000), b=(650, 1000))
    assert s.expected > 0.65
    assert s.synergy < 0


def test_shared_games_do_not_leak_into_baselines():
    # All of A's games are with B: no independent baseline, so fall back to 0.5.
    s = _syn(80, 100, a=(80, 100), b=(80, 100))
    assert s.expected == pytest.approx(0.5)
