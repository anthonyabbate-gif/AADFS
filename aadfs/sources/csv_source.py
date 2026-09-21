"""Read projections out of any CSV you already have.

This is the source you will use most: whatever you subscribe to, export it to
CSV and drop it in. Column names are guessed from a broad alias table, and can
be overridden explicitly when a file is unusual.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from aadfs.sources.base import ProjectionRow, SourceResult

_ALIASES = {
    "name": ("player", "playername", "name", "fullname", "player_name", "nickname"),
    "position": ("pos", "position", "playerposition"),
    "team": ("team", "tm", "teamabbrev", "club"),
    "opponent": ("opp", "opponent", "oppteam"),
    "points": (
        "fpts", "proj", "projection", "projectedpoints", "points", "fantasypoints",
        "projpts", "fd", "fdpoints", "fanduel", "fanduelprojection", "median",
    ),
    "floor": ("floor", "flr", "lowprojection", "p25"),
    "ceiling": ("ceiling", "ceil", "highprojection", "p75", "upside"),
    "stdev": ("stdev", "sd", "std", "stddev", "sigma", "variance"),
    "ownership": ("own", "ownership", "proj_own", "projectedownership", "pown"),
}


def _norm(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _resolve_columns(fieldnames: list[str], overrides: dict[str, str] | None) -> dict[str, str]:
    normalized = {_norm(f): f for f in fieldnames}
    resolved: dict[str, str] = {}
    for field, options in _ALIASES.items():
        for option in options:
            if option in normalized:
                resolved[field] = normalized[option]
                break
    for field, column in (overrides or {}).items():
        if column in fieldnames:
            resolved[field] = column
    return resolved


def _number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_projection_csv(
    source: str | Path | io.StringIO,
    *,
    source_name: str = "csv",
    column_overrides: dict[str, str] | None = None,
) -> SourceResult:
    """Parse a projections CSV into a SourceResult."""
    if isinstance(source, (str, Path)) and Path(source).exists():
        text = Path(source).read_text(encoding="utf-8-sig")
    else:
        text = source.getvalue() if isinstance(source, io.StringIO) else str(source)

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return SourceResult(source=source_name, ok=False, error="File has no header row.")

    columns = _resolve_columns(list(reader.fieldnames), column_overrides)
    if "name" not in columns:
        return SourceResult(
            source=source_name, ok=False,
            error=f"No player-name column found. Columns seen: {', '.join(reader.fieldnames)}",
        )
    if "points" not in columns:
        return SourceResult(
            source=source_name, ok=False,
            error=f"No projection column found. Columns seen: {', '.join(reader.fieldnames)}",
        )

    rows: list[ProjectionRow] = []
    for record in reader:
        name = (record.get(columns["name"]) or "").strip()
        if not name:
            continue
        points = _number(record.get(columns["points"]))
        if points is None:
            continue
        extra = {}
        for field in ("floor", "ceiling", "stdev", "ownership"):
            if field in columns:
                value = _number(record.get(columns[field]))
                if value is not None:
                    extra[field] = value
        rows.append(
            ProjectionRow(
                name=name,
                position=(record.get(columns["position"]) or None) if "position" in columns else None,
                team=(record.get(columns["team"]) or None) if "team" in columns else None,
                opponent=(record.get(columns["opponent"]) or None) if "opponent" in columns else None,
                points=points,
                extra=extra,
            )
        )

    if not rows:
        return SourceResult(source=source_name, ok=False, error="No usable projection rows found.")
    return SourceResult(source=source_name, rows=rows, ok=True)
