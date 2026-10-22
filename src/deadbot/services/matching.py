"""Party-fit scoring. Pure and deterministic: no I/O, all inputs passed in.

Fit of candidate C for party P is a weighted mean of four components, each in [0, 1]
with 0.5 meaning "neutral / unknown":
  synergy       mean predicted synergy between C and each member (Phase 1: empirical, shrunk)
  role          how much of P's hero-type gap C's role mix fills
  availability  play-hour overlap with P, plus online now (presence) or usually-plays-now
  rank          closeness of C's rank to P's average
"""

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from deadbot.services.premade import ordered

ROLE_TYPES = ("brawler", "marksman", "mystic", "assassin")
SUBRANKS_PER_TIER = 6
_PLAYS_NOW_SHARE = 1.5 / 24  # 1.5x a uniform schedule


@dataclass(frozen=True)
class PlayerFeatures:
    account_id: int
    discord_id: int | None = None
    name: str = ""
    rank_badge: int | None = None
    hours: tuple[float, ...] | None = None  # 24 UTC-hour shares summing to 1
    roles: Mapping[str, float] = field(default_factory=dict)  # hero_type -> share of games
    games: int = 0
    online: bool | None = None  # None when presence is unknown


@dataclass(frozen=True)
class PairInfo:
    synergy: float
    games: int


PairLookup = Mapping[tuple[int, int], PairInfo]


@dataclass(frozen=True)
class Weights:
    synergy: float = 0.35
    role: float = 0.25
    availability: float = 0.25
    rank: float = 0.15

    def combine(self, syn: float, role: float, avail: float, rank: float) -> float:
        total = self.synergy + self.role + self.availability + self.rank
        return (
            self.synergy * syn + self.role * role + self.availability * avail + self.rank * rank
        ) / total


