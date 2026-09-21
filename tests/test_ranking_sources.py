"""Source adapters: the CSV one in full, the network ones for failure behaviour."""

import pytest

from aadfs.rankings.evaluate import (
    SourceScore, actual_ranks, score_source, weights_from_scores,
)
from aadfs.rankings.models import RankEntry, SourceRanking
from aadfs.rankings.registry import all_sources, usable_sources, with_csv_sources
from aadfs.rankings.sources import (
    CsvRankings, FantasyNerdsRankings, NFLComRankings, VegasTeamTotals,
)


# --- your own CSVs ------------------------------------------------------------

def test_csv_source_reads_a_rankings_export(tmp_path):
    path = tmp_path / "ecr.csv"
    path.write_text("Player,Pos,Team,Rank\nJa'Marr Chase,WR,CIN,1\nCeeDee Lamb,WR,DAL,2\n")
    result = CsvRankings(str(path)).fetch(2025, 3)
    assert result.ok and result.count == 2
    assert result.entries[0].name == "Ja'Marr Chase"
    assert result.entries[0].rank == 1


def test_csv_source_handles_fantasypros_style_position_ranks(tmp_path):
    """Exports often write the rank as 'WR12' rather than a bare number."""
    path = tmp_path / "fp.csv"
    path.write_text("Player,Pos Rank,Team\nSome Guy,WR12,CIN\nOther Guy,WR3,DAL\n")
    result = CsvRankings(str(path)).fetch(2025, 3)
    assert result.ok
    ranks = {e.name: e.rank for e in result.entries}
    assert ranks["Some Guy"] == 12
    assert ranks["Other Guy"] == 3


def test_csv_source_accepts_projections_instead_of_ranks(tmp_path):
    path = tmp_path / "proj.csv"
    path.write_text("Player,Position,FPTS\nA B,RB,18.4\nC D,RB,9.1\n")
    result = CsvRankings(str(path)).fetch(2025, 3)
    assert result.ok
    assert result.entries[0].points == 18.4


def test_csv_source_explains_a_missing_name_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Team,Rank\nCIN,1\n")
    result = CsvRankings(str(path)).fetch(2025, 3)
    assert not result.ok
    assert "player-name column" in result.error


