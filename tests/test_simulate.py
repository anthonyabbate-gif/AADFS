import numpy as np

from aadfs.models import Player
from aadfs.optimizer import OptimizerConfig, optimize
from aadfs.simulate import (
    ContestSettings, build_correlation_matrix, build_field, evaluate_lineup, simulate_scores,
)


def test_breakeven_rate_follows_the_payout():
    assert ContestSettings(payout_multiple=2.0).breakeven_win_rate == 0.5
    assert round(ContestSettings(payout_multiple=1.8).breakeven_win_rate, 4) == 0.5556


def _p(pid, pos, team, opponent, game):
    player = Player(player_id=pid, name=pid, position=pos, team=team,
                    opponent=opponent, salary=6000, projection=10.0, stdev=4.0)
    player.game_id = game
    return player


def test_correlation_signs_match_football_logic():
    qb = _p("qb", "QB", "BUF", "KC", "BUF@KC")
    wr = _p("wr", "WR", "BUF", "KC", "BUF@KC")
    wr2 = _p("wr2", "WR", "BUF", "KC", "BUF@KC")
    opp_wr = _p("owr", "WR", "KC", "BUF", "BUF@KC")
    dst = _p("dst", "DST", "BUF", "KC", "BUF@KC")
    elsewhere = _p("far", "WR", "SF", "SEA", "SF@SEA")

    matrix = build_correlation_matrix([qb, wr, wr2, opp_wr, dst, elsewhere])
    assert matrix[0, 1] > 0      # QB with his own receiver
    assert matrix[1, 2] < 0      # two receivers competing for targets
    assert matrix[0, 3] > 0      # shootout across the same game
    assert matrix[4, 3] < 0      # defense against the offense it faces
    assert matrix[0, 5] == 0.0   # unrelated game


def test_correlation_matrix_is_positive_definite():
    players = [
        _p(f"p{i}", pos, team, "OPP", "G")
        for i, (pos, team) in enumerate(
            [("QB", "A"), ("WR", "A"), ("WR", "A"), ("TE", "A"), ("RB", "A"), ("DST", "B")]
        )
    ]
    matrix = build_correlation_matrix(players)
    assert np.all(np.linalg.eigvalsh(matrix) > 0)
    np.testing.assert_allclose(np.diag(matrix), 1.0, atol=1e-9)


def test_simulated_scores_track_the_requested_mean(projected_slate):
    players = [p for p in projected_slate.players if p.projection > 5][:30]
    scores = simulate_scores(players, 20_000, seed=5)
    assert scores.shape == (30, 20_000)
    for i, player in enumerate(players):
        assert abs(scores[i].mean() - player.projection) < 0.6 * player.stdev


def test_field_lineups_are_legal(projected_slate):
    settings = ContestSettings(field_size=120)
    field = build_field(projected_slate, settings, seed=3)
    assert len(field) >= 100
    pool = [p for p in projected_slate.playable() if p.projection > 0]
    rules = projected_slate.rules
    for indices in field:
        players = [pool[i] for i in indices]
        assert len(set(indices)) == rules.roster_size
        assert sum(p.salary for p in players) <= rules.salary_cap
        counts = {}
        for p in players:
            counts[p.position] = counts.get(p.position, 0) + 1
        assert counts.get("QB") == 1 and counts.get("DST") == 1
        assert counts.get("RB", 0) + counts.get("WR", 0) + counts.get("TE", 0) == 7
        assert max(
            sum(1 for p in players if p.team == t) for t in {p.team for p in players}
        ) <= rules.max_per_team


def test_field_is_varied(projected_slate):
    field = build_field(projected_slate, ContestSettings(field_size=200), seed=8)
    assert len({tuple(sorted(lu)) for lu in field}) == len(field)
    used = {i for lu in field for i in lu}
    assert len(used) > 30  # not everybody playing the same nine guys


def test_evaluate_lineup_returns_a_sane_probability(projected_slate):
    lineup = optimize(projected_slate, OptimizerConfig()).lineups[0]
    result = evaluate_lineup(lineup, projected_slate, ContestSettings(field_size=150),
                             n_sims=4000)
    data = result.as_dict()
    assert 0.0 <= data["win_probability"] <= 1.0
    assert data["p10_score"] < data["median_score"] < data["p90_score"]
    assert data["cash_line"] > 0
    assert data["beats_breakeven"] == (
        data["win_probability"] > data["breakeven_win_rate"]
    )


def test_expected_roi_is_consistent_with_win_probability(projected_slate):
    lineup = optimize(projected_slate, OptimizerConfig()).lineups[0]
    settings = ContestSettings(field_size=120, payout_multiple=1.8)
    result = evaluate_lineup(lineup, projected_slate, settings, n_sims=3000)
    expected = result.win_probability * 1.8 - 1.0
    assert abs(result.expected_roi - expected) < 1e-9


def test_simulation_is_reproducible(projected_slate):
    lineup = optimize(projected_slate, OptimizerConfig()).lineups[0]
    settings = ContestSettings(field_size=100)
    a = evaluate_lineup(lineup, projected_slate, settings, n_sims=2000, seed=42)
    b = evaluate_lineup(lineup, projected_slate, settings, n_sims=2000, seed=42)
    assert a.win_probability == b.win_probability


def test_busts_are_correlated_between_a_qb_and_his_receiver():
    import numpy as np

    qb = _p("qb", "QB", "BUF", "KC", "BUF@KC")
    own_wr = _p("own", "WR", "BUF", "KC", "BUF@KC")
    other_wr = _p("other", "WR", "SF", "SEA", "SF@SEA")
    for player in (qb, own_wr, other_wr):
        player.projection, player.stdev = 14.0, 7.0

    scores = simulate_scores([qb, own_wr, other_wr], 60_000, seed=9)
    qb_tanked = scores[0] < np.percentile(scores[0], 10)
    own_also = np.mean(scores[1][qb_tanked] < np.percentile(scores[1], 10))
    other_also = np.mean(scores[2][qb_tanked] < np.percentile(scores[2], 10))
    # A teammate's downside must travel with the quarterback's.
    assert own_also > other_also


def test_simulated_marginals_keep_their_volatility(projected_slate):
    players = [p for p in projected_slate.players if p.projection > 8][:20]
    scores = simulate_scores(players, 40_000, seed=6)
    for i, player in enumerate(players):
        assert abs(scores[i].std() - player.stdev) < 0.35 * player.stdev
