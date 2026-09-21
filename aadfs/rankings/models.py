"""Types for ranking consensus.

A *ranking* and a *projection* are different claims. A projection says "14.2
points"; a ranking says "better than that guy". Sources disagree about scale far
more than they disagree about order, so blending them as ranks is both more
robust and more honest than averaging numbers that were never on the same scale.

Everything here is built around that: sources contribute an order, the consensus
is an aggregated order, and how much the sources disagree about a player is
carried through as a first-class value rather than averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RankEntry:
    """One source's opinion about one player, in one week."""

    name: str
    position: str | None = None
    team: str | None = None
    #: Rank within position, 1 = best. Derived from points when not supplied.
    rank: float | None = None
    #: Projected points, if the source gives them. Kept for reference only.
    points: float | None = None
    opponent: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class SourceRanking:
    """Everything one source said this week, plus how the fetch went."""

    source: str
    entries: list[RankEntry] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    from_cache: bool = False
    #: How much this source counts in the blend.
    weight: float = 1.0
    #: Where the numbers came from, shown in the UI so a reader can judge them.
    kind: str = "projection"  # projection | ranking | market | model
    url: str | None = None

    @property
    def count(self) -> int:
        return len(self.entries)


@dataclass
class ConsensusPlayer:
    """A player's aggregated position across every source that covered them."""

    name: str
    position: str
    team: str | None = None
    opponent: str | None = None

    consensus_rank: float = 0.0        # position rank after aggregation, 1 = best
    overall_rank: int = 0              # rank among all players at this position
    tier: int = 0

    #: Spread of this player's rank across sources. High means the sources
    #: genuinely disagree, which is information, not noise.
    rank_stdev: float = 0.0
    best_rank: float | None = None
    worst_rank: float | None = None

    source_count: int = 0
    source_ranks: dict[str, float] = field(default_factory=dict)
    source_points: dict[str, float] = field(default_factory=dict)
    mean_points: float | None = None

    #: Set when a source is a long way from the others on this player.
    outliers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def disagreement(self) -> str:
        """A plain-language read on how much the sources differ."""
        if self.source_count < 2:
            return "single source"
        if self.rank_stdev <= 2.0:
            return "tight"
        if self.rank_stdev <= 5.0:
            return "some spread"
        return "wide"

    @property
    def rank_range(self) -> float:
        if self.best_rank is None or self.worst_rank is None:
            return 0.0
        return round(self.worst_rank - self.best_rank, 1)


@dataclass
class ConsensusBoard:
    """The week's full consensus, by position."""

    season: int
    week: int
    players: list[ConsensusPlayer] = field(default_factory=list)
    sources: list[SourceRanking] = field(default_factory=list)
    generated_at: str = ""

    def by_position(self, position: str) -> list[ConsensusPlayer]:
        return [p for p in self.players if p.position == position]

    @property
    def positions(self) -> list[str]:
        order = ["QB", "RB", "WR", "TE", "K", "DST"]
        present = {p.position for p in self.players}
        return [p for p in order if p in present] + sorted(present - set(order))

    @property
    def ok_sources(self) -> list[SourceRanking]:
        return [s for s in self.sources if s.ok and s.count > 0]

    @property
    def failed_sources(self) -> list[SourceRanking]:
        return [s for s in self.sources if not s.ok or s.count == 0]