@dataclass(frozen=True)
class Scales:
    synergy: float = 0.10
    rank_gap: float = 12.0


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mean(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def rank_ordinal(badge: int | None) -> float | None:
    """Badge (tier*10 + subrank 1-6) -> subranks above Initiate 1. None if unranked."""
    if not badge:
        return None
    tier, sub = divmod(badge, 10)
    return float(tier * SUBRANKS_PER_TIER + max(sub, 1))


def hour_overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """Histogram intersection: 1 = identical schedules, 0 = never online together."""
    return sum(min(x, y) for x, y in zip(a, b, strict=True))


def mean_hours(players: Iterable[PlayerFeatures]) -> tuple[float, ...] | None:
    hs = [p.hours for p in players if p.hours]
    if not hs:
        return None
    return tuple(sum(col) / len(hs) for col in zip(*hs, strict=True))


def role_gap_fill(
    candidate: Mapping[str, float], party: Sequence[Mapping[str, float]]
) -> tuple[float, str | None]:
    """Score in [0,1] for how well `candidate` fills the party's missing hero types, plus the
    type it mainly covers (if any). A balanced party of n has n/4 of each type."""
    if not candidate:
        return 0.5, None
    ideal = (len(party) + 1) / len(ROLE_TYPES)
    gaps = {t: _clip01(ideal - sum(r.get(t, 0.0) for r in party)) for t in ROLE_TYPES}
    biggest = max(gaps.values())
    if biggest <= 0:
        return 0.5, None
    contrib = {t: candidate.get(t, 0.0) * gaps[t] / biggest for t in ROLE_TYPES}
    best = max(ROLE_TYPES, key=lambda t: contrib[t])
    return _clip01(sum(contrib.values())), best if contrib[best] >= 0.3 else None


@dataclass(frozen=True)
class Fit:
    score: float
    synergy: float | None  # mean synergy vs members we have history with
    synergy_known: int
    covered_role: str | None
    hour_overlap: float | None
    plays_now: bool
    online: bool | None
    rank_gap: float | None  # in subranks
    reason: str


def _signed_pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def build_reason(f: "Fit", party_size: int, scales: Scales) -> str:
    parts: list[str] = []
    if f.covered_role:
        parts.append(f"covers {f.covered_role.title()}")
    if f.synergy is not None and f.synergy_known:
        if f.synergy >= 0.01:
            parts.append(f"{_signed_pct(f.synergy)} synergy")
        elif f.synergy <= -0.02:
            parts.append(f"{_signed_pct(f.synergy)} synergy so far")
        if f.synergy_known == party_size and party_size > 1:
            parts.append("has played with everyone")
    if f.online:
        parts.append("online now")
    elif f.plays_now and f.online is None:  # presence, when known, beats habits
        parts.append("usually on at this hour")
    elif f.hour_overlap is not None and f.hour_overlap >= 0.5:
        parts.append("similar play hours")
    if f.rank_gap is not None:
        if f.rank_gap <= SUBRANKS_PER_TIER / 2:
            parts.append("similar rank")
        elif f.rank_gap >= scales.rank_gap / 2:
            parts.append(f"~{f.rank_gap / SUBRANKS_PER_TIER:.0f} tiers apart")
    return " · ".join(parts) or "no shared history yet"


def candidate_fit(
    c: PlayerFeatures,
    party: Sequence[PlayerFeatures],
    pairs: PairLookup,
    weights: Weights,
    scales: Scales,
    now_hour: int,
) -> Fit:
    # Synergy: unknown pairs count as neutral (0) so history only ever moves the score.
    known = [pairs[k] for m in party if (k := ordered(c.account_id, m.account_id)) in pairs]
    syn_all = _mean(
        pairs[k].synergy if (k := ordered(c.account_id, m.account_id)) in pairs else 0.0
        for m in party
    )
    syn_c = 0.5 if syn_all is None else _clip01(0.5 + syn_all / (2 * scales.synergy))

    role_c, covered = role_gap_fill(c.roles, [m.roles for m in party if m.roles])

    party_hours = mean_hours(party)
    overlap = hour_overlap(c.hours, party_hours) if c.hours and party_hours else None
    plays_now = bool(c.hours and c.hours[now_hour] >= _PLAYS_NOW_SHARE)
    if c.online is not None:
        now_signal = 1.0 if c.online else 0.0
    else:
        now_signal = (1.0 if plays_now else 0.0) if c.hours else 0.5
    avail_c = 0.6 * (0.5 if overlap is None else overlap) + 0.4 * now_signal

    c_rank = rank_ordinal(c.rank_badge)
    p_rank = _mean(r for m in party if (r := rank_ordinal(m.rank_badge)) is not None)
    gap = abs(c_rank - p_rank) if c_rank is not None and p_rank is not None else None
    rank_c = 0.5 if gap is None else 1 - _clip01(gap / scales.rank_gap)

    fit = Fit(
        score=weights.combine(syn_c, role_c, avail_c, rank_c),
        synergy=_mean(p.synergy for p in known),
        synergy_known=len(known),
        covered_role=covered,
        hour_overlap=overlap,
        plays_now=plays_now,
        online=c.online,
        rank_gap=gap,
        reason="",
    )
    return replace(fit, reason=build_reason(fit, len(party), scales))


@dataclass(frozen=True)
class Recommendation:
    player: PlayerFeatures
    fit: Fit


def greedy_fill(
    candidates: Sequence[PlayerFeatures],
    party: Sequence[PlayerFeatures],
    slots: int,
    pairs: PairLookup,
    weights: Weights,
    scales: Scales,
    now_hour: int,
) -> list[Recommendation]:
    """Pick the best candidate, add them to the party, re-score the rest, repeat."""
    party = list(party)
    pool = [c for c in candidates if c.account_id not in {m.account_id for m in party}]
    picks: list[Recommendation] = []
    for _ in range(max(0, slots)):
        if not pool:
            break
        scored = [(c, candidate_fit(c, party, pairs, weights, scales, now_hour)) for c in pool]
        best, fit = max(scored, key=lambda cf: (cf[1].score, -cf[0].account_id))
        picks.append(Recommendation(best, fit))
        party.append(best)
        pool.remove(best)
    return picks


@dataclass(frozen=True)
class PairLine:
    a: PlayerFeatures
    b: PlayerFeatures
    info: PairInfo | None


@dataclass(frozen=True)
class PartyFit:
    score: float
    synergy: float  # mean over all pairs, unknown = 0
    pairs: list[PairLine]
    role_balance: float  # 1 = even spread across hero types
    role_mix: dict[str, float]
    hour_overlap: float | None  # mean pairwise
    best_hour_utc: int | None
    rank_spread: float | None  # subranks between highest and lowest


def party_fit(
    members: Sequence[PlayerFeatures], pairs: PairLookup, weights: Weights, scales: Scales
) -> PartyFit:
    combos = list(itertools.combinations(members, 2))
    lines = [PairLine(a, b, pairs.get(ordered(a.account_id, b.account_id))) for a, b in combos]
    syn = _mean(line.info.synergy if line.info else 0.0 for line in lines) or 0.0
    syn_c = _clip01(0.5 + syn / (2 * scales.synergy))

    totals = {t: sum(m.roles.get(t, 0.0) for m in members) for t in ROLE_TYPES}
    total = sum(totals.values())
    mix = {t: v / total for t, v in totals.items()} if total else {}
    # L1 distance from an even split is at most 1.5 (everyone on one type).
    balance = 1 - sum(abs(mix[t] - 1 / len(ROLE_TYPES)) for t in ROLE_TYPES) / 1.5 if mix else 0.5

    timed = [m for m in members if m.hours]
    overlap = _mean(hour_overlap(a.hours, b.hours) for a, b in itertools.combinations(timed, 2))
    best_hour = None
    if timed:
        best_hour = max(
            range(24),
            key=lambda h: (min(m.hours[h] for m in timed), sum(m.hours[h] for m in timed)),
        )

    ranks = [r for m in members if (r := rank_ordinal(m.rank_badge)) is not None]
    spread = max(ranks) - min(ranks) if len(ranks) >= 2 else None
    rank_c = 0.5 if spread is None else 1 - _clip01(spread / scales.rank_gap)

    return PartyFit(
        score=weights.combine(syn_c, balance, 0.5 if overlap is None else overlap, rank_c),
        synergy=syn,
        pairs=lines,
        role_balance=balance,
        role_mix=mix,
        hour_overlap=overlap,
        best_hour_utc=best_hour,
        rank_spread=spread,
    )
