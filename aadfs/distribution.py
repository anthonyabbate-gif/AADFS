"""How a player's weekly score is modelled.

A single lognormal is the obvious choice -- non-negative and right-skewed, which
matches fantasy scoring's upside. But backtesting against real results showed it
gets the *downside* wrong: far more real outcomes land below its floor than the
model predicts, because a lognormal's left tail is thin while football's is fat.
Players get hurt on the first drive, see two targets in a blowout, or lose a
backfield split by halftime. None of that looks like a mild under-performance;
it looks like a near-zero.

So a player's score is modelled as a two-component mixture:

* with probability `bust_rate`, a **bust** -- a small fraction of their
  projection, standing in for the games that fall apart early;
* otherwise a lognormal centred so that the *mixture's* mean still equals the
  projection and its standard deviation still equals the supplied one.

For cash games this matters more than anything else in the model: the floor is
the number the whole format turns on, and an optimistic floor makes risky
lineups look safe.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_NORMAL = NormalDist()

#: A D/ST can score below zero, so its distribution is shifted down before
#: being treated as lognormal. Offensive players are floored at zero.
POSITION_SHIFT = {"QB": 0.0, "RB": 0.0, "WR": 0.0, "TE": 0.0, "DST": 6.0}

#: Chance of a bust week by position. Pass catchers are the most volatile --
#: their production depends on targets they do not control -- while a starting
#: quarterback nearly always accumulates something.
BUST_RATE = {"QB": 0.05, "RB": 0.10, "WR": 0.13, "TE": 0.13, "DST": 0.08}
DEFAULT_BUST_RATE = 0.10

#: What a bust week is worth, as a fraction of the projection.
BUST_FRACTION = 0.25
#: Spread within the bust component itself, relative to its own mean.
BUST_RELATIVE_SD = 0.6

#: Percentiles reported as "floor" and "ceiling".
FLOOR_PERCENTILE = 0.20
CEILING_PERCENTILE = 0.85

_MIN_MEAN = 0.05
_MIN_SD = 0.05


def bust_rate_for(position: str | None) -> float:
    return BUST_RATE.get(position or "", DEFAULT_BUST_RATE)


def _lognormal_params(mean: float, sd: float) -> tuple[float, float]:
    """Convert a target mean and sd into lognormal mu and sigma."""
    mean = max(mean, _MIN_MEAN)
    sd = max(sd, _MIN_SD)
    variance = sd * sd
    sigma_sq = math.log(1.0 + variance / (mean * mean))
    sigma = math.sqrt(sigma_sq)
    mu = math.log(mean) - sigma_sq / 2.0
    return mu, sigma


def mixture_components(
    mean: float, sd: float, position: str | None
) -> tuple[float, tuple[float, float], tuple[float, float]]:
    """Solve for a bust/normal mixture with the requested mean and sd.

    Returns `(bust_rate, (bust_mu, bust_sigma), (main_mu, main_sigma))`, all in
    shifted space. Falls back to a single lognormal (bust rate 0) whenever the
    requested spread is too tight for a mixture to represent.
    """
    shift = POSITION_SHIFT.get(position or "", 0.0)
    target_mean = max(mean + shift, _MIN_MEAN)
    target_sd = max(sd, _MIN_SD)
    q = bust_rate_for(position)

    bust_mean = max(BUST_FRACTION * target_mean, _MIN_MEAN)
    bust_sd = max(BUST_RELATIVE_SD * bust_mean, _MIN_SD)

    # Keep the mixture mean equal to the projection.
    main_mean = (target_mean - q * bust_mean) / (1.0 - q)
    if main_mean <= bust_mean:
        return 0.0, (0.0, 0.0), _lognormal_params(target_mean, target_sd)

    # Split the total variance: the gap between the two components already
    # supplies some of it, and the main component covers the remainder.
    grand_mean = q * bust_mean + (1 - q) * main_mean
    between = (
        q * (bust_mean - grand_mean) ** 2 + (1 - q) * (main_mean - grand_mean) ** 2
    )
    within_bust = q * bust_sd**2
    remaining = target_sd**2 - between - within_bust
    if remaining <= 0:
        # The requested spread is narrower than the mixture's own structure.
        return 0.0, (0.0, 0.0), _lognormal_params(target_mean, target_sd)

    main_sd = math.sqrt(remaining / (1 - q))
    return q, _lognormal_params(bust_mean, bust_sd), _lognormal_params(main_mean, main_sd)


def _cdf_from_components(x: float, components) -> float:
    """P(score <= x) under an already-solved mixture, in shifted space."""
    if x <= 0:
        return 0.0
    q, (bust_mu, bust_sigma), (main_mu, main_sigma) = components
    log_x = math.log(x)
    main = _NORMAL.cdf((log_x - main_mu) / main_sigma)
    if q <= 0:
        return main
    bust = _NORMAL.cdf((log_x - bust_mu) / bust_sigma)
    return q * bust + (1 - q) * main


def mixture_cdf(x: float, mean: float, sd: float, position: str | None = None) -> float:
    """P(score <= x) for a player, in unshifted (real) points."""
    shift = POSITION_SHIFT.get(position or "", 0.0)
    return _cdf_from_components(x + shift, mixture_components(mean, sd, position))


def quantile(mean: float, sd: float, p: float, position: str | None = None) -> float:
    """The p-th quantile of a player's projected score.

    The mixture has no closed-form inverse, so this bisects the CDF. It runs in
    a few dozen iterations and is only ever called once per player.
    """
    shift = POSITION_SHIFT.get(position or "", 0.0)
    components = mixture_components(mean, sd, position)
    q, _, (main_mu, main_sigma) = components
    if q <= 0:
        value = math.exp(main_mu + main_sigma * _NORMAL.inv_cdf(p))
        return round(value - shift, 2)

    # No closed-form inverse for a mixture, so bracket and bisect. The mixture
    # is solved once up front rather than on every iteration.
    low, high = 1e-6, max(math.exp(main_mu + 6 * main_sigma), 1.0)
    for _ in range(60):
        if high - low < 1e-4:
            break
        mid = (low + high) / 2.0
        if _cdf_from_components(mid, components) < p:
            low = mid
        else:
            high = mid
    return round((low + high) / 2.0 - shift, 2)


def floor_ceiling(mean: float, sd: float, position: str | None = None) -> tuple[float, float]:
    """The (floor, ceiling) pair displayed alongside a projection."""
    return (
        quantile(mean, sd, FLOOR_PERCENTILE, position),
        quantile(mean, sd, CEILING_PERCENTILE, position),
    )


def sample(rng, mean: float, sd: float, position: str | None, size: int):
    """Draw `size` simulated scores for one player."""
    shift = POSITION_SHIFT.get(position or "", 0.0)
    q, (bust_mu, bust_sigma), (main_mu, main_sigma) = mixture_components(mean, sd, position)
    main = rng.lognormal(mean=main_mu, sigma=main_sigma, size=size)
    if q <= 0:
        return main - shift
    bust = rng.lognormal(mean=bust_mu, sigma=bust_sigma, size=size)
    is_bust = rng.random(size) < q
    return (bust * is_bust + main * (1 - is_bust)) - shift
