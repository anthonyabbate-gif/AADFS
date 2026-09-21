"""Every input that can contribute an opinion about the week.

The aim is breadth of *independent* opinion. Ten feeds that all resell the same
underlying model are worth less than three that disagree for real reasons, so
the roster deliberately mixes kinds:

* **projection** feeds -- fantasy sites publishing expected points
* **ranking** feeds -- expert or consensus orderings
* **market** inputs -- sportsbook lines, which are the sharpest public signal
  available because real money moves them
* **model** inputs -- computed here from recent usage, which never goes down and
  never needs a key

Sources that need an API key stay dormant until one is present, and every source
reports its own failure rather than raising, so a dead feed on a Saturday costs
you that feed and nothing else.
"""

from __future__ import annotations

import os
from typing import Protocol

from aadfs.rankings.models import RankEntry, SourceRanking
from aadfs.sources.base import HttpCache, fetch_json
from aadfs.sources.espn import ESPNSource
from aadfs.sources.sleeper import SleeperSource

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")


class RankingSource(Protocol):
    """A named contributor of weekly opinion."""

    name: str
    kind: str
    #: Environment variable holding this source's key, if it needs one.
    key_env: str | None
    #: One line shown in the UI and in `aadfs sources doctor`.
    description: str

    def available(self) -> tuple[bool, str]: ...
    def fetch(self, season: int, week: int) -> SourceRanking: ...


class _Base:
    name = "base"
    kind = "projection"
    key_env: str | None = None
    description = ""
    weight = 1.0
    url: str | None = None

    def __init__(self, cache: HttpCache | None = None, weight: float | None = None):
        self.cache = cache
        if weight is not None:
            self.weight = weight

    def api_key(self) -> str | None:
        if not self.key_env:
            return None
        return (os.environ.get(self.key_env) or "").strip() or None

    def available(self) -> tuple[bool, str]:
        """Whether this source can run, and why not if it cannot."""
        if self.key_env and not self.api_key():
            return False, f"needs {self.key_env} to be set"
        return True, "ready"

    def _fail(self, error: str) -> SourceRanking:
        return SourceRanking(source=self.name, ok=False, error=error,
                             kind=self.kind, weight=self.weight, url=self.url)

    def _ok(self, entries: list[RankEntry], from_cache: bool = False) -> SourceRanking:
        return SourceRanking(source=self.name, entries=entries, ok=True,
                             kind=self.kind, weight=self.weight,
                             from_cache=from_cache, url=self.url)


# --- projection feeds we already speak ---------------------------------------

class SleeperRankings(_Base):
    name = "sleeper"
    kind = "projection"
    description = "Sleeper weekly projections. Free, no key."
    url = "https://sleeper.com"

    def fetch(self, season: int, week: int) -> SourceRanking:
        result = SleeperSource(cache=self.cache).fetch(season, week)
        if not result.ok:
            return self._fail(result.error or "fetch failed")
        entries = [
            RankEntry(name=row.name, position=row.position, team=row.team,
                      opponent=row.opponent, points=row.fanduel_points())
            for row in result.rows
            if row.fanduel_points() is not None
        ]
        return self._ok(entries, result.from_cache)


class ESPNRankings(_Base):
    name = "espn"
    kind = "projection"
    description = "ESPN weekly projected stat lines. Free, no key."
    url = "https://fantasy.espn.com"

    def fetch(self, season: int, week: int) -> SourceRanking:
        result = ESPNSource(cache=self.cache).fetch(season, week)
        if not result.ok:
            return self._fail(result.error or "fetch failed")
        entries = [
            RankEntry(name=row.name, position=row.position, team=row.team,
                      points=row.fanduel_points())
            for row in result.rows
            if row.fanduel_points() is not None
        ]
        return self._ok(entries, result.from_cache)


# --- additional public feeds --------------------------------------------------

