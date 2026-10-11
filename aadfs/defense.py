"""How each defence performs against each fantasy position.

The familiar version of this stat -- "the Bills are 31st against quarterbacks"
-- is fantasy points allowed per game to that position. It is genuinely useful
and it is also the most commonly misread number in fantasy, because it says as
much about a defence's schedule as about the defence.

A unit that has faced three elite quarterbacks looks porous; one that has faced
three bad ones looks impenetrable. Neither tells you much about the fourth.

So two numbers are produced for every defence and position:

* **allowed per game** -- the raw figure, which is what every site publishes and
  what people mean when they quote a rank.
* **above expected** -- the same games, but each opponent's output measured
  against what *that* offence normally produces. A defence is credited for
  holding a good offence below its own standard, not for having played a bad one.

They are reported side by side on purpose. When they disagree sharply, the gap
is the schedule, and the adjusted number is the one worth believing.

Rank 1 is always the toughest defence, 32 the most generous, for both measures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from aadfs.names import normalize_team

#: Positions a defence is rated against.
POSITIONS = ("QB", "RB", "WR", "TE")

#: Games in the "recent" window. Four is roughly a month, which is long enough
#: to mean something and short enough to catch an injury or scheme change.
RECENT_WINDOW = 4

#: Below this many games, ranks are noise and are labelled as such.
MIN_GAMES_FOR_CONFIDENCE = 4

#: How far a defence must move between its season and recent ranks before the
#: shift is called a trend, as a share of the teams ranked. Roughly six places
#: in a full 32-team league.
TREND_SHARE = 0.18


def _label(rank: int, total: int) -> str:
    """Plain-language reading of a rank, since 1 vs 32 is easy to invert."""
    if total <= 0:
        return "unranked"
    share = rank / total
    if share <= 0.19:
        return "very tough"
    if share <= 0.38:
        return "tough"
    if share <= 0.62:
        return "average"
    if share <= 0.81:
        return "generous"
    return "very generous"


@dataclass
class DefenseRating:
    """One defence's record against one position."""

    team: str
    position: str

    allowed_per_game: float = 0.0
    rank: int = 0                     # 1 = toughest, by raw points allowed
    above_expected: float = 0.0       # points per game vs opponents' own norms
    adjusted_rank: int = 0            # 1 = toughest, after adjustment
    games: int = 0

    recent_allowed: float | None = None
    recent_rank: int | None = None
    recent_games: int = 0

    league_average: float = 0.0
    teams_ranked: int = 0

    @property
    def label(self) -> str:
        return _label(self.adjusted_rank or self.rank, self.teams_ranked)

    @property
    def raw_label(self) -> str:
        return _label(self.rank, self.teams_ranked)

    @property
    def rank_shift(self) -> int:
        """Raw rank minus adjusted rank. Large values mean schedule effects."""
        if not self.rank or not self.adjusted_rank:
            return 0
        return self.rank - self.adjusted_rank

    @property
    def trend(self) -> str:
        """Whether the recent window differs from the season as a whole.

        The threshold scales with the size of the league being ranked rather
        than being a fixed number of places, so the same rule still means
        something when only part of the league has played.
        """
        if self.recent_rank is None or not self.rank:
            return "n/a"
        threshold = max(2, round(TREND_SHARE * (self.teams_ranked or 32)))
        shift = self.rank - self.recent_rank
        if shift >= threshold:
            return "tightening"
        if shift <= -threshold:
            return "slipping"
        return "steady"

    @property
    def reliable(self) -> bool:
        return self.games >= MIN_GAMES_FOR_CONFIDENCE

    def as_dict(self) -> dict:
        return {
            "team": self.team, "position": self.position,
            "allowed_per_game": round(self.allowed_per_game, 2),
            "rank": self.rank,
            "above_expected": round(self.above_expected, 2),
            "adjusted_rank": self.adjusted_rank,
            "rank_shift": self.rank_shift,
            "games": self.games,
            "recent_allowed": (round(self.recent_allowed, 2)
                               if self.recent_allowed is not None else None),
            "recent_rank": self.recent_rank,
            "recent_games": self.recent_games,
            "league_average": round(self.league_average, 2),
            "teams_ranked": self.teams_ranked,
            "label": self.label, "raw_label": self.raw_label,
            "trend": self.trend, "reliable": self.reliable,
        }


