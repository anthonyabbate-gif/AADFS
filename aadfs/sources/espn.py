"""ESPN fantasy projections.

ESPN's fantasy read API exposes projected stat lines keyed by numeric stat id.
We convert those to a StatLine and apply FanDuel scoring ourselves, so ESPN's
own league scoring settings are irrelevant.
"""

from __future__ import annotations

import json

from aadfs.scoring import StatLine
from aadfs.sources.base import (
    HttpCache, ProjectionRow, SourceResult, fetch_json, safe_fetch,
)

BASE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leagues/0"
)

# ESPN numeric stat id -> StatLine field.
_STAT_IDS = {
    3: "pass_yd", 4: "pass_td", 20: "interceptions",
    24: "rush_yd", 25: "rush_td",
    42: "rec_yd", 43: "rec_td", 53: "receptions",
    72: "fumbles_lost",
    101: "dst_interceptions", 102: "dst_fumble_recoveries", 103: "dst_blocked_kicks",
    99: "dst_sacks", 95: "dst_interceptions", 96: "dst_fumble_recoveries",
    97: "dst_blocked_kicks", 98: "dst_safeties", 93: "dst_tds",
    127: "dst_points_allowed",
}
_POSITION_BY_SLOT = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

#: statSourceId 1 marks a projection rather than an actual result.
_PROJECTION_SOURCE_ID = 1


class ESPNSource:
    """Weekly projections from ESPN's public fantasy read endpoint."""

    name = "espn"

    def __init__(self, cache: HttpCache | None = None, weight: float = 1.0, limit: int = 600):
        self.cache = cache
        self.weight = weight
        self.limit = limit

    def fetch(self, season: int, week: int) -> SourceResult:
        def run():
            fantasy_filter = {
                "players": {
                    "filterStatsForExternalIds": {"value": [season]},
                    "filterSlotIds": {"value": list(_POSITION_BY_SLOT)},
                    "filterStatsForSourceIds": {"value": [0, 1]},
                    "limit": self.limit,
                    "sortAppliedStatTotal": {
                        "sortAsc": False,
                        "sortPriority": 1,
                        "value": f"11{season}{week:02d}",
                    },
                }
            }
            payload, hit = fetch_json(
                BASE_URL.format(season=season),
                cache=self.cache,
                headers={"x-fantasy-filter": json.dumps(fantasy_filter)},
                params={"view": "kona_player_info", "scoringPeriodId": week},
            )
            players = (payload or {}).get("players") or []
            rows = [
                row for row in (self._row(p, season, week) for p in players) if row is not None
            ]
            return rows, hit

        return safe_fetch(self.name, run)

    @staticmethod
    def _row(entry: dict, season: int, week: int) -> ProjectionRow | None:
        player = entry.get("player") or {}
        name = player.get("fullName")
        if not name:
            return None

        statline, found = StatLine(), False
        for block in player.get("stats") or []:
            if block.get("statSourceId") != _PROJECTION_SOURCE_ID:
                continue
            if block.get("scoringPeriodId") != week or block.get("seasonId") != season:
                continue
            for raw_id, value in (block.get("stats") or {}).items():
                field = _STAT_IDS.get(int(raw_id))
                if field and value is not None:
                    setattr(statline, field, float(value))
                    found = True
            break
        if not found:
            return None

        position = _POSITION_BY_SLOT.get(player.get("defaultPositionId"))
        if position in (None, "K"):
            return None
        return ProjectionRow(
            name=name,
            position=position,
            team=None,  # ESPN uses its own numeric pro-team ids; match on name.
            statline=statline,
            extra={"espn_id": player.get("id")},
        )
