"""Historical weekly results from nflverse.

nflverse publishes open, well-maintained NFL data as CSV release assets. We use
it for two things the projection feeds cannot give us:

1. **Actual FanDuel points per player-week**, for backtesting a week after the
   fact and for scoring how well a lineup would have done.
2. **Real variance**, so floors and ceilings come from how a player has actually
   scored rather than from a rule of thumb.

Asset naming has changed across nflverse versions, so several candidate URLs are
tried for each season.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from aadfs.names import normalize_name, normalize_team
from aadfs.scoring import dst_points_allowed_score
from aadfs.sources.base import DEFAULT_CACHE_DIR, USER_AGENT

_RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"
_CANDIDATES = (
    f"{_RELEASE}/player_stats/player_stats_{{season}}.csv",
    f"{_RELEASE}/stats_player/stats_player_week_{{season}}.csv",
    f"{_RELEASE}/player_stats/stats_player_week_{{season}}.csv",
)

# nflverse column -> StatLine-equivalent weight under FanDuel scoring.
_POINT_WEIGHTS = {
    "passing_yards": 0.04,
    "passing_tds": 4.0,
    "interceptions": -1.0,
    "rushing_yards": 0.1,
    "rushing_tds": 6.0,
    "receptions": 0.5,
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "special_teams_tds": 6.0,
    "passing_2pt_conversions": 2.0,
    "rushing_2pt_conversions": 2.0,
    "receiving_2pt_conversions": 2.0,
    "sack_fumbles_lost": -2.0,
    "rushing_fumbles_lost": -2.0,
    "receiving_fumbles_lost": -2.0,
}

#: Fallback week-to-week standard deviation by position, used when a player has
#: too little history of their own. Derived from typical NFL scoring spread;
#: `calibrate_position_variance` recomputes these from real data.
DEFAULT_POSITION_SD = {"QB": 6.5, "RB": 6.0, "WR": 6.5, "TE": 4.5, "DST": 4.0}

#: A player needs at least this many games before we trust their own variance.
MIN_GAMES_FOR_OWN_SD = 4


def download_season(season: int, cache_dir: Path | str = DEFAULT_CACHE_DIR,
                    refresh: bool = False) -> pd.DataFrame:
    """Fetch one season of weekly player stats, caching the CSV locally."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = cache_dir / f"nflverse_player_stats_{season}.csv"

    if local.exists() and not refresh and local.stat().st_size > 1024:
        return pd.read_csv(local, low_memory=False)

    import httpx

    errors = []
    for template in _CANDIDATES:
        url = template.format(season=season)
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                response = client.get(url, headers={"User-Agent": USER_AGENT})
            if response.status_code != 200 or len(response.content) < 1024:
                errors.append(f"{url} -> HTTP {response.status_code}")
                continue
            local.write_bytes(response.content)
            return pd.read_csv(local, low_memory=False)
        except Exception as exc:  # try the next naming scheme
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")

    raise RuntimeError(
        f"Could not download nflverse stats for {season}. Tried:\n  " + "\n  ".join(errors)
    )


def add_fanduel_points(frame: pd.DataFrame) -> pd.DataFrame:
    """Add a `fd_points` column scored under FanDuel's rules."""
    frame = frame.copy()
    total = pd.Series(0.0, index=frame.index)
    for column, weight in _POINT_WEIGHTS.items():
        if column in frame.columns:
            total = total + pd.to_numeric(frame[column], errors="coerce").fillna(0.0) * weight
    frame["fd_points"] = total.round(2)
    return frame


def load_history(seasons: list[int], cache_dir: Path | str = DEFAULT_CACHE_DIR,
                 refresh: bool = False) -> pd.DataFrame:
    """Load and score several seasons of weekly stats."""
    frames = []
    for season in seasons:
        frame = add_fanduel_points(download_season(season, cache_dir, refresh))
        frames.append(frame)
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    name_column = next(
        (c for c in ("player_display_name", "player_name") if c in combined.columns), None
    )
    combined["name_key"] = (
        combined[name_column].map(normalize_name) if name_column else ""
    )
    team_column = next(
        (c for c in ("recent_team", "team") if c in combined.columns), None
    )
    combined["team_code"] = (
        combined[team_column].map(normalize_team) if team_column else None
    )
    if "position" in combined.columns:
        combined["position"] = combined["position"].replace({"FB": "RB", "HB": "RB"})
    return combined


def player_variance(history: pd.DataFrame, min_points: float = 1.0) -> dict[str, float]:
    """Per-player week-to-week standard deviation of FanDuel points.

    Weeks where a player scored essentially nothing are kept, because a zero is
    exactly the downside a cash lineup is trying to avoid.
    """
    if history.empty or "fd_points" not in history.columns:
        return {}
    grouped = history.groupby("name_key")["fd_points"]
    counts, sds = grouped.count(), grouped.std(ddof=1)
    return {
        key: float(sd)
        for key, sd in sds.items()
        if counts.get(key, 0) >= MIN_GAMES_FOR_OWN_SD and np.isfinite(sd) and sd > 0
    }


