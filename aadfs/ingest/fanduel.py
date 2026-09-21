"""Read and write the CSV files FanDuel actually gives you.

FanDuel has no public API for salaries or contest data. The supported way to get
a slate is the "Download Players List" button on the contest entry screen, and
the supported way to enter lineups in bulk is the entries CSV upload. This
module speaks both of those formats.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from aadfs.models import Lineup, Player, Slate
from aadfs.names import normalize_position, normalize_team
from aadfs.scoring import RosterRules

# FanDuel's salary export header, as of the current NFL format. Parsing is done
# by normalised header name so that column order and casing can drift.
_COLUMN_ALIASES = {
    "id": "player_id",
    "playerid": "player_id",
    "position": "position",
    "firstname": "first_name",
    "lastname": "last_name",
    "nickname": "nickname",
    "name": "nickname",
    "fppg": "fppg",
    "played": "played",
    "salary": "salary",
    "game": "game",
    "team": "team",
    "opponent": "opponent",
    "injuryindicator": "injury_status",
    "injurydetails": "injury_detail",
    "tier": "tier",
    "rosterposition": "roster_position",
}


def _norm_header(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _to_int(value: str | None) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return 0
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _split_game(game: str | None) -> tuple[str | None, str | None]:
    """FanDuel writes the matchup as AWAY@HOME."""
    if not game:
        return None, None
    text = str(game).strip()
    for sep in ("@", "vs", "VS", "-"):
        if sep in text:
            away, _, home = text.partition(sep)
            return normalize_team(away.strip()), normalize_team(home.strip())
    return None, None


def parse_salary_csv(source: str | Path | io.StringIO, *, rules: RosterRules | None = None,
                     slate_name: str | None = None) -> Slate:
    """Parse a FanDuel "Download Players List" CSV into a Slate."""
    if isinstance(source, (str, Path)) and Path(source).exists():
        text = Path(source).read_text(encoding="utf-8-sig")
        origin = str(source)
        name = slate_name or Path(source).stem
    else:
        text = source.getvalue() if isinstance(source, io.StringIO) else str(source)
        origin = None
        name = slate_name or "slate"

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Salary file has no header row.")

    mapping = {}
    for raw in reader.fieldnames:
        key = _COLUMN_ALIASES.get(_norm_header(raw or ""))
        if key and key not in mapping:
            mapping[key] = raw

    missing = {"position", "salary"} - set(mapping)
    if missing:
        raise ValueError(
            "Salary file is missing required column(s): "
            + ", ".join(sorted(missing))
            + f". Found: {', '.join(reader.fieldnames)}"
        )

    players: list[Player] = []
    seen: set[str] = set()
    for row_no, row in enumerate(reader, start=2):
        def cell(key: str) -> str | None:
            col = mapping.get(key)
            return (row.get(col) or "").strip() if col else None

        position = normalize_position(cell("position"))
        if not position:
            continue

        nickname = cell("nickname")
        first, last = cell("first_name"), cell("last_name")
        display = nickname or " ".join(filter(None, [first, last])) or "unknown"

        team = normalize_team(cell("team"))
        opponent = normalize_team(cell("opponent"))
        away, home = _split_game(cell("game"))

        pid = cell("player_id") or f"{display}-{team}-{position}"
        if pid in seen:
            pid = f"{pid}-{row_no}"
        seen.add(pid)

        players.append(
            Player(
                player_id=pid,
                name=display,
                position=position,
                team=team or "",
                opponent=opponent,
                salary=_to_int(cell("salary")),
                game_id=("@".join([away, home]) if away and home else None),
                home_team=home,
                away_team=away,
                fd_projection=_to_float(cell("fppg")),
                injury_status=cell("injury_status") or None,
                injury_detail=cell("injury_detail") or None,
                roster_position=cell("roster_position") or None,
            )
        )

    if not players:
        raise ValueError("Salary file parsed but contained no player rows.")

    return Slate(players=players, name=name, rules=rules or RosterRules(), source_file=origin)


# FanDuel's bulk entry upload expects these roster slot headers, in this order.
ENTRY_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF"]


def _slot_assignments(lineup: Lineup) -> list[Player]:
    """Place a lineup's players into FanDuel's fixed slot order."""
    ordered = lineup.ordered()
    if len(ordered) != len(ENTRY_SLOTS):
        raise ValueError(
            f"Lineup does not fit FanDuel's roster slots "
            f"(got {len(ordered)} of {len(ENTRY_SLOTS)} slots filled)."
        )
    return ordered


def lineups_to_entry_csv(lineups: list[Lineup], entry_ids: list[str] | None = None) -> str:
    """Render lineups in FanDuel's bulk-upload shape.

    FanDuel's upload wants your existing entry IDs in the first column; export
    your entries file from the contest, then paste these player IDs across.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["entry_id"] + ENTRY_SLOTS)
    for i, lineup in enumerate(lineups):
        entry_id = entry_ids[i] if entry_ids and i < len(entry_ids) else ""
        writer.writerow([entry_id] + [p.player_id for p in _slot_assignments(lineup)])
    return out.getvalue()


def lineups_to_readable_csv(lineups: list[Lineup]) -> str:
    """A human-readable export for reviewing lineups away from the app."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["lineup", "slot", "player", "position", "team", "opponent",
         "salary", "projection", "floor", "ceiling", "value"]
    )
    for i, lineup in enumerate(lineups, start=1):
        for slot, p in zip(ENTRY_SLOTS, _slot_assignments(lineup)):
            writer.writerow(
                [i, slot, p.name, p.position, p.team, p.opponent or "",
                 p.salary, round(p.projection, 2), round(p.floor, 2),
                 round(p.ceiling, 2), round(p.value, 2)]
            )
        writer.writerow(
            [i, "TOTAL", "", "", "", "", lineup.salary,
             lineup.projection, lineup.floor, lineup.ceiling, ""]
        )
    return out.getvalue()
