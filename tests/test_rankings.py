"""Consensus ranking: aggregation, coverage handling, tiers and scoring."""

import json

import pytest

from aadfs.rankings.aggregate import (
    MAX_TIER_SIZE, build_consensus, ranks_within_position,
)
from aadfs.rankings.models import RankEntry, SourceRanking
from aadfs.rankings.pipeline import board_to_csv, board_to_json


def source(name, names, position="WR", ranked=True, points=None, weight=1.0, ok=True):
    entries = []
    for index, player in enumerate(names):
        entries.append(
            RankEntry(
                name=player, position=position, team="AAA",
                rank=float(index + 1) if ranked else None,
                points=(points[index] if points else (100.0 - index * 3)),
            )
        )
    return SourceRanking(source=name, entries=entries, weight=weight, ok=ok)


# --- turning a source into an order ------------------------------------------

def test_explicit_ranks_are_respected():
    s = SourceRanking(source="a", entries=[
        RankEntry(name="Low", position="WR", rank=9, points=99),
        RankEntry(name="High", position="WR", rank=1, points=1),
    ])
    ranks = ranks_within_position(s)
    assert ranks[("high", "WR")] == 1
    assert ranks[("low", "WR")] == 2


def test_projection_only_sources_are_ranked_by_points():
    s = SourceRanking(source="a", entries=[
        RankEntry(name="Mid", position="RB", points=12.0),
        RankEntry(name="Best", position="RB", points=20.0),
        RankEntry(name="Worst", position="RB", points=4.0),
    ])
    ranks = ranks_within_position(s)
    assert ranks[("best", "RB")] == 1
    assert ranks[("mid", "RB")] == 2
    assert ranks[("worst", "RB")] == 3


def test_positions_are_ranked_independently():
    s = SourceRanking(source="a", entries=[
        RankEntry(name="QB One", position="QB", points=25.0),
        RankEntry(name="WR One", position="WR", points=18.0),
    ])
    ranks = ranks_within_position(s)
    assert ranks[("qb one", "QB")] == 1
    assert ranks[("wr one", "WR")] == 1


# --- consensus ----------------------------------------------------------------

def test_sources_that_agree_produce_that_order():
    order = ["A", "B", "C", "D"]
    board = build_consensus([source("s1", order), source("s2", order)], 2025, 3)
    assert [p.name for p in board.by_position("WR")] == order
    assert all(p.rank_stdev == 0 for p in board.by_position("WR"))


def test_disagreement_is_measured_not_hidden():
    board = build_consensus(
        [source("s1", ["A", "B", "C"]), source("s2", ["C", "B", "A"])], 2025, 3
    )
    players = {p.name: p for p in board.by_position("WR")}
    # B is 2nd in both; A and C swing from 1st to 3rd.
    assert players["B"].rank_stdev == 0
    assert players["A"].rank_stdev > 0
    assert players["A"].rank_range == 2.0
    assert players["A"].disagreement != "tight" or players["A"].rank_stdev > 0


def test_weights_shift_the_consensus():
    heavy = build_consensus(
        [source("s1", ["A", "B"]), source("s2", ["B", "A"])],
        2025, 3, weights={"s1": 5.0, "s2": 1.0},
    )
    assert [p.name for p in heavy.by_position("WR")] == ["A", "B"]

    flipped = build_consensus(
        [source("s1", ["A", "B"]), source("s2", ["B", "A"])],
        2025, 3, weights={"s1": 1.0, "s2": 5.0},
    )
    assert [p.name for p in flipped.by_position("WR")] == ["B", "A"]


def test_a_player_only_one_source_rates_does_not_float_to_the_top():
    """The whole point of the coverage penalty."""
    broad = ["A", "B", "C", "D", "E", "F", "G", "H"]
    board = build_consensus(
        [
            source("s1", broad),
            source("s2", broad),
            source("s3", ["Nobody"]),  # one source, one enthusiastic pick
        ],
        2025, 3,
    )
    players = {p.name: p for p in board.by_position("WR")}
    assert players["Nobody"].overall_rank > players["A"].overall_rank
    assert players["Nobody"].source_count == 1
    assert any("1 source" in note for note in players["Nobody"].notes)