class NFLComRankings(_Base):
    """NFL.com's own fantasy projections."""

    name = "nfl_com"
    kind = "projection"
    description = "NFL.com fantasy projections. Free, no key."
    url = "https://fantasy.nfl.com"
    ENDPOINT = "https://api.fantasy.nfl.com/v2/players/weekprojectedstats"

    def fetch(self, season: int, week: int) -> SourceRanking:
        try:
            payload, cached = fetch_json(
                self.ENDPOINT,
                cache=self.cache,
                params={"season": season, "week": week},
            )
        except Exception as exc:
            return self._fail(f"{type(exc).__name__}: {exc}")

        players = (((payload or {}).get("games") or {}).get(str(season))
                   or {}).get("players") or {}
        if not players:
            return self._fail("response contained no players (endpoint shape may have changed)")

        entries = []
        for player_id, record in players.items():
            stats = (record.get("weekProjectedStats") or {}).get(str(week)) or {}
            points = stats.get("pts") or record.get("projectedPts")
            if points is None:
                continue
            try:
                value = float(points)
            except (TypeError, ValueError):
                continue
            entries.append(
                RankEntry(name=record.get("name") or player_id,
                          position=record.get("position"),
                          team=record.get("teamAbbr"), points=value)
            )
        if not entries:
            return self._fail("no player had a projection in the response")
        return self._ok(entries, cached)


class FantasyNerdsRankings(_Base):
    """Fantasy Nerds weekly rankings.

    Their API accepts the literal key `TEST` for exploring the response shape,
    so this source stays usable for a trial run before any subscription.
    """

    name = "fantasy_nerds"
    kind = "ranking"
    key_env = "FANTASYNERDS_API_KEY"
    description = "Fantasy Nerds weekly rankings. Key required ('TEST' works for trials)."
    url = "https://api.fantasynerds.com"
    ENDPOINT = "https://api.fantasynerds.com/v1/nfl/weekly-rankings"

    def fetch(self, season: int, week: int) -> SourceRanking:
        key = self.api_key()
        if not key:
            return self._fail(f"needs {self.key_env}")
        try:
            payload, cached = fetch_json(
                self.ENDPOINT, cache=self.cache,
                params={"apikey": key, "week": week},
            )
        except Exception as exc:
            return self._fail(f"{type(exc).__name__}: {exc}")

        # The feed groups players by position under a 'players' key.
        blocks = payload.get("players") if isinstance(payload, dict) else payload
        if isinstance(blocks, dict):
            rows = [r for group in blocks.values() if isinstance(group, list) for r in group]
        elif isinstance(blocks, list):
            rows = blocks
        else:
            return self._fail("unexpected response shape")

        entries = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("player_name")
            if not name:
                continue
            rank = row.get("rank") or row.get("position_rank")
            points = row.get("proj_pts") or row.get("projected_points")
            entries.append(
                RankEntry(
                    name=name,
                    position=row.get("position") or row.get("pos"),
                    team=row.get("team"),
                    rank=float(rank) if rank not in (None, "") else None,
                    points=float(points) if points not in (None, "") else None,
                )
            )
        if not entries:
            return self._fail("no usable rows in the response")
        return self._ok(entries, cached)


# --- market inputs ------------------------------------------------------------

