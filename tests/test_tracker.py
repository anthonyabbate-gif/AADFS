import io

import pytest

from aadfs.optimizer import OptimizerConfig, optimize
from aadfs.store import Store
from aadfs.tracker import (
    backtest_week, parse_entry_history, score_lineup, summarize_entries,
)

ENTRY_CSV = (
    "Entry Id,Contest Id,Contest Name,Date,Entry Fee,Winnings ($),Score,Position,Entries\n"
    "E1,C1,NFL $5 Double Up,2025-09-14,$5.00,$9.00,142.62,120,1000\n"
    "E2,C1,NFL $5 Double Up,2025-09-14,$5.00,$0.00,118.20,640,1000\n"
    "E3,C2,NFL $10 50/50,2025-09-21,$10.00,$18.00,151.04,88,500\n"
)


def test_entry_history_parses_currency_and_places():
    rows = parse_entry_history(io.StringIO(ENTRY_CSV), season=2025, week=2)
    assert len(rows) == 3
    assert rows[0]["entry_fee"] == 5.0
    assert rows[0]["winnings"] == 9.0
    assert rows[0]["position"] == 120
    assert rows[0]["field_size"] == 1000
    assert rows[0]["season"] == 2025


def test_entry_history_rejects_an_unrecognisable_file():
    with pytest.raises(ValueError, match="score or winnings"):
        parse_entry_history(io.StringIO("Foo,Bar\n1,2\n"))


def test_summary_computes_roi_and_cash_rate():
    rows = parse_entry_history(io.StringIO(ENTRY_CSV))
    summary = summarize_entries(rows).as_dict()
    assert summary["entries"] == 3
    assert summary["total_fees"] == 20.0
    assert summary["total_winnings"] == 27.0
    assert summary["net"] == 7.0
    assert summary["roi"] == 0.35
    assert round(summary["cash_rate"], 4) == 0.6667


def test_summary_of_nothing_is_zero_not_an_error():
    summary = summarize_entries([]).as_dict()
    assert summary["entries"] == 0
    assert summary["roi"] == 0.0


def test_store_round_trips_entries_and_deduplicates(tmp_path):
    store = Store(tmp_path / "t.db")
    rows = parse_entry_history(io.StringIO(ENTRY_CSV), season=2025, week=2)
    assert store.save_entries(rows) == 3
    assert store.save_entries(rows) == 0  # same entries, no duplicates
    assert len(store.all_entries()) == 3


def test_store_saves_and_reads_back_a_lineup(tmp_path, projected_slate):
    store = Store(tmp_path / "t.db")
    lineup = optimize(projected_slate, OptimizerConfig()).lineups[0]
    slate_id = store.save_slate("wk3", 2025, 3, len(projected_slate.players))
    lineup_id = store.save_lineup(slate_id, lineup, {"preset": "cash"},
                                  {"win_probability": 0.61, "expected_roi": 0.1})
    saved = store.recent_lineups()[0]
    assert saved["id"] == lineup_id
    assert len(saved["players"]) == 9
    assert saved["config"] == {"preset": "cash"}
    assert saved["win_probability"] == 0.61


def test_backtest_measures_projection_error_and_the_best_possible_lineup(projected_slate):
    from aadfs.names import normalize_name

    # Pretend everybody scored exactly 1.5x their projection.
    actuals = {
        normalize_name(p.name): round(p.projection * 1.5, 2)
        for p in projected_slate.players if p.position != "DST"
    }
    dst = {
        p.team: round(p.projection * 1.5, 2)
        for p in projected_slate.players if p.position == "DST"
    }
    result = backtest_week(projected_slate, actuals, season=2025, week=3, dst_actuals=dst)
    data = result.as_dict()

    assert data["scored_players"] > 0
    assert data["projection_bias"] > 0  # everybody beat their projection
    assert data["optimal_score"] is not None
    assert not data["unscored_players"]


def test_backtest_reports_defenses_it_could_not_score(projected_slate):
    from aadfs.names import normalize_name

    actuals = {
        normalize_name(p.name): 12.0
        for p in projected_slate.players if p.position != "DST"
    }
    # No D/ST results supplied: they must be reported, not silently zeroed.
    data = backtest_week(projected_slate, actuals, season=2025, week=3).as_dict()
    assert any("DST" in name for name in data["unscored_players"])


def test_lineup_scoring_needs_every_player(projected_slate):
    lineup = optimize(projected_slate, OptimizerConfig()).lineups[0]
    assert score_lineup(lineup) is None  # no actuals attached yet
    for player in lineup.players:
        player.actual = 10.0
    assert score_lineup(lineup) == 90.0
