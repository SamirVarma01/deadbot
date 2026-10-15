"""Empirical pairwise synergy with Bayesian shrinkage (Phase 1, no model).

For a pair (a, b) on the same team:
  p_a, p_b   = each player's win rate *excluding* games played together (no leakage)
  expected   = sigmoid(logit p_a + logit p_b - logit 0.5): their individual edges add up
  shrunk     = (wins_together + k * expected) / (games_together + k)
  synergy    = shrunk - expected   (positive: they win more together than expected)
"""

import math
from dataclasses import dataclass

_EPS = 0.02  # keep logits finite for tiny or perfect records
MIN_BASELINE_GAMES = 10


def _clip(p: float) -> float:
    return min(1 - _EPS, max(_EPS, p))


def logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def leave_pair_out_rate(
    total_wins: int | None, total_games: int | None, pair_wins: int, pair_games: int
) -> float | None:
    """Win rate without the shared games, or None if too few games remain."""
    if total_games is None or total_wins is None:
        return None
    games = total_games - pair_games
    wins = total_wins - pair_wins
    if games < MIN_BASELINE_GAMES or wins < 0:
        return None
    return wins / games


def expected_pair_win_rate(p_a: float | None, p_b: float | None) -> float:
    p_a = 0.5 if p_a is None else p_a
    p_b = 0.5 if p_b is None else p_b
    return sigmoid(logit(p_a) + logit(p_b))  # logit(0.5) == 0


def shrunk_rate(wins: int, games: int, prior: float, strength: float) -> float:
    if games < 0 or wins < 0 or wins > games:
        raise ValueError(f"bad record {wins}/{games}")
    return (wins + strength * prior) / (games + strength)


@dataclass(frozen=True)
class PairSynergy:
    games: int
    wins: int
    expected: float
    shrunk: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def synergy(self) -> float:
        return self.shrunk - self.expected


def pair_synergy(
    *,
    pair_wins: int,
    pair_games: int,
    a_wins: int | None,
    a_games: int | None,
    b_wins: int | None,
    b_games: int | None,
    strength: float,
) -> PairSynergy:
    p_a = leave_pair_out_rate(a_wins, a_games, pair_wins, pair_games)
    p_b = leave_pair_out_rate(b_wins, b_games, pair_wins, pair_games)
    expected = expected_pair_win_rate(p_a, p_b)
    return PairSynergy(
        games=pair_games,
        wins=pair_wins,
        expected=expected,
        shrunk=shrunk_rate(pair_wins, pair_games, expected, strength),
    )
