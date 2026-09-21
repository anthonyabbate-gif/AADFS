"""FanDuel NFL scoring.

FanDuel NFL is half-PPR with no kicker in the Classic roster. These values are
kept in one place so that a rules change only needs editing here.

Always sanity-check against FanDuel's live scoring page before a season: DFS
sites adjust rules between seasons more often than people expect.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- offensive scoring -------------------------------------------------------

PASS_YD = 0.04  # 1 point per 25 passing yards
PASS_TD = 4.0
INTERCEPTION = -1.0
RUSH_YD = 0.1
RUSH_TD = 6.0
REC = 0.5  # half PPR
REC_YD = 0.1
REC_TD = 6.0
FUMBLE_LOST = -2.0
TWO_POINT = 2.0
RETURN_TD = 6.0
OWN_FUMBLE_REC_TD = 6.0

# --- defense / special teams -------------------------------------------------

DST_SACK = 1.0
DST_INT = 2.0
DST_FUM_REC = 2.0
DST_TD = 6.0
DST_SAFETY = 2.0
DST_BLOCKED_KICK = 2.0
DST_XP_RETURN = 2.0

# Points allowed buckets, evaluated top-down: (inclusive_max_points, score)
DST_POINTS_ALLOWED = [
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (20, 1.0),
    (27, 0.0),
    (34, -1.0),
    (10**9, -4.0),
]


def dst_points_allowed_score(points_allowed: float) -> float:
    """Score the points-allowed component of a D/ST performance."""
    for ceiling, value in DST_POINTS_ALLOWED:
        if points_allowed <= ceiling:
            return value
    return DST_POINTS_ALLOWED[-1][1]


@dataclass
class StatLine:
    """A raw box score for one player, in the fields FanDuel actually scores."""

    pass_yd: float = 0.0
    pass_td: float = 0.0
    interceptions: float = 0.0
    rush_yd: float = 0.0
    rush_td: float = 0.0
    receptions: float = 0.0
    rec_yd: float = 0.0
    rec_td: float = 0.0
    fumbles_lost: float = 0.0
    two_point_conversions: float = 0.0
    return_td: float = 0.0
    own_fumble_rec_td: float = 0.0

    # D/ST
    dst_sacks: float = 0.0
    dst_interceptions: float = 0.0
    dst_fumble_recoveries: float = 0.0
    dst_tds: float = 0.0
    dst_safeties: float = 0.0
    dst_blocked_kicks: float = 0.0
    dst_xp_returns: float = 0.0
    dst_points_allowed: float | None = None


def score_statline(s: StatLine) -> float:
    """FanDuel fantasy points for a single stat line."""
    total = (
        s.pass_yd * PASS_YD
        + s.pass_td * PASS_TD
        + s.interceptions * INTERCEPTION
        + s.rush_yd * RUSH_YD
        + s.rush_td * RUSH_TD
        + s.receptions * REC
        + s.rec_yd * REC_YD
        + s.rec_td * REC_TD
        + s.fumbles_lost * FUMBLE_LOST
        + s.two_point_conversions * TWO_POINT
        + s.return_td * RETURN_TD
        + s.own_fumble_rec_td * OWN_FUMBLE_REC_TD
        + s.dst_sacks * DST_SACK
        + s.dst_interceptions * DST_INT
        + s.dst_fumble_recoveries * DST_FUM_REC
        + s.dst_tds * DST_TD
        + s.dst_safeties * DST_SAFETY
        + s.dst_blocked_kicks * DST_BLOCKED_KICK
        + s.dst_xp_returns * DST_XP_RETURN
    )
    if s.dst_points_allowed is not None:
        total += dst_points_allowed_score(s.dst_points_allowed)
    return round(total, 2)


# --- roster construction rules ----------------------------------------------

QB, RB, WR, TE, DST = "QB", "RB", "WR", "TE", "DST"
FLEX_POSITIONS = (RB, WR, TE)


@dataclass
class RosterRules:
    """FanDuel NFL Classic roster construction.

    `max_per_team` and `min_games` mirror FanDuel's stated limits but are
    configurable because they are the rules most likely to drift season to
    season. Verify them against the contest's own rules page before you trust a
    lineup to be enterable.
    """

    salary_cap: int = 60_000
    roster_size: int = 9
    # position -> (minimum, maximum) once FLEX is accounted for
    slots: dict[str, tuple[int, int]] = field(
        default_factory=lambda: {
            QB: (1, 1),
            RB: (2, 3),
            WR: (3, 4),
            TE: (1, 2),
            DST: (1, 1),
        }
    )
    flex_group_total: int = 7  # RB + WR + TE always sums to exactly this
    max_per_team: int = 4
    dst_counts_toward_team_limit: bool = True
    min_games: int = 3

    def flex_positions(self) -> tuple[str, ...]:
        return FLEX_POSITIONS
