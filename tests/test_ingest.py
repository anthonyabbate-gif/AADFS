import io

import pytest

from aadfs.ingest.fanduel import (
    ENTRY_SLOTS, lineups_to_entry_csv, lineups_to_readable_csv, parse_salary_csv,
)
from aadfs.models import Lineup


def test_parses_fanduel_export(slate):
    assert len(slate.players) == 192
    assert {p.position for p in slate.players} == {"QB", "RB", "WR", "TE", "DST"}
    assert len(slate.games) == 6
    assert all(p.salary > 0 for p in slate.players)


def test_game_and_opponent_are_populated(slate):
    player = next(p for p in slate.players if p.team == "BUF")
    assert player.opponent == "KC"
    assert player.game_id == "BUF@KC"
    assert player.game_key() == "BUF@KC"


def test_defense_rows_become_dst(slate):
    defenses = [p for p in slate.players if p.position == "DST"]
    assert len(defenses) == 12
    assert all(d.name == d.team for d in defenses)


def test_injury_flags_are_read(slate):
    assert any(p.is_out for p in slate.players)
    assert any(p.is_questionable for p in slate.players)


def test_salary_with_currency_formatting_parses():
    text = (
        "Id,Position,Nickname,Salary,Team,Opponent,Game,FPPG\n"
        "a1,QB,Test Guy,\"$8,500\",BUF,KC,BUF@KC,19.5\n"
    )
    parsed = parse_salary_csv(io.StringIO(text))
    assert parsed.players[0].salary == 8500
    assert parsed.players[0].fd_projection == 19.5


def test_missing_required_column_raises_with_a_useful_message():
    with pytest.raises(ValueError, match="missing required column"):
        parse_salary_csv(io.StringIO("Id,Nickname,Team\na1,Test Guy,BUF\n"))


def test_empty_file_raises():
    with pytest.raises(ValueError):
        parse_salary_csv(io.StringIO(""))


def _lineup(slate):
    picks = []
    for position, count in (("QB", 1), ("RB", 3), ("WR", 3), ("TE", 1), ("DST", 1)):
        picks += [p for p in slate.players if p.position == position][:count]
    return Lineup(players=picks, rules=slate.rules, label="L1")


def test_entry_csv_has_fanduel_slot_header(slate):
    text = lineups_to_entry_csv([_lineup(slate)])
    header = text.splitlines()[0].split(",")
    assert header == ["entry_id"] + ENTRY_SLOTS
    assert len(text.splitlines()[1].split(",")) == len(ENTRY_SLOTS) + 1


def test_entry_csv_uses_fanduel_player_ids(slate):
    lineup = _lineup(slate)
    row = lineups_to_entry_csv([lineup]).splitlines()[1].split(",")[1:]
    assert set(row) == {p.player_id for p in lineup.players}


def test_readable_csv_includes_a_total_row(slate):
    text = lineups_to_readable_csv([_lineup(slate)])
    assert "TOTAL" in text
    assert len(text.splitlines()) == 1 + 9 + 1  # header + roster + total