def calibrate_position_variance(history: pd.DataFrame,
                                min_mean_points: float = 5.0) -> dict[str, float]:
    """Position-level standard deviations measured from real history.

    Restricted to players averaging a meaningful score, so that the long tail of
    never-plays does not drag the estimate toward zero.
    """
    if history.empty or "position" not in history.columns:
        return dict(DEFAULT_POSITION_SD)

    means = history.groupby("name_key")["fd_points"].mean()
    keep = set(means[means >= min_mean_points].index)
    subset = history[history["name_key"].isin(keep)]

    result = dict(DEFAULT_POSITION_SD)
    for position, group in subset.groupby("position"):
        if position not in result:
            continue
        sd = group.groupby("name_key")["fd_points"].std(ddof=1).median()
        if np.isfinite(sd) and sd > 0:
            result[str(position)] = float(round(sd, 2))
    return result


def weekly_actuals(history: pd.DataFrame, season: int, week: int) -> dict[str, float]:
    """FanDuel points actually scored in one week, keyed by normalised name."""
    if history.empty:
        return {}
    mask = (history["season"] == season) & (history["week"] == week)
    subset = history[mask]
    return {
        str(row.name_key): float(row.fd_points)
        for row in subset.itertuples()
        if row.name_key
    }


# --- team defense ------------------------------------------------------------
# nflverse's player stats do not cover team defenses, so D/ST is scored from the
# team-week file instead. Points allowed is not published directly either, so it
# is reconstructed from the opposing team's touchdowns, field goals and extra
# points; that reconstruction reproduces real final scores exactly.

_TEAM_RELEASE = (
    f"{_RELEASE}/stats_team/stats_team_week_{{season}}.csv",
    f"{_RELEASE}/team_stats/team_stats_week_{{season}}.csv",
)

_DST_WEIGHTS = {
    "def_sacks": 1.0,
    "def_interceptions": 2.0,
    "fumble_recovery_opp": 2.0,
    "def_tds": 6.0,
    "special_teams_tds": 6.0,
    "def_safeties": 2.0,
    "def_punt_blocks": 2.0,
    "def_fg_blocks": 2.0,
    "def_pat_blocks": 2.0,
    "def_2pt_made": 2.0,
}


def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=frame.index)


def points_scored(frame: pd.DataFrame) -> pd.Series:
    """Reconstruct each team's points scored in a week.

    Receiving touchdowns are deliberately excluded: they are the same scores as
    the passing touchdowns, counted from the other end.
    """
    touchdowns = (
        _column(frame, "passing_tds")
        + _column(frame, "rushing_tds")
        + _column(frame, "special_teams_tds")
        + _column(frame, "def_tds")
        + _column(frame, "fumble_recovery_tds")
    )
    two_point = (
        _column(frame, "passing_2pt_conversions")
        + _column(frame, "rushing_2pt_conversions")
        + _column(frame, "def_2pt_made")
    )
    return (
        6 * touchdowns
        + 3 * _column(frame, "fg_made")
        + _column(frame, "pat_made")
        + 2 * _column(frame, "def_safeties")
        + 2 * two_point
    )


def download_team_season(season: int, cache_dir: Path | str = DEFAULT_CACHE_DIR,
                         refresh: bool = False) -> pd.DataFrame:
    """Fetch one season of weekly team stats."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = cache_dir / f"nflverse_team_stats_{season}.csv"
    if local.exists() and not refresh and local.stat().st_size > 1024:
        return pd.read_csv(local, low_memory=False)

    import httpx

    errors = []
    for template in _TEAM_RELEASE:
        url = template.format(season=season)
        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                response = client.get(url, headers={"User-Agent": USER_AGENT})
            if response.status_code != 200 or len(response.content) < 1024:
                errors.append(f"{url} -> HTTP {response.status_code}")
                continue
            local.write_bytes(response.content)
            return pd.read_csv(local, low_memory=False)
        except Exception as exc:
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")

    raise RuntimeError(
        f"Could not download nflverse team stats for {season}. Tried:\n  "
        + "\n  ".join(errors)
    )


def score_team_defenses(frame: pd.DataFrame) -> pd.DataFrame:
    """Add `dst_points` to a team-week frame, under FanDuel D/ST scoring."""
    frame = frame.copy()
    frame["team_code"] = frame["team"].map(normalize_team)
    frame["opponent_code"] = frame.get("opponent_team", pd.Series(index=frame.index)).map(
        normalize_team
    )
    frame["points_scored"] = points_scored(frame)

    # Points allowed is the opposing team's score in the same game.
    allowed = frame.set_index(["season", "week", "team_code"])["points_scored"]
    keys = list(zip(frame["season"], frame["week"], frame["opponent_code"]))
    frame["points_allowed"] = [allowed.get(key, float("nan")) for key in keys]

    total = pd.Series(0.0, index=frame.index)
    for column, weight in _DST_WEIGHTS.items():
        total = total + _column(frame, column) * weight
    total = total + frame["points_allowed"].map(
        lambda value: dst_points_allowed_score(value) if pd.notna(value) else 0.0
    )
    frame["dst_points"] = total.round(2)
    return frame


def weekly_dst_actuals(
    season: int, week: int, cache_dir: Path | str = DEFAULT_CACHE_DIR
) -> dict[str, float]:
    """FanDuel D/ST points actually scored in one week, keyed by team code."""
    frame = score_team_defenses(download_team_season(season, cache_dir))
    mask = (frame["season"] == season) & (frame["week"] == week)
    return {
        str(row.team_code): float(row.dst_points)
        for row in frame[mask].itertuples()
        if isinstance(row.team_code, str)
    }
