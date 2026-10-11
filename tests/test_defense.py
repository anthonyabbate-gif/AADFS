"""Defence vs position: aggregation, rank direction, and schedule adjustment."""

import pandas as pd
import pytest

from aadfs.defense import (
    MIN_GAMES_FOR_CONFIDENCE, POSITIONS, DefenseBoard, DefenseRating,
    build_board, position_game_log, upcoming_matchups,
)


def frame(rows):
    """Build a minimal weekly-stats frame; one row per player-week."""
    return pd.DataFrame([
        {"season": 2026, "season_type": "REG", "week": w, "team_code": off,
         "opponent_team": deff, "position": pos, "fd_points": pts,
         "player_display_name": name}
        for w, off, deff, pos, pts, name in rows
    ])


# --- aggregation --------------------------------------------------------------

def test_points_are_summed_across_a_position_group():
    log = position_game_log(frame([
        (1, "BUF", "NYJ", "WR", 10.0, "A"),
        (1, "BUF", "NYJ", "WR", 7.0, "B"),
        (1, "BUF", "NYJ", "QB", 20.0, "C"),
    ]))
    wr = log[(log.defense == "NYJ") & (log.position == "WR")]
    assert len(wr) == 1
    assert wr.iloc[0]["points"] == 17.0


def test_non_fantasy_positions_are_ignored():
    log = position_game_log(frame([
        (1, "BUF", "NYJ", "WR", 10.0, "A"),
        (1, "BUF", "NYJ", "LB", 99.0, "D"),
        (1, "BUF", "NYJ", "CB", 99.0, "E"),
    ]))
    assert set(log.position) == {"WR"}


def test_postseason_is_excluded():
    data = frame([(1, "BUF", "NYJ", "WR", 10.0, "A")])
    post = data.copy()
    post["season_type"] = "POST"
    assert position_game_log(pd.concat([data, post])).shape[0] == 1


def test_empty_history_gives_an_empty_board():
    board = build_board(pd.DataFrame(), 2026)
    assert board.ratings == []
    assert board.teams == []


# --- rank direction -----------------------------------------------------------

def _three_defenses():
    """NYJ allows least, MIA middle, NE most -- against identical offences."""
    rows = []
    for week, (defense, points) in enumerate(
        [("NYJ", 5.0), ("MIA", 15.0), ("NE", 25.0)], start=1
    ):
        for offense in ("BUF", "KC", "SF"):
            rows.append((week, offense, defense, "WR", points, f"{offense}wr"))
    return frame(rows)


def test_rank_one_is_the_toughest_defence():
    board = build_board(_three_defenses(), 2026)
    assert board.get("NYJ", "WR").rank == 1
    assert board.get("NE", "WR").rank == 3


def test_allowed_per_game_is_a_mean_not_a_total():
    rows = [(w, "BUF", "NYJ", "WR", 10.0, "a") for w in (1, 2, 3)]
    board = build_board(frame(rows), 2026)
    rating = board.get("NYJ", "WR")
    assert rating.allowed_per_game == 10.0
    assert rating.games == 3


def test_labels_follow_the_rank_not_the_raw_points():
    board = build_board(_three_defenses(), 2026)
    assert "tough" in board.get("NYJ", "WR").label
    assert "generous" in board.get("NE", "WR").label


# --- the schedule adjustment --------------------------------------------------