class VegasTeamTotals(_Base):
    """Implied team totals from sportsbook lines.

    This does not rank players directly; it produces a team-level expectation
    that the blend uses as context. A back-up running back on a team implied for
    31 points is in a materially better spot than the same player on a team
    implied for 16, and the betting market prices that faster than any fantasy
    site updates its projections.
    """

    name = "vegas_totals"
    kind = "market"
    key_env = "ODDS_API_KEY"
    description = "Implied team totals from sportsbook lines. Free tier key from the-odds-api.com."
    url = "https://the-odds-api.com"
    weight = 0.0  # contextual: reported, not blended into player ranks
    ENDPOINT = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"

    def fetch(self, season: int, week: int) -> SourceRanking:
        key = self.api_key()
        if not key:
            return self._fail(f"needs {self.key_env}")
        try:
            payload, cached = fetch_json(
                self.ENDPOINT, cache=self.cache,
                params={"apiKey": key, "regions": "us",
                        "markets": "spreads,totals", "oddsFormat": "american"},
            )
        except Exception as exc:
            return self._fail(f"{type(exc).__name__}: {exc}")

        entries: list[RankEntry] = []
        for game in payload or []:
            home, away = game.get("home_team"), game.get("away_team")
            total = spread = None
            for book in game.get("bookmakers") or []:
                for market in book.get("markets") or []:
                    outcomes = market.get("outcomes") or []
                    if market.get("key") == "totals" and outcomes:
                        total = outcomes[0].get("point")
                    elif market.get("key") == "spreads":
                        for outcome in outcomes:
                            if outcome.get("name") == home:
                                spread = outcome.get("point")
                if total is not None and spread is not None:
                    break
            if total is None or spread is None:
                continue
            # Implied total = half the game total, adjusted by half the spread.
            home_total = total / 2.0 - spread / 2.0
            away_total = total / 2.0 + spread / 2.0
            for team, implied, opponent in (
                (home, home_total, away), (away, away_total, home)
            ):
                entries.append(
                    RankEntry(name=str(team), position="TEAM", team=str(team),
                              opponent=str(opponent), points=round(implied, 2),
                              extra={"game_total": total, "spread": spread})
                )
        if not entries:
            return self._fail("no games with both a total and a spread")
        return self._ok(entries, cached)


# --- computed locally ---------------------------------------------------------

class RecentFormModel(_Base):
    """A projection computed here from recent usage.

    This source exists because every other one can fail. It needs no key, no
    subscription and no third party staying up on a Saturday -- only the nflverse
    results already cached on disk. It is also a genuine check on the others: a
    player every feed loves but who has not seen the field in a month shows up
    here immediately.

    Recent weeks are weighted more heavily than old ones, because role changes
    are the thing a season average is slowest to notice.
    """

    name = "recent_form"
    kind = "model"
    description = "Recency-weighted model built from nflverse results. No key, works offline."
    weight = 0.8

    #: Each week further back counts this much less than the one after it.
    DECAY = 0.82
    #: Weeks of history considered.
    LOOKBACK = 6
    #: A player needs this many games in the window to be ranked at all.
    MIN_GAMES = 2

    def __init__(self, cache_dir: str | None = None, weight: float | None = None,
                 seasons: list[int] | None = None):
        from aadfs.config import cache_dir as default_cache_dir

        super().__init__(None, weight)
        self.cache_dir = cache_dir or default_cache_dir()
        self.seasons = seasons

    def available(self) -> tuple[bool, str]:
        return True, "ready (uses cached nflverse results)"

    def fetch(self, season: int, week: int) -> SourceRanking:
        from aadfs.sources.nflverse import load_history

        seasons = self.seasons or ([season] if week > self.MIN_GAMES else [season, season - 1])
        try:
            history = load_history(seasons, cache_dir=self.cache_dir)
        except Exception as exc:
            # Fall back to the prior season alone, which is likely already cached.
            try:
                history = load_history([season - 1], cache_dir=self.cache_dir)
            except Exception:
                return self._fail(f"{type(exc).__name__}: {exc}")

        if history.empty:
            return self._fail("no history available")
        if "season_type" in history.columns:
            history = history[history["season_type"] == "REG"]

        # Order weeks across seasons so the decay runs over real time.
        history = history.assign(
            _order=history["season"].astype(int) * 100 + history["week"].astype(int)
        )
        cutoff = season * 100 + week
        window = history[history["_order"] < cutoff].sort_values("_order")
        if window.empty:
            return self._fail(f"no results before {season} week {week}")

        recent_orders = sorted(window["_order"].unique())[-self.LOOKBACK:]
        window = window[window["_order"].isin(recent_orders)]
        age = {order: len(recent_orders) - 1 - i for i, order in enumerate(recent_orders)}

        entries: list[RankEntry] = []
        for (name, position), group in window.groupby(["player_display_name", "position"]
                                                      if "player_display_name" in window.columns
                                                      else ["player_name", "position"]):
            if position not in FANTASY_POSITIONS:
                continue
            if len(group) < self.MIN_GAMES:
                continue
            weights, total = [], 0.0
            for row in group.itertuples():
                w = self.DECAY ** age.get(row._asdict().get("_order", 0), 0)
                weights.append(w)
                total += w * float(row.fd_points)
            if not weights:
                continue
            team = None
            for column in ("recent_team", "team"):
                if column in group.columns:
                    team = group[column].iloc[-1]
                    break
            entries.append(
                RankEntry(name=str(name), position=str(position),
                          team=str(team) if team else None,
                          points=round(total / sum(weights), 2))
            )

        if not entries:
            return self._fail("no player met the minimum games threshold")
        return self._ok(entries)


