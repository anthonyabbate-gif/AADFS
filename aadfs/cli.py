"""Command-line entry points.

The web app is the main way to use this, but the CLI matters for the pieces you
want to automate — above all the Saturday scan, which is meant to run from cron.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aadfs.ingest.fanduel import lineups_to_entry_csv, lineups_to_readable_csv


def _print_lineup(lineup, simulation=None) -> None:
    header = (
        f"{lineup.label}  ${lineup.salary:,} "
        f"(${lineup.salary_remaining:,} left)  proj {lineup.projection}  "
        f"floor {lineup.floor}  ceil {lineup.ceiling}  {lineup.game_count} games"
    )
    print(header)
    print("-" * len(header))
    for slot, player in lineup.slotted():
        flag = ""
        if player.is_questionable:
            flag = f"  [{player.injury_status}]"
        print(
            f"  {slot:<5} {player.name:<24} {player.position:<4} {player.team:<4} "
            f"vs {str(player.opponent or ''):<4} ${player.salary:>6,}  "
            f"{player.projection:>5.1f}  ({player.floor:.1f}-{player.ceiling:.1f}){flag}"
        )
    if simulation:
        data = simulation.as_dict()
        verdict = "clears" if data["beats_breakeven"] else "does NOT clear"
        print(
            f"  → {data['win_probability'] * 100:.1f}% to cash "
            f"({verdict} the {data['breakeven_win_rate'] * 100:.1f}% break-even), "
            f"ROI {data['expected_roi'] * 100:+.1f}%, median {data['median_score']}, "
            f"10th pct {data['p10_score']}"
        )
    print()


def cmd_scan(args) -> int:
    from aadfs.jobs.saturday_scan import run_scan

    summary = run_scan(
        args.salary_file,
        season=args.season,
        week=args.week,
        build_starting_lineups=args.lineups,
        fetch_remote=not args.offline,
        history_seasons=args.history,
    )
    if not summary.get("ok"):
        print(f"Scan failed: {summary.get('error')}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"Scan for {summary['season']} week {summary['week']}")
    print(f"  projections written : {summary['consensus_csv']}")
    print(f"  report              : {summary['report_json']}")
    if summary.get("lineups_csv"):
        print(f"  lineups             : {summary['lineups_csv']}")
    print(f"  players projected   : {summary['players_with_projection']}")
    print(f"  sources ok          : {', '.join(summary['sources_ok']) or 'none'}")
    if summary["sources_failed"]:
        print(f"  sources failed      : {', '.join(summary['sources_failed'])}")
    return 0


def cmd_build(args) -> int:
    from aadfs.optimizer import OptimizerConfig
    from aadfs.pipeline import build_lineups, build_projections, latest_salary_file
    from aadfs.simulate import ContestSettings

    salary_file = args.salary_file or latest_salary_file()
    if salary_file is None:
        print(
            "No salary file given and none found in data/salaries/. Download the "
            "players list from FanDuel first.",
            file=sys.stderr,
        )
        return 1

    bundle = build_projections(
        salary_file,
        season=args.season,
        week=args.week,
        projection_files=args.projections,
        history_seasons=args.history,
        fetch_remote=not args.offline,
    )

    for report in bundle.consensus.failed_sources:
        print(f"warning: source '{report.source}' unusable: {report.error}", file=sys.stderr)
    if bundle.history_error:
        print(f"warning: {bundle.history_error}", file=sys.stderr)
    if not bundle.consensus.players_with_projection:
        print("No projections available; cannot build a lineup.", file=sys.stderr)
        return 1

    config = OptimizerConfig(
        n_lineups=args.lineups,
        weight_projection=args.weight_projection,
        weight_floor=args.weight_floor,
        min_salary=args.min_salary,
        exclude_questionable=args.exclude_questionable,
        min_sources=args.min_sources,
    )
    contest = ContestSettings(payout_multiple=args.payout_multiple)
    outcome = build_lineups(
        bundle.slate, config, contest, simulate=not args.no_sim, n_sims=args.sims
    )

    if not outcome.optimization.lineups:
        print(outcome.optimization.infeasible_reason or "No lineup found.", file=sys.stderr)
        return 1
    for note in outcome.optimization.relaxations:
        print(f"note: {note}", file=sys.stderr)

    simulations = outcome.simulations or [None] * len(outcome.optimization.lineups)
    for lineup, simulation in zip(outcome.optimization.lineups, simulations):
        _print_lineup(lineup, simulation)

    if args.out:
        out = Path(args.out)
        out.write_text(lineups_to_readable_csv(outcome.optimization.lineups))
        print(f"wrote {out}")
    if args.entries_out:
        out = Path(args.entries_out)
        out.write_text(lineups_to_entry_csv(outcome.optimization.lineups))
        print(f"wrote {out}")
    return 0


def cmd_backtest(args) -> int:
    from aadfs.pipeline import build_projections
    from aadfs.sources.nflverse import (
        load_history, weekly_actuals, weekly_dst_actuals,
    )
    from aadfs.tracker import backtest_week

    bundle = build_projections(
        args.salary_file,
        season=args.season,
        week=args.week,
        history_seasons=args.history,
        fetch_remote=not args.offline,
    )
    history = load_history([args.season])
    actuals = weekly_actuals(history, args.season, args.week)
    if not actuals:
        print(
            f"No results found for {args.season} week {args.week}. "
            "They may not be published yet.",
            file=sys.stderr,
        )
        return 1

    try:
        dst_actuals = weekly_dst_actuals(args.season, args.week)
    except Exception as exc:
        print(f"warning: team-defense results unavailable: {exc}", file=sys.stderr)
        dst_actuals = {}

    result = backtest_week(
        bundle.slate, actuals, season=args.season, week=args.week,
        dst_actuals=dst_actuals,
    )
    data = result.as_dict()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    print(f"Backtest {args.season} week {args.week}")
    print(f"  players scored      : {data['scored_players']}")
    print(f"  projection MAE      : {data['projection_mae']}")
    print(f"  projection bias     : {data['projection_bias']:+}" if data["projection_bias"]
          is not None else "  projection bias     : n/a")
    print(f"  best possible score : {data['optimal_score']}")
    if data["unscored_players"]:
        print(f"  unscored (sample)   : {', '.join(data['unscored_players'][:8])}")
    print("\n  Largest projection misses:")
    for row in data["player_errors"][:12]:
        print(
            f"    {row['name']:<24} {row['position']:<4} "
            f"proj {row['projected']:>5.1f}  actual {row['actual']:>5.1f}  "
            f"{row['error']:+.1f}"
        )
    return 0


def cmd_import_results(args) -> int:
    from aadfs.store import Store
    from aadfs.tracker import parse_entry_history, summarize_entries

    store = Store(args.db)
    rows = parse_entry_history(args.file, season=args.season, week=args.week)
    added = store.save_entries(rows)
    summary = summarize_entries(store.all_entries()).as_dict()
    print(f"Imported {len(rows)} entries ({added} new).")
    print(f"  entries   : {summary['entries']}")
    print(f"  cash rate : {summary['cash_rate'] * 100:.1f}% "
          f"(need {summary['breakeven_cash_rate_at_1_8x'] * 100:.1f}% at 1.8x)")
    print(f"  net       : ${summary['net']:,.2f}  (ROI {summary['roi'] * 100:+.1f}%)")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    print(f"AADFS running at http://{args.host}:{args.port}")
    uvicorn.run("aadfs.web.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aadfs", description="Weekly FanDuel NFL cash-game lineup builder."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("--season", type=int, help="NFL season, e.g. 2025")
        sub.add_argument("--week", type=int, help="NFL week, 1-18")
        sub.add_argument("--history", type=int, nargs="*",
                         help="Seasons of results used to size player variance")
        sub.add_argument("--offline", action="store_true",
                         help="Skip remote projection sources")

    scan = subparsers.add_parser("scan", help="Run the weekly projection scan")
    scan.add_argument("--salary-file", help="FanDuel players-list CSV")
    scan.add_argument("--lineups", type=int, default=3)
    scan.add_argument("--json", action="store_true")
    add_common(scan)
    scan.set_defaults(func=cmd_scan)

    build = subparsers.add_parser("build", help="Build lineups")
    build.add_argument("--salary-file", help="FanDuel players-list CSV")
    build.add_argument("--projections", nargs="*", help="Extra projection CSVs")
    build.add_argument("--lineups", type=int, default=1)
    build.add_argument("--weight-projection", type=float, default=0.55, dest="weight_projection")
    build.add_argument("--weight-floor", type=float, default=0.45, dest="weight_floor")
    build.add_argument("--min-salary", type=int, default=58_200, dest="min_salary")
    build.add_argument("--min-sources", type=int, default=0, dest="min_sources")
    build.add_argument("--payout-multiple", type=float, default=1.8, dest="payout_multiple")
    build.add_argument("--exclude-questionable", action="store_true",
                       dest="exclude_questionable")
    build.add_argument("--sims", type=int, default=10_000)
    build.add_argument("--no-sim", action="store_true", dest="no_sim")
    build.add_argument("--out", help="Write a readable lineup CSV here")
    build.add_argument("--entries-out", dest="entries_out",
                       help="Write a FanDuel upload CSV here")
    add_common(build)
    build.set_defaults(func=cmd_build)

    backtest = subparsers.add_parser("backtest", help="Score a past week")
    backtest.add_argument("salary_file", help="That week's FanDuel players-list CSV")
    backtest.add_argument("--json", action="store_true")
    add_common(backtest)
    backtest.set_defaults(func=cmd_backtest)

    results = subparsers.add_parser("import-results", help="Import FanDuel entry history")
    results.add_argument("file", help="Entry-history CSV exported from FanDuel")
    results.add_argument("--db", default="aadfs.db")
    results.add_argument("--season", type=int)
    results.add_argument("--week", type=int)
    results.set_defaults(func=cmd_import_results)

    serve = subparsers.add_parser("serve", help="Start the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
