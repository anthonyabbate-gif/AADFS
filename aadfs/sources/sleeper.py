"""Sleeper projections.

Sleeper serves weekly projections as raw stat keys, which is ideal: we score
them under FanDuel's rules rather than inheriting someone else's scoring.
No API key and no authentication required.
"""

from __future__ import annotations

from aadfs.scoring import StatLine
from aadfs.sources.base import (
    HttpCache, ProjectionRow, SourceResult, fetch_json, safe_fetch,
)

PROJECTIONS_URL = "https://api.sleeper.com/projections/nfl/{season}/{week}"
POSITIONS = ("QB", "RB", "WR", "TE", "DEF")

# Sleeper stat key -> StatLine field.
_STAT_MAP = {
    "pass_yd": "pass_yd", "pass_td": "pass_td", "pass_int": "interceptions",
    "pass_2pt": "two_point_conversions",
    "rush_yd": "rush_yd", "rush_td": "rush_td", "rush_2pt": "two_point_conversions",
    "rec": "receptions", "rec_yd": "rec_yd", "rec_td": "rec_td",
    "rec_2pt": "two_point_conversions",
    "fum_lost": "fumbles_lost",
    "st_td": "return_td", "def_st_td": "return_td",
    "sack": "dst_sacks", "int": "dst_interceptions", "fum_rec": "dst_fumble_recoveries",
    "def_td": "dst_tds", "safe": "dst_safeties", "blk_kick": "dst_blocked_kicks",
    "pts_allow": "dst_points_allowed",
}
_ADDITIVE = {"two_point_conversions"}


def _to_statline(stats: dict) -> StatLine:
    line = StatLine()
    for key, value in (stats or {}).items():
        field = _STAT_MAP.get(key)
        if not field or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if field in _ADDITIVE:
            setattr(line, field, getattr(line, field) + number)
        else:
            setattr(line, field, number)
    return line


class SleeperSource:
    """Weekly projections from Sleeper's public projections endpoint."""

    name = "sleeper"

    def __init__(self, cache: HttpCache | None = None, weight: float = 1.0):
        self.cache = cache
        self.weight = weight

    def fetch(self, season: int, week: int) -> SourceResult:
        def run():
            rows: list[ProjectionRow] = []
            cached_all = True
            for position in POSITIONS:
                payload, hit = fetch_json(
                    PROJECTIONS_URL.format(season=season, week=week),
                    cache=self.cache,
                    params={
                        "season_type": "regular",
                        "position": position,
                        "order_by": "ppr",
                    },
                )
                cached_all = cached_all and hit
                for entry in payload or []:
                    rows.append(self._row(entry, position))
            return rows, cached_all

        return safe_fetch(self.name, run)

    @staticmethod
    def _row(entry: dict, requested_position: str) -> ProjectionRow:
        player = entry.get("player") or {}
        stats = entry.get("stats") or {}
        name = (
            player.get("full_name")
            or " ".join(filter(None, [player.get("first_name"), player.get("last_name")]))
            or entry.get("player_id")
            or ""
        )
        position = player.get("position") or entry.get("position") or requested_position
        team = entry.get("team") or player.get("team")
        if position in {"DEF", "DST"}:
            name = team or name
        return ProjectionRow(
            name=name,
            position=position,
            team=team,
            opponent=entry.get("opponent"),
            statline=_to_statline(stats),
            points=stats.get("pts_half_ppr"),
            extra={"sleeper_id": entry.get("player_id")},
        )
