"""Compose ingest, consensus, optimisation and simulation into one flow.

Both the web app and the CLI drive this module, so that a lineup built from the
browser and one built from a cron job go through exactly the same steps.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from aadfs.config import cache_dir as cache_directory
from aadfs.consensus import ConsensusReport, build_consensus
from aadfs.ingest.fanduel import parse_salary_csv
from aadfs.models import Slate
from aadfs.names import normalize_name
from aadfs.optimizer import OptimizationResult, OptimizerConfig, optimize
from aadfs.simulate import ContestSettings, SimulationResult, build_field, evaluate_lineup
from aadfs.sources.base import HttpCache, SourceResult
from aadfs.sources.csv_source import parse_projection_csv
from aadfs.sources.espn import ESPNSource
from aadfs.sources.nflverse import (
    DEFAULT_POSITION_SD, calibrate_position_variance, load_history, player_variance,
)
from aadfs.sources.sleeper import SleeperSource

#: The NFL regular season starts in the first full week of September. Used only
#: to guess a default season/week; both are overridable everywhere.
_SEASON_START_MONTH = 9


def guess_season_and_week(today: dt.date | None = None) -> tuple[int, int]:
    """Best guess at the current NFL season and week."""
    today = today or dt.date.today()
    season = today.year if today.month >= _SEASON_START_MONTH else today.year - 1

    # Week 1 kicks off on the Thursday after Labor Day; approximate with the
    # first Tuesday of September as the boundary.
    september = dt.date(season, 9, 1)
    offset = (1 - september.weekday()) % 7  # first Tuesday
    week_one_start = september + dt.timedelta(days=offset)
    if today < week_one_start:
        return season, 1
    week = (today - week_one_start).days // 7 + 1
    return season, max(1, min(week, 18))


@dataclass
class ProjectionBundle:
    """Everything gathered before a lineup is built."""

    slate: Slate
    consensus: ConsensusReport
    source_results: list[SourceResult] = field(default_factory=list)
    position_sd: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_POSITION_SD))
    history_seasons: list[int] = field(default_factory=list)
    history_error: str | None = None

    def source_summary(self) -> list[dict]:
        matched = {r.source: r for r in self.consensus.sources}
        summary = []
        for result in self.source_results:
            report = matched.get(result.source)
            summary.append(
                {
                    "source": result.source,
                    "ok": result.ok,
                    "error": result.error,
                    "rows": result.count,
                    "matched": report.matched if report else 0,
                    "unmatched": report.unmatched_count if report else 0,
                    "unmatched_sample": (report.unmatched[:8] if report else []),
                    "from_cache": result.from_cache,
                }
            )
        return summary


def default_sources(cache: HttpCache | None = None) -> list:
    """The projection sources fetched by default."""
    cache = cache or HttpCache()
    return [SleeperSource(cache=cache), ESPNSource(cache=cache)]


def load_variance(
    seasons: list[int] | None = None,
    cache_dir: str | Path | None = None,
) -> tuple[dict[str, float], dict[str, float], list[int], str | None]:
    """Load historical variance, falling back to priors if the data is missing."""
    seasons = seasons or []
    if not seasons:
        return {}, dict(DEFAULT_POSITION_SD), [], None
    try:
        history = load_history(seasons, cache_dir=cache_dir or cache_directory())
        return (
            player_variance(history),
            calibrate_position_variance(history),
            seasons,
            None,
        )
    except Exception as exc:
        return {}, dict(DEFAULT_POSITION_SD), [], f"{type(exc).__name__}: {exc}"


def build_projections(
    salary_file: str | Path,
    *,
    season: int | None = None,
    week: int | None = None,
    sources: list | None = None,
    projection_files: list[str | Path] | None = None,
    weights: dict[str, float] | None = None,
    history_seasons: list[int] | None = None,
    cache_dir: str | Path | None = None,
    fetch_remote: bool = True,
) -> ProjectionBundle:
    """Read a salary file, gather projections, and blend them onto the slate."""
    guessed_season, guessed_week = guess_season_and_week()
    season = season or guessed_season
    week = week or guessed_week

    slate = parse_salary_csv(salary_file)
    slate.season, slate.week = season, week

    results: list[SourceResult] = []
    if fetch_remote:
        for source in sources if sources is not None else default_sources(
            HttpCache(cache_dir or cache_directory())
        ):
            results.append(source.fetch(season, week))

    for path in projection_files or []:
        results.append(parse_projection_csv(path, source_name=Path(path).stem))

    player_sd, position_sd, seasons_used, history_error = load_variance(
        history_seasons, cache_dir or cache_directory()
    )
    report = build_consensus(
        slate, results, weights=weights,
        historical_sd=player_sd, position_sd=position_sd,
    )

    return ProjectionBundle(
        slate=slate,
        consensus=report,
        source_results=results,
        position_sd=position_sd,
        history_seasons=seasons_used,
        history_error=history_error,
    )


@dataclass
class BuildOutcome:
    """Lineups plus the simulation verdict on each."""

    optimization: OptimizationResult
    simulations: list[SimulationResult] = field(default_factory=list)
    contest: ContestSettings = field(default_factory=ContestSettings)


def build_lineups(
    slate: Slate,
    config: OptimizerConfig | None = None,
    contest: ContestSettings | None = None,
    *,
    simulate: bool = True,
    n_sims: int = 10_000,
    seed: int | None = 11,
) -> BuildOutcome:
    """Optimise lineups and score each one against a simulated field."""
    config = config or OptimizerConfig()
    contest = contest or ContestSettings()
    optimization = optimize(slate, config)

    simulations: list[SimulationResult] = []
    if simulate and optimization.lineups:
        # One shared field, so lineups are compared on identical opponents.
        field_lineups = build_field(slate, contest, seed=(seed or 0) + 101)
        for lineup in optimization.lineups:
            simulations.append(
                evaluate_lineup(
                    lineup, slate, contest,
                    n_sims=n_sims, seed=seed, field=field_lineups,
                )
            )

    return BuildOutcome(optimization=optimization, simulations=simulations, contest=contest)


def latest_salary_file(directory: str | Path = "data/salaries") -> Path | None:
    """The most recently modified salary CSV in a directory."""
    directory = Path(directory)
    if not directory.exists():
        return None
    candidates = sorted(
        directory.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None