def test_source_count_is_the_number_that_actually_rated_the_player():
    board = build_consensus(
        [source("s1", ["A", "B"]), source("s2", ["A"]), source("s3", ["A", "B"])],
        2025, 3,
    )
    players = {p.name: p for p in board.by_position("WR")}
    assert players["A"].source_count == 3
    assert players["B"].source_count == 2


def test_min_sources_filters_thin_coverage():
    board = build_consensus(
        [source("s1", ["A", "B"]), source("s2", ["A"])], 2025, 3, min_sources=2
    )
    assert [p.name for p in board.by_position("WR")] == ["A"]


def test_outliers_are_flagged():
    common = [f"P{i}" for i in range(1, 21)]
    odd = ["P20"] + [f"P{i}" for i in range(1, 20)]  # one source loves the last guy
    board = build_consensus(
        [source("s1", common), source("s2", common), source("s3", odd)], 2025, 3
    )
    p20 = next(p for p in board.by_position("WR") if p.name == "P20")
    assert p20.outliers
    assert "s3" in p20.outliers[0]


def test_failed_sources_are_carried_through_not_dropped():
    dead = SourceRanking(source="dead", ok=False, error="timeout")
    board = build_consensus([source("s1", ["A", "B"]), dead], 2025, 3)
    assert [s.source for s in board.failed_sources] == ["dead"]
    assert [s.source for s in board.ok_sources] == ["s1"]
    assert len(board.by_position("WR")) == 2


def test_empty_input_produces_an_empty_board_not_a_crash():
    board = build_consensus([], 2025, 3)
    assert board.players == []
    assert board.positions == []


# --- tiers --------------------------------------------------------------------

def test_tiers_start_at_one_and_never_skip():
    order = [f"P{i:02d}" for i in range(1, 30)]
    board = build_consensus([source("s1", order), source("s2", order)], 2025, 3)
    tiers = [p.tier for p in board.by_position("WR")]
    assert tiers[0] == 1
    assert tiers == sorted(tiers)
    assert set(tiers) == set(range(1, max(tiers) + 1))


def test_no_tier_exceeds_the_size_cap():
    """Guards the bug where an uncertain player kept pushing the boundary out."""
    import random

    rng = random.Random(5)
    truth = [f"P{i:02d}" for i in range(1, 40)]
    sources = []
    for i in range(6):
        noisy = sorted((j + rng.gauss(0, 0.6 + j * 0.25), n) for j, n in enumerate(truth))
        sources.append(source(f"s{i}", [n for _, n in noisy]))
    board = build_consensus(sources, 2025, 3)
    counts: dict[int, int] = {}
    for player in board.by_position("WR"):
        counts[player.tier] = counts.get(player.tier, 0) + 1
    assert max(counts.values()) <= MAX_TIER_SIZE


def test_agreement_makes_tighter_tiers_than_disagreement():
    order = [f"P{i:02d}" for i in range(1, 25)]
    agreed = build_consensus([source(f"s{i}", order) for i in range(4)], 2025, 3)

    import random

    rng = random.Random(2)
    messy = []
    for i in range(4):
        shuffled = order[:]
        rng.shuffle(shuffled)
        messy.append(source(f"s{i}", shuffled))
    disagreed = build_consensus(messy, 2025, 3)

    agreed_tiers = max(p.tier for p in agreed.by_position("WR"))
    messy_tiers = max(p.tier for p in disagreed.by_position("WR"))
    # When sources disagree, uncertainty bands overlap and tiers merge.
    assert messy_tiers <= agreed_tiers


# --- output -------------------------------------------------------------------

def test_csv_export_has_a_row_per_player_and_a_column_per_source():
    board = build_consensus([source("s1", ["A", "B"]), source("s2", ["B", "A"])], 2025, 3)
    text = board_to_csv(board)
    lines = text.strip().splitlines()
    assert len(lines) == 3  # header + two players
    assert "rank_s1" in lines[0] and "rank_s2" in lines[0]


def test_json_export_round_trips():
    board = build_consensus([source("s1", ["A", "B"])], 2025, 3)
    data = json.loads(board_to_json(board))
    assert data["season"] == 2025 and data["week"] == 3
    assert {p["name"] for p in data["players"]} == {"A", "B"}
    assert data["players"][0]["source_ranks"] == {"s1": 1.0}
