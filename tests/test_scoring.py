import pytest

from aadfs.scoring import RosterRules, StatLine, dst_points_allowed_score, score_statline


def test_passing_line_scores_at_quarter_point_per_yard():
    # 300 yards = 12 pts, 2 TD = 8, 1 INT = -1
    assert score_statline(StatLine(pass_yd=300, pass_td=2, interceptions=1)) == 19.0


def test_receiving_is_half_ppr():
    # 8 catches = 4, 110 yards = 11, 1 TD = 6
    assert score_statline(StatLine(receptions=8, rec_yd=110, rec_td=1)) == 21.0


def test_rushing_and_fumbles():
    assert score_statline(StatLine(rush_yd=95, rush_td=1, fumbles_lost=1)) == 13.5


def test_two_point_and_return_touchdowns_count():
    assert score_statline(StatLine(two_point_conversions=1, return_td=1)) == 8.0


@pytest.mark.parametrize(
    "allowed,expected",
    [(0, 10.0), (6, 7.0), (7, 4.0), (13, 4.0), (20, 1.0), (27, 0.0), (34, -1.0), (52, -4.0)],
)
def test_dst_points_allowed_buckets(allowed, expected):
    assert dst_points_allowed_score(allowed) == expected


def test_dst_full_line():
    line = StatLine(dst_sacks=4, dst_interceptions=2, dst_tds=1, dst_points_allowed=10)
    # 4 + 4 + 6 + 4 (points-allowed bucket)
    assert score_statline(line) == 18.0


def test_roster_rules_defaults_match_fanduel_classic():
    rules = RosterRules()
    assert rules.salary_cap == 60_000
    assert rules.roster_size == 9
    assert sum(low for low, _ in rules.slots.values()) == 8  # 9th is the FLEX
    assert rules.flex_group_total == 7
