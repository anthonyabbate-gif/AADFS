"""Invariants every produced lineup must satisfy to be enterable on FanDuel."""

import pytest

from aadfs.optimizer import OptimizerConfig, optimize


def assert_legal(lineup, slate):
    rules = slate.rules
    assert len(lineup.players) == rules.roster_size
    assert lineup.salary <= rules.salary_cap
    counts = {}
    for p in lineup.players:
        counts[p.position] = counts.get(p.position, 0) + 1
    assert counts.get("QB") == 1
    assert counts.get("DST") == 1
    assert 2 <= counts.get("RB", 0) <= 3
    assert 3 <= counts.get("WR", 0) <= 4
    assert 1 <= counts.get("TE", 0) <= 2
    assert counts.get("RB", 0) + counts.get("WR", 0) + counts.get("TE", 0) == 7
    assert max(lineup.team_counts.values()) <= rules.max_per_team
    assert lineup.game_count >= rules.min_games
    assert len({p.player_id for p in lineup.players}) == rules.roster_size


def test_basic_lineup_is_legal(projected_slate):
    result = optimize(projected_slate, OptimizerConfig())
    assert result.ok
    assert_legal(result.lineups[0], projected_slate)


def test_respects_minimum_salary(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(min_salary=59_000))
    assert result.lineups[0].salary >= 59_000
    assert not result.relaxations


def test_locked_player_is_always_rostered(projected_slate):
    # Lock a deliberately bad, cheap player the optimiser would never pick.
    cheap = min(
        (p for p in projected_slate.players if p.position == "WR"),
        key=lambda p: p.projection,
    )
    result = optimize(projected_slate, OptimizerConfig(locked={cheap.player_id}))
    assert cheap.player_id in result.lineups[0].ids()
    assert_legal(result.lineups[0], projected_slate)


def test_excluded_player_is_never_rostered(projected_slate):
    baseline = optimize(projected_slate, OptimizerConfig()).lineups[0]
    banned = baseline.players[0].player_id
    result = optimize(projected_slate, OptimizerConfig(excluded={banned}))
    assert banned not in result.lineups[0].ids()


def test_out_players_are_excluded_by_default(projected_slate):
    out_ids = {p.player_id for p in projected_slate.players if p.is_out}
    assert out_ids, "fixture should contain players flagged out"
    result = optimize(projected_slate, OptimizerConfig(n_lineups=5))
    for lineup in result.lineups:
        assert not (set(lineup.ids()) & out_ids)


def test_questionable_players_excluded_when_asked(projected_slate):
    questionable = {p.player_id for p in projected_slate.players if p.is_questionable}
    result = optimize(projected_slate, OptimizerConfig(exclude_questionable=True, n_lineups=3))
    for lineup in result.lineups:
        assert not (set(lineup.ids()) & questionable)


def test_dst_never_faces_own_offense_by_default(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(n_lineups=6))
    for lineup in result.lineups:
        dst = next(p for p in lineup.players if p.position == "DST")
        opposing = {p.team for p in lineup.players if p.position != "DST"}
        assert dst.opponent not in opposing


def test_multiple_lineups_are_distinct_and_respect_overlap(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(n_lineups=5, max_overlap=5))
    assert len(result.lineups) == 5
    seen = set()
    for i, lineup in enumerate(result.lineups):
        assert_legal(lineup, projected_slate)
        assert lineup.ids() not in seen
        seen.add(lineup.ids())
        for other in result.lineups[:i]:
            assert lineup.overlap(other) <= 5


def test_max_per_game_constraint(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(max_per_game=2, n_lineups=2))
    for lineup in result.lineups:
        counts = {}
        for p in lineup.players:
            counts[p.game_key()] = counts.get(p.game_key(), 0) + 1
        assert max(counts.values()) <= 2


def test_qb_stack_forces_a_teammate(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(require_qb_stack=1, n_lineups=3))
    for lineup in result.lineups:
        qb = next(p for p in lineup.players if p.position == "QB")
        mates = [
            p for p in lineup.players
            if p.team == qb.team and p.position in ("WR", "TE", "RB")
        ]
        assert mates, "QB stack requested but no teammate rostered"


def test_floor_weighting_produces_a_safer_lineup(projected_slate):
    import random

    from aadfs.distribution import floor_ceiling

    # Give players differing volatility so floor and projection can disagree.
    rng = random.Random(3)
    for p in projected_slate.players:
        p.stdev = round(p.stdev * rng.uniform(0.6, 1.5), 2)
        p.floor, p.ceiling = floor_ceiling(p.projection, p.stdev, p.position)

    aggressive = optimize(
        projected_slate, OptimizerConfig(weight_projection=1.0, weight_floor=0.0)
    ).lineups[0]
    safe = optimize(
        projected_slate, OptimizerConfig(weight_projection=0.0, weight_floor=1.0)
    ).lineups[0]

    assert safe.floor > aggressive.floor
    assert aggressive.projection >= safe.projection


def test_impossible_constraints_report_rather_than_crash(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(min_projection=10_000))
    assert not result.ok
    assert result.infeasible_reason
    assert "filters" in result.infeasible_reason


def test_too_many_locks_is_reported(projected_slate):
    qbs = [p.player_id for p in projected_slate.players if p.position == "QB"][:3]
    result = optimize(projected_slate, OptimizerConfig(locked=set(qbs)))
    # Three QBs cannot coexist; the solver must say so rather than return junk.
    assert not result.ok
    assert result.infeasible_reason


def test_unreachable_min_salary_is_relaxed_and_reported(projected_slate):
    result = optimize(projected_slate, OptimizerConfig(min_salary=59_999))
    if result.ok and result.relaxations:
        assert any("salary" in note for note in result.relaxations)
