"""Which sources exist, and which ones can actually run right now."""

from __future__ import annotations

from aadfs.rankings.sources import (
    CsvRankings, ESPNRankings, FantasyNerdsRankings, NFLComRankings,
    RecentFormModel, SleeperRankings, VegasTeamTotals,
)
from aadfs.sources.base import HttpCache

#: Built-in sources, in the order they are reported.
BUILTIN = (
    SleeperRankings,
    ESPNRankings,
    NFLComRankings,
    FantasyNerdsRankings,
    RecentFormModel,
    VegasTeamTotals,
)


def all_sources(cache: HttpCache | None = None,
                weights: dict[str, float] | None = None) -> list:
    """Every built-in source, whether or not it is currently usable."""
    cache = cache if cache is not None else HttpCache()
    weights = weights or {}
    built = []
    for cls in BUILTIN:
        source = cls(cache=cache) if cls is not RecentFormModel else cls()
        if source.name in weights:
            source.weight = weights[source.name]
        built.append(source)
    return built


def usable_sources(cache: HttpCache | None = None,
                   weights: dict[str, float] | None = None) -> list:
    """Only the sources whose prerequisites are met."""
    return [s for s in all_sources(cache, weights) if s.available()[0]]


def with_csv_sources(sources: list, paths: list[str] | None,
                     weight: float = 1.5) -> list:
    """Append your own CSV exports.

    They default to a higher weight than the free feeds: a source you chose to
    pay for should generally outrank one that costs nothing, at least until the
    accuracy scoring says otherwise.
    """
    for path in paths or []:
        sources.append(CsvRankings(path, weight=weight))
    return sources
