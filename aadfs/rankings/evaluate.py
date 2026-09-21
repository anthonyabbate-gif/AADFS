"""Scoring how good each source actually is.

Casting a wide net only helps if the blend knows which inputs to trust. Adding
a bad source to a consensus does not average out -- it drags. So every source
can be scored against what really happened, and the resulting weights fed back
into the blend.

Three measures, because they answer different questions:

* **Spearman correlation** -- did the source get the overall order right?
* **Mean absolute rank error** -- how far off was it per player?
* **Top-12 hit rate** -- of the players it called startable, how many were?
  This is the one that matters most in practice, because the top of the board
  is where decisions actually get made.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aadfs.names import normalize_name
from aadfs.rankings.aggregate import ranks_within_position
from aadfs.rankings.models import SourceRanking

#: Positions scored separately; a source can be good at one and poor at another.
SCORED_POSITIONS = ("QB", "RB", "WR", "TE")
TOP_N = 12


@dataclass
class SourceScore:
    """How one source did in one week, per position and overall."""

    source: str
    season: int
    week: int
    spearman: float | None = None
    mean_abs_rank_error: float | None = None
    top_n_hit_rate: float | None = None
    players_scored: int = 0
    by_position: dict[str, dict] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "source": self.source, "season": self.season, "week": self.week,
            "spearman": self.spearman,
            "mean_abs_rank_error": self.mean_abs_rank_error,
            "top_n_hit_rate": self.top_n_hit_rate,
            "players_scored": self.players_scored,
            "by_position": self.by_position,
        }


def _spearman(predicted: list[float], actual: list[float]) -> float | None:
    """Rank correlation, computed as Pearson on the ranks."""
    if len(predicted) < 3:
        return None

    def to_ranks(values: list[float]) -> np.ndarray:
        order = np.argsort(np.argsort(np.asarray(values, dtype=float)))
        return order.astype(float)

    a, b = to_ranks(predicted), to_ranks(actual)
    if a.std() == 0 or b.std() == 0:
        return None
    value = float(np.corrcoef(a, b)[0, 1])
    return None if math.isnan(value) else round(value, 4)


def actual_ranks(
    actuals: dict[str, float], positions: dict[str, str]
) -> dict[tuple[str, str], float]:
    """Turn a week's real scores into a rank within each position."""
    grouped: dict[str, list[tuple[str, float]]] = {}
    for key, points in actuals.items():
        position = positions.get(key)
        if position in SCORED_POSITIONS:
            grouped.setdefault(position, []).append((key, points))

    ranked: dict[tuple[str, str], float] = {}
    for position, rows in grouped.items():
        rows.sort(key=lambda r: -r[1])
        for index, (key, _) in enumerate(rows, start=1):
            ranked[(key, position)] = float(index)
    return ranked


def score_source(
    source: SourceRanking,
    truth: dict[tuple[str, str], float],
    season: int,
    week: int,
    top_n: int = TOP_N,
) -> SourceScore:
    """Compare one source's ordering against what actually happened."""
    score = SourceScore(source=source.source, season=season, week=week)
    if not source.ok or not source.entries:
        return score

    predicted = ranks_within_position(source)
    all_pred: list[float] = []
    all_true: list[float] = []

    for position in SCORED_POSITIONS:
        pairs = [
            (rank, truth[(key, pos)])
            for (key, pos), rank in predicted.items()
            if pos == position and (key, pos) in truth
        ]
        if len(pairs) < 3:
            continue
        pred = [p for p, _ in pairs]
        real = [t for _, t in pairs]
        all_pred.extend(pred)
        all_true.extend(real)

        called = {k for (k, pos), r in predicted.items() if pos == position and r <= top_n}
        were = {k for (k, pos), r in truth.items() if pos == position and r <= top_n}
        hit = len(called & were) / len(were) if were else None

        score.by_position[position] = {
            "spearman": _spearman(pred, real),
            "mean_abs_rank_error": round(
                sum(abs(p - t) for p, t in pairs) / len(pairs), 2
            ),
            "top_n_hit_rate": round(hit, 3) if hit is not None else None,
            "players": len(pairs),
        }

    if all_pred:
        score.spearman = _spearman(all_pred, all_true)
        score.mean_abs_rank_error = round(
            sum(abs(p - t) for p, t in zip(all_pred, all_true)) / len(all_pred), 2
        )
        rates = [
            v["top_n_hit_rate"] for v in score.by_position.values()
            if v["top_n_hit_rate"] is not None
        ]
        score.top_n_hit_rate = round(sum(rates) / len(rates), 3) if rates else None
        score.players_scored = len(all_pred)
    return score


def weights_from_scores(
    scores: list[SourceScore], floor: float = 0.3, ceiling: float = 2.0
) -> dict[str, float]:
    """Turn accuracy into blend weights.

    Weights are centred on 1.0 and clamped, deliberately. A source that did
    badly for three weeks might simply have had three bad weeks, and a blend
    that swings hard on a small sample is worse than one that does not move at
    all. The clamp keeps the feedback gentle.
    """
    grouped: dict[str, list[float]] = {}
    for score in scores:
        if score.spearman is not None:
            grouped.setdefault(score.source, []).append(score.spearman)
    if not grouped:
        return {}

    means = {name: sum(values) / len(values) for name, values in grouped.items()}
    overall = sum(means.values()) / len(means)
    if overall <= 0:
        return {name: 1.0 for name in means}

    weights = {}
    for name, value in means.items():
        relative = value / overall
        weights[name] = round(min(max(relative, floor), ceiling), 3)
    return weights


# --- scoring from saved boards ------------------------------------------------
# Live feeds publish the current week only, so a source cannot be graded on a
# past week after the fact. It has to be graded on what it said at the time.
# That is why every board is written to disk: the saved file is the record of
# what each source claimed, and the accuracy scoring reads it back once the
# results are in.

def positions_from_history(history) -> dict[str, str]:
    """Map normalised player name to position, from nflverse results."""
    column = (
        "player_display_name" if "player_display_name" in history.columns
        else "player_name"
    )
    return {
        normalize_name(str(getattr(row, column))): str(row.position)
        for row in history.itertuples()
    }


def board_as_source_rankings(board_json: dict) -> list[SourceRanking]:
    """Rebuild each source's ordering from a saved board file."""
    from aadfs.rankings.models import RankEntry

    by_source: dict[str, list[RankEntry]] = {}
    for player in board_json.get("players", []):
        for source, rank in (player.get("source_ranks") or {}).items():
            by_source.setdefault(source, []).append(
                RankEntry(
                    name=player["name"], position=player.get("position"),
                    team=player.get("team"), rank=float(rank),
                )
            )
    return [
        SourceRanking(source=name, entries=entries)
        for name, entries in by_source.items()
    ]


def score_saved_board(board_json: dict, actuals: dict[str, float],
                      positions: dict[str, str], top_n: int = TOP_N) -> list[SourceScore]:
    """Grade every source in a saved board against what happened."""
    truth = actual_ranks(actuals, positions)
    if not truth:
        return []
    season = int(board_json.get("season", 0))
    week = int(board_json.get("week", 0))
    return [
        score_source(source, truth, season, week, top_n)
        for source in board_as_source_rankings(board_json)
    ]