class CsvRankings(_Base):
    """Rankings or projections from a CSV you supply.

    This is how a paid source joins the blend. Anything with a player column and
    either a rank or a projection works, which covers every subscription export
    worth having as well as a FantasyPros download.
    """

    kind = "ranking"

    def __init__(self, path: str, name: str | None = None, weight: float = 1.0,
                 kind: str = "ranking"):
        super().__init__(None, weight)
        from pathlib import Path

        self.path = Path(path)
        self.name = name or self.path.stem
        self.kind = kind
        self.description = f"Your CSV: {self.path.name}"

    def available(self) -> tuple[bool, str]:
        if not self.path.exists():
            return False, f"file not found: {self.path}"
        return True, "ready"

    def fetch(self, season: int, week: int) -> SourceRanking:
        import csv

        if not self.path.exists():
            return self._fail(f"file not found: {self.path}")

        def norm(value: str) -> str:
            return "".join(ch for ch in str(value).lower() if ch.isalnum())

        try:
            text = self.path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return self._fail(f"could not read file: {exc}")

        reader = csv.DictReader(text.splitlines())
        if not reader.fieldnames:
            return self._fail("file has no header row")
        columns = {norm(f): f for f in reader.fieldnames}

        def pick(*options):
            for option in options:
                if option in columns:
                    return columns[option]
            return None

        name_col = pick("player", "playername", "name", "fullname", "playernameid")
        rank_col = pick("rank", "rk", "ecr", "overallrank", "positionrank", "posrank")
        points_col = pick("fpts", "proj", "projection", "points", "projectedpoints",
                          "fantasypoints")
        if not name_col:
            return self._fail(
                f"no player-name column. Columns: {', '.join(reader.fieldnames)}"
            )
        if not rank_col and not points_col:
            return self._fail(
                f"needs a rank or projection column. Columns: {', '.join(reader.fieldnames)}"
            )

        pos_col = pick("pos", "position")
        team_col = pick("team", "tm")

        def number(value):
            if value is None:
                return None
            text = str(value).strip().replace(",", "")
            # FantasyPros writes position ranks like "WR12"; keep the digits.
            digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
            try:
                return float(digits) if digits else None
            except ValueError:
                return None

        entries = []
        for row in reader:
            name = (row.get(name_col) or "").strip()
            if not name:
                continue
            entries.append(
                RankEntry(
                    name=name,
                    position=(row.get(pos_col) or None) if pos_col else None,
                    team=(row.get(team_col) or None) if team_col else None,
                    rank=number(row.get(rank_col)) if rank_col else None,
                    points=number(row.get(points_col)) if points_col else None,
                )
            )
        if not entries:
            return self._fail("no usable rows")
        return self._ok(entries)