@dataclass
class DefenseBoard:
    """Every defence against every position, for one season to date."""

    season: int
    through_week: int
    ratings: list[DefenseRating] = field(default_factory=list)
    league_average: dict[str, float] = field(default_factory=dict)
    recent_window: int = RECENT_WINDOW
    generated_at: str = ""

    def for_team(self, team: str) -> list[DefenseRating]:
        code = normalize_team(team)
        order = {p: i for i, p in enumerate(POSITIONS)}
        return sorted(
            (r for r in self.ratings if r.team == code),
            key=lambda r: order.get(r.position, 9),
        )

    def for_position(self, position: str) -> list[DefenseRating]:
        return sorted(
            (r for r in self.ratings if r.position == position.upper()),
            key=lambda r: r.adjusted_rank or r.rank,
        )

    def get(self, team: str, position: str) -> DefenseRating | None:
        code = normalize_team(team)
        for rating in self.ratings:
            if rating.team == code and rating.position == position.upper():
                return rating
        return None

    @property
    def teams(self) -> list[str]:
        return sorted({r.team for r in self.ratings})


def position_game_log(history: pd.DataFrame) -> pd.DataFrame:
    """Points each position group scored in each team-game.

    One row per (week, offence, defence, position). This is the unit everything
    else is built from: a defence's record is the set of rows where it is the
    defence.
    """
    if history.empty:
        return pd.DataFrame(
            columns=["week", "offense", "defense", "position", "points"]
        )

    frame = history
    if "season_type" in frame.columns:
        frame = frame[frame["season_type"] == "REG"]
    frame = frame[frame["position"].isin(POSITIONS)].copy()

    if "team_code" not in frame.columns:
        source = next(
            (c for c in ("recent_team", "team") if c in frame.columns), None
        )
        frame["team_code"] = frame[source].map(normalize_team) if source else None
    frame["defense"] = frame["opponent_team"].map(normalize_team)
    frame = frame[frame["defense"].notna() & frame["team_code"].notna()]

    grouped = (
        frame.groupby(["week", "team_code", "defense", "position"], as_index=False)
        ["fd_points"].sum()
        .rename(columns={"team_code": "offense", "fd_points": "points"})
    )
    grouped["week"] = grouped["week"].astype(int)
    return grouped


def _rank_within_position(frame: pd.DataFrame, column: str, name: str) -> pd.DataFrame:
    """Rank ascending within each position; 1 = fewest allowed = toughest."""
    frame = frame.copy()
    frame[name] = (
        frame.groupby("position")[column].rank(method="min").astype(int)
    )
    return frame


def build_board(
    history: pd.DataFrame,
    season: int,
    through_week: int | None = None,
    recent_window: int = RECENT_WINDOW,
) -> DefenseBoard:
    """Compute every defence's rating against every position."""
    import datetime as dt

    log = position_game_log(history)
    board = DefenseBoard(
        season=season,
        through_week=int(through_week or (log["week"].max() if len(log) else 0)),
        recent_window=recent_window,
        generated_at=dt.datetime.now().isoformat(timespec="seconds"),
    )
    if log.empty:
        return board

    if through_week:
        log = log[log["week"] <= int(through_week)]
    if log.empty:
        return board

    # Each offence's baseline for a position, excluding the game being judged.
    # Leaving the game out matters: otherwise a defence is partly compared
    # against an average it helped create, which flattens the adjustment.
    totals = log.groupby(["offense", "position"])["points"].transform("sum")
    counts = log.groupby(["offense", "position"])["points"].transform("size")
    log = log.assign(
        opponent_baseline=(totals - log["points"]) / (counts - 1).where(counts > 1)
    )
    log["above_expected"] = log["points"] - log["opponent_baseline"]

    season_stats = log.groupby(["defense", "position"], as_index=False).agg(
        allowed_per_game=("points", "mean"), games=("points", "size")
    )
    adjusted = (
        log.dropna(subset=["above_expected"])
        .groupby(["defense", "position"], as_index=False)
        .agg(above_expected=("above_expected", "mean"))
    )
    season_stats = season_stats.merge(adjusted, on=["defense", "position"], how="left")
    season_stats["above_expected"] = season_stats["above_expected"].fillna(0.0)

    season_stats = _rank_within_position(season_stats, "allowed_per_game", "rank")
    season_stats = _rank_within_position(season_stats, "above_expected", "adjusted_rank")

    # The recent window, ranked separately so the trend is comparable.
    latest = int(log["week"].max())
    recent_log = log[log["week"] > latest - recent_window]
    recent_stats = recent_log.groupby(["defense", "position"], as_index=False).agg(
        recent_allowed=("points", "mean"), recent_games=("points", "size")
    )
    if len(recent_stats):
        recent_stats = _rank_within_position(
            recent_stats, "recent_allowed", "recent_rank"
        )

    merged = season_stats.merge(recent_stats, on=["defense", "position"], how="left")
    league = log.groupby("position")["points"].mean().to_dict()
    board.league_average = {k: round(float(v), 2) for k, v in league.items()}
    counts_by_position = merged.groupby("position")["defense"].size().to_dict()

    for row in merged.itertuples():
        board.ratings.append(
            DefenseRating(
                team=str(row.defense),
                position=str(row.position),
                allowed_per_game=float(row.allowed_per_game),
                rank=int(row.rank),
                above_expected=float(row.above_expected),
                adjusted_rank=int(row.adjusted_rank),
                games=int(row.games),
                recent_allowed=(
                    float(row.recent_allowed) if pd.notna(row.recent_allowed) else None
                ),
                recent_rank=(
                    int(row.recent_rank) if pd.notna(row.recent_rank) else None
                ),
                recent_games=(
                    int(row.recent_games) if pd.notna(row.recent_games) else 0
                ),
                league_average=float(league.get(row.position, 0.0)),
                teams_ranked=int(counts_by_position.get(row.position, 0)),
            )
        )
    return board


