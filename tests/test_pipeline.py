import datetime as dt

import pytest

from aadfs.pipeline import (
    build_lineups, build_projections, guess_season_and_week, latest_salary_file,
)

from .conftest import SALARY_CSV


@pytest.mark.parametrize(
    "today,expected",
    [
        ("2025-09-03", (2025, 1)),   # before week 1 kicks off
        ("2025-09-10", (2025, 2)),   # midweek: you are building the next slate
        ("2025-11-26", (2025, 13)),
        ("2026-01-05", (2025, 18)),  # January belongs to the previous season
        ("2025-07-01", (2024, 18)),  # offseason clamps to the last week
    ],
)
def test_season_and_week_inference(today, expected):
    assert guess_season_and_week(dt.date.fromisoformat(today)) == expected


def test_build_projections_offline_uses_fanduel_averages():
    bundle = build_projections(
        SALARY_CSV, season=2025, week=3, fetch_remote=False, history_seasons=None
    )
    assert bundle.consensus.players_with_projection == 192
    assert bundle.source_results == []
    assert bundle.history_error is None


def test_build_lineups_produces_simulations():
    bundle = build_projections(SALARY_CSV, season=2025, week=3, fetch_remote=False)
    outcome = build_lineups(bundle.slate, n_sims=2000)
    assert len(outcome.optimization.lineups) == 1
    assert len(outcome.simulations) == 1
    assert 0.0 <= outcome.simulations[0].win_probability <= 1.0


def test_build_lineups_shares_one_field_across_lineups():
    from aadfs.optimizer import OptimizerConfig

    bundle = build_projections(SALARY_CSV, season=2025, week=3, fetch_remote=False)
    outcome = build_lineups(bundle.slate, OptimizerConfig(n_lineups=3), n_sims=2000)
    # A shared field means every lineup was judged against the same cash line.
    assert len({s.field_size for s in outcome.simulations}) == 1


def test_missing_history_degrades_to_defaults_without_raising():
    bundle = build_projections(
        SALARY_CSV, season=2025, week=3, fetch_remote=False,
        history_seasons=[1901],  # a season that certainly does not exist
    )
    assert bundle.history_error is not None
    assert bundle.consensus.players_with_projection == 192


def test_latest_salary_file_handles_an_empty_directory(tmp_path):
    assert latest_salary_file(tmp_path) is None
    (tmp_path / "a.csv").write_text("x")
    assert latest_salary_file(tmp_path).name == "a.csv"
