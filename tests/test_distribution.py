import pytest

from aadfs.distribution import floor_ceiling, quantile


@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE", "DST"])
def test_quantiles_increase_monotonically(position):
    values = [quantile(12.0, 5.0, p, position) for p in (0.05, 0.2, 0.5, 0.8, 0.95)]
    assert values == sorted(values)


def test_floor_below_ceiling_and_both_near_the_mean():
    low, high = floor_ceiling(15.0, 6.0, "WR")
    assert low < 15.0 < high


def test_offensive_players_cannot_project_negative():
    assert quantile(8.0, 6.0, 0.001, "WR") >= 0.0


def test_defense_can_go_negative():
    # A D/ST is shifted so the model allows the negative scores FanDuel awards.
    assert quantile(6.0, 5.0, 0.01, "DST") < 0.0


def test_more_volatility_means_a_lower_floor_and_higher_ceiling():
    tight_low, tight_high = floor_ceiling(12.0, 3.0, "RB")
    wide_low, wide_high = floor_ceiling(12.0, 8.0, "RB")
    assert wide_low < tight_low
    assert wide_high > tight_high


def test_zero_and_tiny_inputs_do_not_blow_up():
    assert quantile(0.0, 0.0, 0.5, "WR") >= 0.0
    assert floor_ceiling(0.01, 0.01, "TE")[0] >= 0.0


# --- bust mixture ------------------------------------------------------------

def test_mixture_preserves_the_requested_mean_and_spread():
    import numpy as np

    from aadfs.distribution import sample

    rng = np.random.default_rng(7)
    for position, mean, sd in [("QB", 20, 7), ("RB", 12, 6), ("WR", 10, 6.5),
                               ("TE", 7, 4.5), ("DST", 7, 4)]:
        draws = sample(rng, mean, sd, position, 200_000)
        assert abs(draws.mean() - mean) < 0.1
        assert abs(draws.std() - sd) < 0.1


def test_quantiles_are_the_actual_quantiles():
    from aadfs.distribution import CEILING_PERCENTILE, FLOOR_PERCENTILE, mixture_cdf

    low, high = floor_ceiling(12.0, 6.0, "WR")
    assert abs(mixture_cdf(low, 12.0, 6.0, "WR") - FLOOR_PERCENTILE) < 0.01
    assert abs(mixture_cdf(high, 12.0, 6.0, "WR") - CEILING_PERCENTILE) < 0.01


def test_mixture_has_a_fatter_left_tail_than_a_plain_lognormal():
    import math
    from statistics import NormalDist

    from aadfs.distribution import _lognormal_params, mixture_cdf

    mean, sd = 12.0, 6.0
    mu, sigma = _lognormal_params(mean, sd)
    threshold = 3.0  # a genuine bust week

    plain = NormalDist().cdf((math.log(threshold) - mu) / sigma)
    mixed = mixture_cdf(threshold, mean, sd, "WR")
    assert mixed > plain


def test_pass_catchers_bust_more_often_than_quarterbacks():
    from aadfs.distribution import bust_rate_for

    assert bust_rate_for("WR") > bust_rate_for("QB")
    assert bust_rate_for(None) > 0


def test_very_tight_spreads_fall_back_to_a_single_lognormal():
    from aadfs.distribution import mixture_components

    rate, _, _ = mixture_components(12.0, 0.2, "WR")
    assert rate == 0.0  # a mixture cannot be that narrow; fall back gracefully