@dataclass
class Matchup:
    """One upcoming game, with each side's opponent rated by position."""

    week: int
    away: str
    home: str
    kickoff: str | None = None
    #: offence team -> position -> the rating of the defence it faces
    faces: dict[str, dict[str, DefenseRating]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "week": self.week, "away": self.away, "home": self.home,
            "kickoff": self.kickoff,
            "faces": {
                team: {pos: rating.as_dict() for pos, rating in by_pos.items()}
                for team, by_pos in self.faces.items()
            },
        }


def upcoming_matchups(board: DefenseBoard, schedule: pd.DataFrame,
                      week: int) -> list[Matchup]:
    """Join a week's fixtures to each defence's ratings.

    This is the form the ratings are actually used in: not "who is worst against
    tight ends" but "my tight end plays Denver this week -- is that good?"
    """
    games = schedule[schedule["week"] == int(week)]
    matchups: list[Matchup] = []
    for row in games.itertuples():
        away, home = str(row.away), str(row.home)
        matchup = Matchup(
            week=int(week), away=away, home=home,
            kickoff=str(getattr(row, "gameday", "")) or None,
        )
        for offense, defense in ((away, home), (home, away)):
            ratings = {}
            for position in POSITIONS:
                rating = board.get(defense, position)
                if rating is not None:
                    ratings[position] = rating
            matchup.faces[offense] = ratings
        matchups.append(matchup)
    return matchups


# --- weekly refresh -----------------------------------------------------------

def write_board(board: DefenseBoard, output_dir=None) -> dict[str, str]:
    """Save the board as CSV and JSON for the week."""
    import csv
    import io
    import json
    from pathlib import Path

    from aadfs.config import rankings_dir

    directory = Path(output_dir or rankings_dir())
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{board.season}_wk{board.through_week:02d}"

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "team", "position", "allowed_per_game", "rank", "above_expected",
        "adjusted_rank", "rank_shift", "recent_allowed", "recent_rank",
        "games", "league_average", "label", "trend", "reliable",
    ])
    for rating in sorted(board.ratings, key=lambda r: (r.position, r.adjusted_rank)):
        d = rating.as_dict()
        writer.writerow([d[k] for k in (
            "team", "position", "allowed_per_game", "rank", "above_expected",
            "adjusted_rank", "rank_shift", "recent_allowed", "recent_rank",
            "games", "league_average", "label", "trend", "reliable")])

    csv_path = directory / f"defense_{stem}.csv"
    json_path = directory / f"defense_{stem}.json"
    csv_path.write_text(out.getvalue())
    json_path.write_text(json.dumps({
        "season": board.season, "through_week": board.through_week,
        "recent_window": board.recent_window, "generated_at": board.generated_at,
        "league_average": board.league_average,
        "ratings": [r.as_dict() for r in board.ratings],
    }, indent=2))
    return {"csv": str(csv_path), "json": str(json_path)}


def refresh(season: int | None = None, recent_window: int = RECENT_WINDOW,
            output_dir=None) -> dict:
    """Rebuild and save the board from the latest results. Used by the weekly job."""
    from aadfs.pipeline import guess_season_and_week
    from aadfs.sources.nflverse import last_completed_week, load_history

    season = season or guess_season_and_week()[0]
    history = load_history([season])
    try:
        through = last_completed_week(season) or None
    except Exception:
        through = None
    board = build_board(history, season, through_week=through,
                        recent_window=recent_window)
    if not board.ratings:
        return {"ok": False, "error": f"No completed {season} games found."}
    paths = write_board(board, output_dir)
    return {
        "ok": True, "season": season, "through_week": board.through_week,
        "ratings": len(board.ratings), **paths,
    }