def test_csv_source_explains_having_neither_rank_nor_projection(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Player,Team\nA B,CIN\n")
    result = CsvRankings(str(path)).fetch(2025, 3)
    assert not result.ok
    assert "rank or projection" in result.error


def test_csv_source_reports_a_missing_file_rather_than_raising(tmp_path):
    source = CsvRankings(str(tmp_path / "nope.csv"))
    ready, why = source.available()
    assert not ready and "not found" in why
    assert not source.fetch(2025, 3).ok


# --- keys and availability ----------------------------------------------------

def test_sources_needing_a_key_are_skipped_until_it_exists(monkeypatch):
    monkeypatch.delenv("FANTASYNERDS_API_KEY", raising=False)
    ready, why = FantasyNerdsRankings().available()
    assert not ready and "FANTASYNERDS_API_KEY" in why


def test_sources_become_available_once_the_key_is_set(monkeypatch):
    monkeypatch.setenv("FANTASYNERDS_API_KEY", "TEST")
    assert FantasyNerdsRankings().available()[0]


def test_usable_sources_excludes_the_ones_missing_keys(monkeypatch):
    monkeypatch.delenv("FANTASYNERDS_API_KEY", raising=False)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    names = {s.name for s in usable_sources()}
    assert "fantasy_nerds" not in names
    assert "recent_form" in names  # needs nothing


def test_every_builtin_source_declares_what_it_is():
    for source in all_sources():
        assert source.name and source.description
        assert source.kind in {"projection", "ranking", "market", "model"}


def test_csv_sources_can_be_appended_with_a_higher_weight(tmp_path):
    path = tmp_path / "mine.csv"
    path.write_text("Player,Rank\nA B,1\n")
    sources = with_csv_sources([], [str(path)], weight=1.5)
    assert len(sources) == 1 and sources[0].weight == 1.5


# --- network failure behaviour -------------------------------------------------

def test_network_sources_report_failure_instead_of_raising(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("aadfs.rankings.sources.fetch_json", explode)
    monkeypatch.setenv("ODDS_API_KEY", "x")
    for source in (NFLComRankings(), VegasTeamTotals()):
        result = source.fetch(2025, 3)
        assert not result.ok
        assert "connection reset" in result.error


def test_a_shape_change_is_reported_clearly_not_silently_empty(monkeypatch):
    monkeypatch.setattr(
        "aadfs.rankings.sources.fetch_json", lambda *a, **k: ({"unexpected": []}, False)
    )
    result = NFLComRankings().fetch(2025, 3)
    assert not result.ok
    assert "shape may have changed" in result.error


def test_vegas_implied_totals_are_derived_correctly(monkeypatch):
    payload = [{
        "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
        "bookmakers": [{"markets": [
            {"key": "totals", "outcomes": [{"name": "Over", "point": 48.5}]},
            {"key": "spreads", "outcomes": [
                {"name": "Kansas City Chiefs", "point": -2.5},
                {"name": "Buffalo Bills", "point": 2.5}]},
        ]}],
    }]
    monkeypatch.setattr("aadfs.rankings.sources.fetch_json",
                        lambda *a, **k: (payload, False))
    monkeypatch.setenv("ODDS_API_KEY", "x")
    result = VegasTeamTotals().fetch(2025, 3)
    assert result.ok
    totals = {e.name: e.points for e in result.entries}
    # Favourite by 2.5 in a 48.5 game: 24.25 + 1.25 = 25.5, dog 23.0
    assert totals["Kansas City Chiefs"] == 25.5
    assert totals["Buffalo Bills"] == 23.0
    assert sum(totals.values()) == 48.5


# --- accuracy scoring ---------------------------------------------------------

def _ranking(name, order, position="WR"):
    return SourceRanking(source=name, entries=[
        RankEntry(name=n, position=position, rank=i + 1) for i, n in enumerate(order)
    ])


def test_a_perfect_source_scores_perfectly():
    order = [f"P{i}" for i in range(1, 16)]
    truth = actual_ranks({f"p{i}": 100 - i for i in range(1, 16)},
                         {f"p{i}": "WR" for i in range(1, 16)})
    score = score_source(_ranking("perfect", order), truth, 2025, 3)
    assert score.spearman == 1.0
    assert score.mean_abs_rank_error == 0.0
    assert score.top_n_hit_rate == 1.0


def test_a_reversed_source_scores_as_badly_as_possible():
    order = [f"P{i}" for i in range(1, 16)]
    truth = actual_ranks({f"p{i}": 100 - i for i in range(1, 16)},
                         {f"p{i}": "WR" for i in range(1, 16)})
    score = score_source(_ranking("reversed", list(reversed(order))), truth, 2025, 3)
    assert score.spearman == -1.0
    assert score.mean_abs_rank_error > 0


def test_actual_ranks_orders_by_points_within_position():
    truth = actual_ranks(
        {"a": 30.0, "b": 10.0, "c": 20.0, "d": 99.0},
        {"a": "RB", "b": "RB", "c": "RB", "d": "QB"},
    )
    assert truth[("a", "RB")] == 1
    assert truth[("c", "RB")] == 2
    assert truth[("b", "RB")] == 3
    assert truth[("d", "QB")] == 1


def test_scoring_a_dead_source_returns_an_empty_score():
    dead = SourceRanking(source="dead", ok=False, error="timeout")
    score = score_source(dead, {("a", "WR"): 1.0}, 2025, 3)
    assert score.spearman is None
    assert score.players_scored == 0


def test_weights_favour_the_more_accurate_source():
    scores = [
        SourceScore(source="good", season=2025, week=1, spearman=0.9),
        SourceScore(source="bad", season=2025, week=1, spearman=0.3),
    ]
    weights = weights_from_scores(scores)
    assert weights["good"] > weights["bad"]


def test_weights_are_clamped_so_a_small_sample_cannot_swing_the_blend():
    scores = [
        SourceScore(source="great", season=2025, week=1, spearman=0.99),
        SourceScore(source="awful", season=2025, week=1, spearman=0.001),
    ]
    weights = weights_from_scores(scores)
    assert all(0.3 <= v <= 2.0 for v in weights.values())


def test_weights_are_empty_when_nothing_could_be_scored():
    assert weights_from_scores([]) == {}
    assert weights_from_scores(
        [SourceScore(source="a", season=2025, week=1, spearman=None)]
    ) == {}
