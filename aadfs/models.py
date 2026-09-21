"""Core domain objects: players, slates, projections and lineups."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from aadfs.scoring import RosterRules


@dataclass
class Player:
    """One selectable roster entry on a FanDuel slate."""

    player_id: str
    name: str
    position: str
    team: str
    opponent: str | None = None
    salary: int = 0
    game_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    kickoff: str | None = None

    # FanDuel's own numbers, straight off the salary export.
    fd_projection: float | None = None
    injury_status: str | None = None
    injury_detail: str | None = None
    roster_position: str | None = None

    # Filled in by the consensus step.
    projection: float = 0.0
    floor: float = 0.0
    ceiling: float = 0.0
    stdev: float = 0.0
    source_count: int = 0
    source_values: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def value(self) -> float:
        """Projected points per $1000 of salary."""
        if not self.salary:
            return 0.0
        return self.projection / (self.salary / 1000.0)

    @property
    def is_questionable(self) -> bool:
        status = (self.injury_status or "").strip().upper()
        return status in {"Q", "QUESTIONABLE", "D", "DOUBTFUL", "GTD"}

    @property
    def is_out(self) -> bool:
        status = (self.injury_status or "").strip().upper()
        return status in {"O", "OUT", "IR", "NA", "SUSP", "PUP"}

    def game_key(self) -> str:
        """A stable identifier for the game this player is in."""
        if self.game_id:
            return self.game_id
        teams = sorted(filter(None, [self.team, self.opponent]))
        return "@".join(teams) if teams else self.player_id


@dataclass
class Slate:
    """A set of players available in one FanDuel contest."""

    players: list[Player]
    name: str = "slate"
    season: int | None = None
    week: int | None = None
    rules: RosterRules = field(default_factory=RosterRules)
    source_file: str | None = None

    def __post_init__(self) -> None:
        self._index = {p.player_id: p for p in self.players}

    def by_id(self, player_id: str) -> Player:
        return self._index[player_id]

    def reindex(self) -> None:
        self._index = {p.player_id: p for p in self.players}

    @property
    def teams(self) -> set[str]:
        return {p.team for p in self.players if p.team}

    @property
    def games(self) -> set[str]:
        return {p.game_key() for p in self.players}

    def playable(self) -> list[Player]:
        """Players that can legally and sensibly be rostered."""
        return [p for p in self.players if p.salary > 0 and not p.is_out]


@dataclass
class Lineup:
    """A completed nine-man FanDuel roster."""

    players: list[Player]
    rules: RosterRules = field(default_factory=RosterRules)
    label: str = ""

    @property
    def salary(self) -> int:
        return sum(p.salary for p in self.players)

    @property
    def salary_remaining(self) -> int:
        return self.rules.salary_cap - self.salary

    @property
    def projection(self) -> float:
        return round(sum(p.projection for p in self.players), 2)

    @property
    def floor(self) -> float:
        return round(sum(p.floor for p in self.players), 2)

    @property
    def ceiling(self) -> float:
        return round(sum(p.ceiling for p in self.players), 2)

    @property
    def stdev(self) -> float:
        """Independent-variance approximation; the simulator handles the real thing."""
        return round(math.sqrt(sum(p.stdev**2 for p in self.players)), 2)

    @property
    def team_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.players:
            counts[p.team] = counts.get(p.team, 0) + 1
        return counts

    @property
    def game_count(self) -> int:
        return len({p.game_key() for p in self.players})

    #: FanDuel's roster slots, in upload order.
    SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF")

    def slotted(self) -> list[tuple[str, Player]]:
        """Players paired with the FanDuel roster slot each one fills.

        The extra body among RB/WR/TE is the FLEX. Within a position the
        highest-projected players take the fixed slots, so the FLEX is the
        marginal one -- which is how the lineup reads most naturally.
        """
        buckets: dict[str, list[Player]] = {}
        for p in self.players:
            buckets.setdefault(p.position, []).append(p)
        for group in buckets.values():
            group.sort(key=lambda p: (-p.projection, -p.salary, p.name))

        qb = buckets.get("QB", [])
        rb = buckets.get("RB", [])
        wr = buckets.get("WR", [])
        te = buckets.get("TE", [])
        dst = buckets.get("DST", [])

        flex = rb[2:] + wr[3:] + te[1:]
        ordered = qb[:1] + rb[:2] + wr[:3] + te[:1] + flex[:1] + dst[:1]
        return list(zip(self.SLOTS, ordered))

    def ordered(self) -> list[Player]:
        """Players in FanDuel's roster-slot order, with the FLEX resolved."""
        return [player for _, player in self.slotted()]

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(p.player_id for p in self.players))

    def overlap(self, other: "Lineup") -> int:
        return len(set(self.ids()) & set(other.ids()))
