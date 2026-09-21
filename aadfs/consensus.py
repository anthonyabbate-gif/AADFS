"""Blend several projection sources into one number per player.

The output is not just a mean. For cash games the spread matters at least as
much, so each player also gets a standard deviation built from two things:

* how much the sources disagree about them this week, and
* how much that player's real scoring has bounced around historically.

A player everybody agrees on with a steady history gets a tight distribution and
is a natural cash play. A player the sources split on gets a wide one and has to
clear a higher bar to make the lineup.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from aadfs.distribution import floor_ceiling
from aadfs.models import Slate
from aadfs.names import PlayerMatcher, normalize_name
from aadfs.sources.base import SourceResult
from aadfs.sources.nflverse import DEFAULT_POSITION_SD

#: Typical weekly score by position, used to scale a variance prior to a
#: player's projected workload. A WR projected for 5 points is not as volatile
#: in absolute terms as one projected for 18.
POSITION_REFERENCE_MEAN = {"QB": 17.0, "RB": 11.0, "WR": 10.0, "TE": 7.5, "DST": 7.0}

#: Floor on the modelled spread, so nobody looks like a risk-free bet.
MIN_RELATIVE_SD = 0.28


@dataclass
class MatchReport:
    """What happened when a source was matched onto the slate."""

    source: str
    matched: int = 0
    unmatched: list[str] = field(default_factory=list)
    ok: bool = True
    error: str | None = None

    @property
    def unmatched_count(self) -> int:
        return len(self.unmatched)


@dataclass
class ConsensusReport:
    """Diagnostics for a whole consensus build."""

    sources: list[MatchReport] = field(default_factory=list)
    players_with_projection: int = 0
    players_without_projection: list[str] = field(default_factory=list)

    @property
    def ok_sources(self) -> list[str]:
        return [s.source for s in self.sources if s.ok and s.matched > 0]

    @property
    def failed_sources(self) -> list[MatchReport]:
        return [s for s in self.sources if not s.ok or s.matched == 0]


def _base_sd(position: str, projection: float,
             historical_sd: float | None,
             position_sd: dict[str, float]) -> float:
    """A player's own volatility, before source disagreement is added."""
    reference = POSITION_REFERENCE_MEAN.get(position, 10.0)
    prior = position_sd.get(position, DEFAULT_POSITION_SD.get(position, 6.0))

    if historical_sd is not None and historical_sd > 0:
        # Scale the player's measured spread toward this week's projected role.
        scaled = historical_sd * math.sqrt(max(projection, 0.5) / max(reference, 1.0))
        # Keep it anchored: never let one noisy sample run away from the prior.
        sd = 0.65 * scaled + 0.35 * prior * math.sqrt(max(projection, 0.5) / max(reference, 1.0))
    else:
        sd = prior * math.sqrt(max(projection, 0.5) / max(reference, 1.0))

    return max(sd, MIN_RELATIVE_SD * max(projection, 1.0))


def build_consensus(
    slate: Slate,
    results: list[SourceResult],
    *,
    weights: dict[str, float] | None = None,
    historical_sd: dict[str, float] | None = None,
    position_sd: dict[str, float] | None = None,
    use_fanduel_fppg: bool = True,
    fanduel_fppg_weight: float = 0.35,
) -> ConsensusReport:
    """Write blended projections onto `slate` in place and report on the build."""
    weights = weights or {}
    historical_sd = historical_sd or {}
    position_sd = position_sd or dict(DEFAULT_POSITION_SD)

    matcher = PlayerMatcher(slate.players)
    report = ConsensusReport()

    # player_id -> source -> (points, weight)
    gathered: dict[str, dict[str, tuple[float, float]]] = {}
    extras: dict[str, dict[str, float]] = {}

    for result in results:
        match_report = MatchReport(source=result.source, ok=result.ok, error=result.error)
        if not result.ok:
            report.sources.append(match_report)
            continue

        weight = weights.get(result.source, 1.0)
        for row in result.rows:
            points = row.fanduel_points()
            if points is None:
                continue
            match = matcher.match(row.name, row.team, row.position)
            if match.target_id is None:
                match_report.unmatched.append(row.name)
                continue
            match_report.matched += 1
            gathered.setdefault(match.target_id, {})[result.source] = (float(points), weight)
            for key in ("floor", "ceiling", "stdev", "ownership"):
                if key in row.extra:
                    extras.setdefault(match.target_id, {})[key] = float(row.extra[key])

        report.sources.append(match_report)

    for player in slate.players:
        contributions = dict(gathered.get(player.player_id, {}))

        if use_fanduel_fppg and player.fd_projection is not None and player.fd_projection > 0:
            contributions.setdefault(
                "fanduel_fppg", (float(player.fd_projection), fanduel_fppg_weight)
            )

        player.source_values = {name: round(value, 2) for name, (value, _) in contributions.items()}
        player.source_count = len([n for n in contributions if n != "fanduel_fppg"])

        if not contributions:
            player.projection = 0.0
            player.floor = 0.0
            player.ceiling = 0.0
            player.stdev = 0.0
            if player.salary > 0 and not player.is_out:
                report.players_without_projection.append(f"{player.name} ({player.position})")
            continue

        total_weight = sum(w for _, w in contributions.values()) or 1.0
        mean = sum(v * w for v, w in contributions.values()) / total_weight

        # Weighted spread across sources: this week's disagreement.
        if len(contributions) > 1:
            variance = sum(
                w * (v - mean) ** 2 for v, w in contributions.values()
            ) / total_weight
            disagreement = math.sqrt(max(variance, 0.0))
        else:
            # A lone source tells us nothing about agreement; assume some.
            disagreement = 0.15 * max(mean, 1.0)

        own_sd = historical_sd.get(normalize_name(player.name))
        base = _base_sd(player.position, mean, own_sd, position_sd)
        sd = math.sqrt(base**2 + disagreement**2)

        supplied_sd = extras.get(player.player_id, {}).get("stdev")
        if supplied_sd and supplied_sd > 0:
            sd = math.sqrt(0.5 * sd**2 + 0.5 * float(supplied_sd) ** 2)

        player.projection = round(mean, 2)
        player.stdev = round(sd, 2)
        low, high = floor_ceiling(mean, sd, player.position)
        supplied = extras.get(player.player_id, {})
        player.floor = round(supplied.get("floor", low), 2)
        player.ceiling = round(supplied.get("ceiling", high), 2)
        if "ownership" in supplied:
            player.notes.append(f"proj. ownership {supplied['ownership']:.1f}%")

        report.players_with_projection += 1

    slate.reindex()
    return report
