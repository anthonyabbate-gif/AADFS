from aadfs.models import Lineup, Player
from aadfs.scoring import RosterRules


def _p(pid, pos, team, salary=6000, projection=10.0):
    return Player(
        player_id=pid, name=f"P{pid}", position=pos, team=team,
        opponent="OPP", salary=salary, projection=projection,
        floor=projection * 0.6, ceiling=projection * 1.5, stdev=projection * 0.4,
    )


def _nine(extra_position="RB"):
    players = [_p("qb", "QB", "A"), _p("rb1", "RB", "B"), _p("rb2", "RB", "C"),
               _p("wr1", "WR", "D"), _p("wr2", "WR", "E"), _p("wr3", "WR", "F"),
               _p("te1", "TE", "G"), _p("dst", "DST", "H")]
    players.append(_p("flex", extra_position, "I", projection=5.0))
    return Lineup(players=players, rules=RosterRules())


def test_totals():
    lineup = _nine()
    assert lineup.salary == 9 * 6000
    assert lineup.projection == round(8 * 10.0 + 5.0, 2)
    # Each synthetic player is on a different team, so each is its own game.
    assert lineup.game_count == 9


def test_game_key_pairs_opponents_together():
    home = _p("h", "WR", "BUF")
    away = _p("a", "WR", "KC")
    home.opponent, away.opponent = "KC", "BUF"
    assert home.game_key() == away.game_key()


def test_explicit_game_id_wins_over_the_team_pair():
    player = _p("x", "WR", "BUF")
    player.game_id = "BUF@KC"
    assert player.game_key() == "BUF@KC"


def test_slot_assignment_matches_position_eligibility():
    for extra in ("RB", "WR", "TE"):
        slotted = _nine(extra).slotted()
        assert [slot for slot, _ in slotted] == list(Lineup.SLOTS)
        for slot, player in slotted:
            if slot == "FLEX":
                assert player.position in ("RB", "WR", "TE")
            elif slot == "DEF":
                assert player.position == "DST"
            else:
                assert player.position == slot


def test_flex_takes_the_lowest_projected_of_the_surplus_position():
    lineup = _nine("RB")
    flex = dict(lineup.slotted())["FLEX"]
    assert flex.player_id == "flex"  # the 5.0-point RB, not a 10.0-point one


def test_overlap_counts_shared_players():
    a = _nine()
    b = _nine()
    assert a.overlap(b) == 9
    b.players[0] = _p("other_qb", "QB", "Z")
    assert a.overlap(b) == 8


def test_team_counts_and_salary_remaining():
    lineup = _nine()
    assert lineup.team_counts["A"] == 1
    assert lineup.salary_remaining == 60_000 - lineup.salary
