"""The Saturday scan.

Run this the day before a slate. It pulls every configured projection source,
blends them against the week's salary file, and writes three artefacts:

* `consensus_<season>_wk<week>.csv` — one blended projection per player
* `scan_<season>_wk<week>.json`     — what each source returned and what failed
* `lineups_<season>_wk<week>.csv`   — optional starting lineups

The scan never raises on a dead source. A source that fails is recorded in the
report with its error, and the blend proceeds on what did arrive, because a
half-populated projection set on Saturday is far more useful than a traceback.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from aadfs.ingest.fanduel import lineups_to_readable_csv
from aadfs.optimizer import OptimizerConfig
from aadfs.pipeline import (
    ProjectionBundle, build_lineups, build_projections, guess_season_and_week,
    latest_salary_file,
)
from aadfs.simulate import ContestSettings

from aadfs.config import cache_dir as scan_cache_dir, projections_dir

DEFAULT_OUTPUT_DIR = projections_dir()


def write_consensus_csv(bundle: ProjectionBundle, path: Path) -> Path:
    """Write the blended projections, including every source's own number."""
    path.parent.mkdir(parents=True, exist_ok=True)
    source_names = sorted(
        {name for p in bundle.slate.players for name in p.source_values}
    )
    header = [
        "player_id", "name", "position", "team", "opponent", "salary",
        "projection", "floor", "ceiling", "stdev", "value", "sources",
        "injury_status",
    ] + [f"src_{name}" for name in source_names]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for player in sorted(bundle.slate.players, key=lambda p: -p.projection):
            writer.writerow(
                [
                    player.player_id, player.name, player.position, player.team,
                    player.opponent or "", player.salary,
                    round(player.projection, 2), round(player.floor, 2),
                    round(player.ceiling, 2), round(player.stdev, 2),
                    round(player.value, 2), player.source_count,
                    player.injury_status or "",
                ]
                + [player.source_values.get(name, "") for name in source_names]
            )
    return path


def write_report(bundle: ProjectionBundle, path: Path, extra: dict | None = None) -> Path:
    """Write the machine-readable scan report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "season": bundle.slate.season,
        "week": bundle.slate.week,
        "slate": bundle.slate.name,
        "salary_file": bundle.slate.source_file,
        "players": len(bundle.slate.players),
        "players_with_projection": bundle.consensus.players_with_projection,
        "players_without_projection": bundle.consensus.players_without_projection[:40],
        "position_stdev": bundle.position_sd,
        "history_seasons": bundle.history_seasons,
        "history_error": bundle.history_error,
        "sources": bundle.source_summary(),
    }
    report.update(extra or {})
    path.write_text(json.dumps(report, indent=2))
    return path


def run_scan(
    salary_file: str | Path | None = None,
    *,
    season: int | None = None,
    week: int | None = None,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    history_seasons: list[int] | None = None,
    build_starting_lineups: int = 3,
    fetch_remote: bool = True,
    cache_dir: str | Path | None = None,
) -> dict:
    """Run the full Saturday scan and return a summary of what it produced."""
    season, week = (season, week) if season and week else guess_season_and_week()

    salary_file = salary_file or latest_salary_file()
    if salary_file is None:
        return {
            "ok": False,
            "error": (
                "No FanDuel salary file found. Download the players list from the "
                "contest entry screen and save it in data/salaries/."
            ),
        }

    bundle = build_projections(
        salary_file,
        season=season,
        week=week,
        history_seasons=history_seasons or [season - 1],
        cache_dir=cache_dir or scan_cache_dir(),
        fetch_remote=fetch_remote,
    )

    output_dir = Path(output_dir)
    stem = f"{season}_wk{week:02d}"
    consensus_path = write_consensus_csv(bundle, output_dir / f"consensus_{stem}.csv")

    lineup_path = None
    lineup_summary: list[dict] = []
    if build_starting_lineups > 0 and bundle.consensus.players_with_projection > 0:
        outcome = build_lineups(
            bundle.slate,
            OptimizerConfig(n_lineups=build_starting_lineups),
            ContestSettings(),
        )
        if outcome.optimization.lineups:
            lineup_path = output_dir / f"lineups_{stem}.csv"
            lineup_path.write_text(lineups_to_readable_csv(outcome.optimization.lineups))
            for lineup, simulation in zip(outcome.optimization.lineups, outcome.simulations):
                lineup_summary.append(
                    {
                        "label": lineup.label,
                        "salary": lineup.salary,
                        "projection": lineup.projection,
                        "floor": lineup.floor,
                        "players": [p.name for p in lineup.ordered()],
                        **simulation.as_dict(),
                    }
                )

    report_path = write_report(
        bundle,
        output_dir / f"scan_{stem}.json",
        extra={"lineups": lineup_summary, "relaxations": []},
    )

    failed = [s.source for s in bundle.consensus.failed_sources]
    return {
        "ok": True,
        "season": season,
        "week": week,
        "consensus_csv": str(consensus_path),
        "report_json": str(report_path),
        "lineups_csv": str(lineup_path) if lineup_path else None,
        "players_with_projection": bundle.consensus.players_with_projection,
        "sources_ok": bundle.consensus.ok_sources,
        "sources_failed": failed,
        "lineups": lineup_summary,
    }


def main() -> int:
    """Entry point for `python -m aadfs.jobs.saturday_scan`."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the weekly projection scan.")
    parser.add_argument("--salary-file", help="FanDuel players-list CSV")
    parser.add_argument("--season", type=int)
    parser.add_argument("--week", type=int)
    parser.add_argument("--lineups", type=int, default=3)
    parser.add_argument("--offline", action="store_true", help="Skip remote sources")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    summary = run_scan(
        args.salary_file,
        season=args.season,
        week=args.week,
        output_dir=args.output_dir,
        build_starting_lineups=args.lineups,
        fetch_remote=not args.offline,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
