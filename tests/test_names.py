from aadfs.models import Player
from aadfs.names import (
    PlayerMatcher, dst_key, normalize_name, normalize_position, normalize_team,
)


def test_suffixes_and_punctuation_collapse():
    assert normalize_name("Marvin Harrison Jr.") == normalize_name("Marvin Harrison")
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_accents_are_stripped():
    assert normalize_name("Cristian Peña") == "cristian pena"


def test_team_aliases_normalise_to_fanduel_codes():
    assert normalize_team("JAX") == "JAC"
    assert normalize_team("WSH") == "WAS"
    assert normalize_team("Green Bay Packers") == "GB"
    assert normalize_team("gnb") == "GB"
    assert normalize_team("OAK") == "LV"
    assert normalize_team("") is None


def test_position_aliases():
    assert normalize_position("D") == "DST"
    assert normalize_position("DEF") == "DST"
    assert normalize_position("fb") == "RB"


def _player(pid, name, pos, team, salary=5000):
    return Player(player_id=pid, name=name, position=pos, team=team, salary=salary)


def test_matcher_exact_and_fuzzy():
    matcher = PlayerMatcher([
        _player("1", "Ja'Marr Chase", "WR", "CIN"),
        _player("2", "Marvin Harrison Jr.", "WR", "ARI"),
        _player("3", "SF", "DST", "SF"),
    ])
    assert matcher.match("Ja'Marr Chase", "CIN", "WR").target_id == "1"
    # Different spelling, no team given
    assert matcher.match("JaMarr Chase", None, "WR").target_id == "1"
    assert matcher.match("Marvin Harrison", "ARI", "WR").target_id == "2"


def test_matcher_resolves_dst_by_team_or_name():
    matcher = PlayerMatcher([_player("3", "San Francisco 49ers", "DST", "SF")])
    assert matcher.match("49ers D/ST", "SF", "DST").target_id == "3"
    assert matcher.match("San Francisco 49ers", None, "DEF").target_id == "3"


def test_matcher_refuses_ambiguous_duplicate_names():
    matcher = PlayerMatcher([
        _player("1", "Mike Williams", "WR", "NYJ"),
        _player("2", "Mike Williams", "WR", "LAC"),
    ])
    # Without a team there is no safe answer, so it must refuse.
    assert matcher.match("Mike Williams", None, "WR").target_id is None
    # With a team it resolves.
    assert matcher.match("Mike Williams", "LAC", "WR").target_id == "2"


def test_matcher_rejects_unrelated_names():
    matcher = PlayerMatcher([_player("1", "Josh Allen", "QB", "BUF")])
    result = matcher.match("Christian McCaffrey", "SF", "RB")
    assert result.target_id is None
    assert result.method == "unmatched"


def test_dst_key_uses_team_only():
    assert dst_key("JAX") == dst_key("JAC") == "DST|JAC"