def test_a_defence_that_faced_only_strong_offences_ranks_better_once_adjusted():
    """The whole reason the adjusted number exists.

    HARD faces only elite offences and allows a lot; EASY faces only poor ones
    and allows little. Raw ranking says EASY is better. Adjusted should see
    that HARD actually held its opponents below their own norms.
    """
    rows = []
    # Elite offences normally score 30; poor ones normally score 10.
    for week in (1, 2, 3, 4):
        rows.append((week, "ELITE1", "OTHER", "WR", 30.0, "e1"))
        rows.append((week, "ELITE2", "OTHER", "WR", 30.0, "e2"))
        rows.append((week, "POOR1", "OTHER", "WR", 10.0, "p1"))
        rows.append((week, "POOR2", "OTHER", "WR", 10.0, "p2"))
    # HARD holds elite offences to 25 (below their 30 norm).
    rows += [(5, "ELITE1", "HARD", "WR", 25.0, "e1"),
             (6, "ELITE2", "HARD", "WR", 25.0, "e2")]
    # EASY lets poor offences reach 15 (above their 10 norm).
    rows += [(5, "POOR1", "EASY", "WR", 15.0, "p1"),
             (6, "POOR2", "EASY", "WR", 15.0, "p2")]

    board = build_board(frame(rows), 2026)
    hard, easy = board.get("HARD", "WR"), board.get("EASY", "WR")

    # Raw: EASY looks far better, because it allowed 15 against HARD's 25.
    assert easy.allowed_per_game < hard.allowed_per_game
    assert easy.rank < hard.rank
    # Adjusted: HARD suppressed its opponents, EASY inflated its own.
    assert hard.above_expected < 0 < easy.above_expected
    assert hard.adjusted_rank < easy.adjusted_rank
    # And the gap is visible as a schedule effect.
    assert hard.rank_shift > 0


def test_the_baseline_excludes_the_game_being_judged():
    """Otherwise a defence is compared against an average it helped set."""
    rows = [
        (1, "OFF", "D1", "WR", 10.0, "a"),
        (2, "OFF", "D2", "WR", 10.0, "a"),
        (3, "OFF", "D3", "WR", 40.0, "a"),
    ]
    board = build_board(frame(rows), 2026)
    # D3's baseline is the other two games (10.0), not the 20.0 three-game mean.
    assert board.get("D3", "WR").above_expected == pytest.approx(30.0)
    assert board.get("D1", "WR").above_expected == pytest.approx(-15.0)


def test_an_offence_with_a_single_game_contributes_no_adjustment():
    """With one game there is no 'normally', so it cannot adjust anything."""
    rows = [(1, "ONEOFF", "D1", "WR", 40.0, "a")]
    board = build_board(frame(rows), 2026)
    assert board.get("D1", "WR").above_expected == 0.0


# --- recent form and trend ----------------------------------------------------

def test_recent_window_only_counts_the_last_n_weeks():
    rows = [(w, "BUF", "NYJ", "WR", 30.0, "a") for w in (1, 2)]
    rows += [(w, "BUF", "NYJ", "WR", 5.0, "a") for w in (3, 4, 5, 6)]
    board = build_board(frame(rows), 2026, recent_window=4)
    rating = board.get("NYJ", "WR")
    assert rating.recent_games == 4
    assert rating.recent_allowed == 5.0
    assert rating.allowed_per_game > rating.recent_allowed


def test_trend_reports_a_defence_tightening_up():
    """A ranked league wide enough for a rank move to mean something."""
    others = [f"D{i:02d}" for i in range(20)]
    rows = []
    for w in (1, 2, 3, 4, 5, 6, 7, 8):
        # A spread of steady mid-table defences to rank against.
        for i, team in enumerate(others):
            rows.append((w, f"OFF{i}", team, "WR", 10.0 + i, f"p{i}"))
        # SWING is the worst in the league early and the best late.
        rows.append((w, "SWINGOFF", "SWING", "WR", 40.0 if w <= 4 else 1.0, "s"))
    board = build_board(frame(rows), 2026, recent_window=4)
    assert board.get("SWING", "WR").trend == "tightening"


def test_trend_reports_a_defence_slipping():
    others = [f"D{i:02d}" for i in range(20)]
    rows = []
    for w in (1, 2, 3, 4, 5, 6, 7, 8):
        for i, team in enumerate(others):
            rows.append((w, f"OFF{i}", team, "WR", 10.0 + i, f"p{i}"))
        rows.append((w, "SWINGOFF", "SWING", "WR", 1.0 if w <= 4 else 40.0, "s"))
    board = build_board(frame(rows), 2026, recent_window=4)
    assert board.get("SWING", "WR").trend == "slipping"


