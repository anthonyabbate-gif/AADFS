"""Local SQLite storage for slates, lineups and contest results.

Everything the app learns about your play lives here, in one file you own. The
schema is deliberately small: a lineup you built, an entry you actually made,
and the result once the week is scored.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

def _default_db() -> Path:
    from aadfs.config import database_path

    return database_path()


DEFAULT_DB = _default_db()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    season INTEGER,
    week INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    player_count INTEGER,
    UNIQUE(name, season, week)
);

CREATE TABLE IF NOT EXISTS lineups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slate_id INTEGER REFERENCES slates(id) ON DELETE CASCADE,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    salary INTEGER,
    projection REAL,
    floor REAL,
    ceiling REAL,
    win_probability REAL,
    expected_roi REAL,
    config_json TEXT,
    players_json TEXT NOT NULL,
    actual_score REAL
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT,
    contest_id TEXT,
    contest_name TEXT,
    played_on TEXT,
    season INTEGER,
    week INTEGER,
    entry_fee REAL,
    winnings REAL,
    score REAL,
    position INTEGER,
    field_size INTEGER,
    lineup_id INTEGER REFERENCES lineups(id) ON DELETE SET NULL,
    UNIQUE(entry_id, contest_id)
);

CREATE TABLE IF NOT EXISTS player_weeks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    name_key TEXT NOT NULL,
    position TEXT,
    team TEXT,
    projected REAL,
    actual REAL,
    salary INTEGER,
    UNIQUE(season, week, name_key)
);

CREATE INDEX IF NOT EXISTS idx_entries_week ON entries(season, week);
CREATE INDEX IF NOT EXISTS idx_player_weeks ON player_weeks(season, week);
"""


@dataclass
class Store:
    """A thin wrapper over the SQLite file."""

    path: Path | str = DEFAULT_DB

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- slates -------------------------------------------------------------

    def save_slate(self, name: str, season: int | None, week: int | None,
                   player_count: int) -> int:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO slates(name, season, week, player_count) "
                "VALUES (?,?,?,?)",
                (name, season, week, player_count),
            )
            row = conn.execute(
                "SELECT id FROM slates WHERE name=? AND season IS ? AND week IS ?",
                (name, season, week),
            ).fetchone()
            return int(row["id"])

    # --- lineups ------------------------------------------------------------

    def save_lineup(self, slate_id: int, lineup, config: dict | None = None,
                    simulation: dict | None = None) -> int:
        players = [
            {
                "player_id": p.player_id, "name": p.name, "position": p.position,
                "team": p.team, "opponent": p.opponent, "salary": p.salary,
                "projection": p.projection, "floor": p.floor, "ceiling": p.ceiling,
            }
            for p in lineup.ordered()
        ]
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO lineups(slate_id, label, salary, projection, floor, ceiling, "
                "win_probability, expected_roi, config_json, players_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    slate_id, lineup.label, lineup.salary, lineup.projection,
                    lineup.floor, lineup.ceiling,
                    (simulation or {}).get("win_probability"),
                    (simulation or {}).get("expected_roi"),
                    json.dumps(config or {}), json.dumps(players),
                ),
            )
            return int(cursor.lastrowid)

    def lineups_for_week(self, season: int, week: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT l.*, s.season, s.week, s.name AS slate_name FROM lineups l "
                "JOIN slates s ON s.id = l.slate_id WHERE s.season=? AND s.week=? "
                "ORDER BY l.created_at DESC",
                (season, week),
            ).fetchall()
        return [self._lineup_row(r) for r in rows]

    def recent_lineups(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT l.*, s.season, s.week, s.name AS slate_name FROM lineups l "
                "LEFT JOIN slates s ON s.id = l.slate_id "
                "ORDER BY l.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._lineup_row(r) for r in rows]

    @staticmethod
    def _lineup_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["players"] = json.loads(data.pop("players_json") or "[]")
        data["config"] = json.loads(data.pop("config_json") or "{}")
        return data

    def set_lineup_actual(self, lineup_id: int, actual: float) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE lineups SET actual_score=? WHERE id=?", (actual, lineup_id))

    # --- entries ------------------------------------------------------------

    def save_entries(self, entries: list[dict]) -> int:
        if not entries:
            return 0
        columns = ["entry_id", "contest_id", "contest_name", "played_on", "season", "week",
                   "entry_fee", "winnings", "score", "position", "field_size"]
        placeholders = ",".join("?" * len(columns))
        with self.connect() as conn:
            before = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
            conn.executemany(
                f"INSERT OR REPLACE INTO entries({','.join(columns)}) VALUES ({placeholders})",
                [tuple(e.get(c) for c in columns) for e in entries],
            )
            after = conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"]
        return after - before

    def all_entries(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM entries ORDER BY played_on DESC, contest_name"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- player weeks -------------------------------------------------------

    def save_player_weeks(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        columns = ["season", "week", "name_key", "position", "team",
                   "projected", "actual", "salary"]
        placeholders = ",".join("?" * len(columns))
        with self.connect() as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO player_weeks({','.join(columns)}) "
                f"VALUES ({placeholders})",
                [tuple(r.get(c) for c in columns) for r in rows],
            )
        return len(rows)

    def player_weeks(self, season: int | None = None) -> list[dict]:
        query = "SELECT * FROM player_weeks"
        params: tuple = ()
        if season is not None:
            query += " WHERE season=?"
            params = (season,)
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
