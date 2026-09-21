"""Building and publishing the week's consensus board."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from aadfs.rankings.aggregate import build_consensus
from aadfs.rankings.models import ConsensusBoard
from aadfs.rankings.registry import usable_sources, with_csv_sources
from aadfs.sources.base import HttpCache

from aadfs.config import cache_dir as _cache_dir, rankings_dir as _rankings_dir

DEFAULT_OUTPUT_DIR = _rankings_dir()
WEIGHTS_FILE = DEFAULT_OUTPUT_DIR / "weights.json"
DEFAULT_CACHE_DIR = _cache_dir()


def load_weights(path: Path | str = WEIGHTS_FILE) -> dict[str, float]:
    """Blend weights learned from past accuracy, if any have been saved."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    weights = data.get("weights", data)
    return {k: float(v) for k, v in weights.items() if isinstance(v, (int, float))}


def save_weights(weights: dict[str, float], path: Path | str = WEIGHTS_FILE,
                 meta: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"weights": weights, **(meta or {})}, indent=2))
    return path


def gather(
    season: int,
    week: int,
    *,
    csv_paths: list[str] | None = None,
    weights: dict[str, float] | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    only: list[str] | None = None,
) -> list:
    """Fetch every usable source for the week."""
    cache = HttpCache(cache_dir or DEFAULT_CACHE_DIR,
                      ttl_seconds=0 if refresh else 6 * 3600)
    sources = with_csv_sources(usable_sources(cache, weights), csv_paths)
    if only:
        wanted = {name.lower() for name in only}
        sources = [s for s in sources if s.name.lower() in wanted]
    return [s.fetch(season, week) for s in sources]


def build_board(
    season: int,
    week: int,
    *,
    csv_paths: list[str] | None = None,
    weights: dict[str, float] | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    only: list[str] | None = None,
    min_sources: int = 1,
) -> ConsensusBoard:
    """Fetch everything and aggregate it into the week's board."""
    weights = weights if weights is not None else load_weights()
    results = gather(
        season, week, csv_paths=csv_paths, weights=weights,
        cache_dir=cache_dir, refresh=refresh, only=only,
    )
    return build_consensus(results, season, week, weights=weights,
                           min_sources=min_sources)


def board_to_csv(board: ConsensusBoard) -> str:
    """The board as a flat CSV, one row per player."""
    source_names = sorted({name for p in board.players for name in p.source_ranks})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["position", "rank", "tier", "player", "team", "opponent",
         "consensus_rank", "rank_stdev", "best", "worst", "range",
         "agreement", "sources", "mean_points", "flags"]
        + [f"rank_{name}" for name in source_names]
    )
    for player in board.players:
        writer.writerow(
            [
                player.position, player.overall_rank, player.tier, player.name,
                player.team or "", player.opponent or "",
                player.consensus_rank, player.rank_stdev,
                player.best_rank, player.worst_rank, player.rank_range,
                player.disagreement, player.source_count,
                player.mean_points if player.mean_points is not None else "",
                "; ".join(player.outliers + player.notes),
            ]
            + [player.source_ranks.get(name, "") for name in source_names]
        )
    return out.getvalue()


def board_to_json(board: ConsensusBoard) -> str:
    return json.dumps(
        {
            "season": board.season,
            "week": board.week,
            "generated_at": board.generated_at,
            "sources": [
                {"source": s.source, "kind": s.kind, "ok": s.ok, "entries": s.count,
                 "weight": s.weight, "error": s.error, "from_cache": s.from_cache}
                for s in board.sources
            ],
            "players": [
                {
                    "position": p.position, "rank": p.overall_rank, "tier": p.tier,
                    "name": p.name, "team": p.team, "opponent": p.opponent,
                    "consensus_rank": p.consensus_rank, "rank_stdev": p.rank_stdev,
                    "best": p.best_rank, "worst": p.worst_rank,
                    "agreement": p.disagreement, "sources": p.source_count,
                    "source_ranks": p.source_ranks,
                    "mean_points": p.mean_points,
                    "outliers": p.outliers, "notes": p.notes,
                }
                for p in board.players
            ],
        },
        indent=2,
    )


def write_board(board: ConsensusBoard,
                output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> dict[str, str]:
    """Write the board to disk as both CSV and JSON."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{board.season}_wk{board.week:02d}"
    csv_path = output_dir / f"consensus_{stem}.csv"
    json_path = output_dir / f"consensus_{stem}.json"
    csv_path.write_text(board_to_csv(board))
    json_path.write_text(board_to_json(board))
    return {"csv": str(csv_path), "json": str(json_path)}