def test_a_steady_defence_shows_no_trend():
    others = [f"D{i:02d}" for i in range(20)]
    rows = []
    for w in (1, 2, 3, 4, 5, 6, 7, 8):
        for i, team in enumerate(others):
            rows.append((w, f"OFF{i}", team, "WR", 10.0 + i, f"p{i}"))
    board = build_board(frame(rows), 2026, recent_window=4)
    assert board.get("D05", "WR").trend == "steady"


def test_the_trend_threshold_scales_with_the_league_size():
    """Six places means something in a 32-team league and nothing in a 3-team one."""
    small = DefenseRating(team="X", position="WR", rank=1, recent_rank=3,
                          teams_ranked=3)
    big = DefenseRating(team="X", position="WR", rank=1, recent_rank=3,
                        teams_ranked=32)
    assert small.trend == "slipping"   # 2 of 3 places is a real move
    assert big.trend == "steady"       # 2 of 32 places is noise


def test_small_samples_are_flagged_as_unreliable():
    rows = [(1, "BUF", "NYJ", "WR", 10.0, "a")]
    board = build_board(frame(rows), 2026)
    assert board.get("NYJ", "WR").reliable is False
    rows += [(w, "BUF", "NYJ", "WR", 10.0, "a")
             for w in range(2, MIN_GAMES_FOR_CONFIDENCE + 1)]
    board = build_board(frame(rows), 2026)
    assert board.get("NYJ", "WR").reliable is True


def test_through_week_truncates_the_sample():
    rows = [(w, "BUF", "NYJ", "WR", float(w), "a") for w in (1, 2, 3, 4, 5)]
    board = build_board(frame(rows), 2026, through_week=3)
    assert board.get("NYJ", "WR").games == 3
    assert board.through_week == 3


# --- lookups and matchups -----------------------------------------------------

def test_team_lookup_normalises_the_code_and_orders_positions():
    rows = [(1, "BUF", "JAX", pos, 10.0, pos) for pos in POSITIONS]
    board = build_board(frame(rows), 2026)
    # JAX normalises to JAC, FanDuel's spelling.
    assert [r.position for r in board.for_team("JAX")] == list(POSITIONS)
    assert board.get("JAC", "QB") is not None


def test_position_lookup_is_sorted_toughest_first():
    board = build_board(_three_defenses(), 2026)
    ranks = [r.adjusted_rank for r in board.for_position("WR")]
    assert ranks == sorted(ranks)


def test_missing_combinations_return_none():
    board = build_board(_three_defenses(), 2026)
    assert board.get("NYJ", "TE") is None
    assert board.get("NOPE", "WR") is None


def test_matchups_pair_each_offence_with_the_defence_it_faces():
    rows = []
    for w in (1, 2):
        rows.append((w, "KC", "BUF", "QB", 20.0, "a"))
        rows.append((w, "BUF", "KC", "QB", 10.0, "b"))
    board = build_board(frame(rows), 2026)
    schedule = pd.DataFrame([
        {"week": 3, "away": "BUF", "home": "KC", "gameday": "2026-10-18"}
    ])
    matchups = upcoming_matchups(board, schedule, 3)
    assert len(matchups) == 1
    game = matchups[0]
    # Buffalo's offence faces the Kansas City defence, and vice versa.
    assert game.faces["BUF"]["QB"].team == "KC"
    assert game.faces["KC"]["QB"].team == "BUF"


def test_matchups_for_a_week_with_no_games_is_empty():
    board = build_board(_three_defenses(), 2026)
    schedule = pd.DataFrame([{"week": 3, "away": "BUF", "home": "KC"}])
    assert upcoming_matchups(board, schedule, 9) == []


def test_rating_serialises_every_displayed_field():
    board = build_board(_three_defenses(), 2026)
    data = board.get("NYJ", "WR").as_dict()
    for key in ("team", "position", "allowed_per_game", "rank", "above_expected",
                "adjusted_rank", "rank_shift", "games", "league_average",
                "label", "trend", "reliable", "teams_ranked"):
        assert key in data
