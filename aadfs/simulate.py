"""Monte Carlo evaluation of a lineup against a simulated contest field.

Projection totals rank lineups; they do not tell you whether a lineup wins a
50/50. That depends on the distribution of your score *relative to the field*,
and on the correlations that make lineups move together — a QB and his top
receiver are not two independent bets.

So we simulate:

1. Draw correlated weekly scores for every player on the slate, using a Gaussian
   copula over each player's shifted-lognormal marginal.
2. Build a plausible field of opponent lineups.
3. Score your lineup and the field on the *same* draws and count how often you
   finish above the cash line.

The output is a win probability and an expected ROI, which is the number that
actually decides whether a cash lineup is worth entering.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from statistics import NormalDist

import numpy as np

from aadfs.distribution import POSITION_SHIFT, bust_rate_for, mixture_components
from aadfs.models import Lineup, Player, Slate

_NORMAL = NormalDist()

# --- correlation assumptions -------------------------------------------------
# These are deliberately modest. Overstating correlation makes stacks look
# safer than they are, which is precisely the wrong error for cash games.

CORR_QB_PASS_CATCHER = 0.35    # QB with his own WR/TE
CORR_QB_RB = 0.05              # QB with his own RB
CORR_SAME_TEAM_CATCHERS = -0.10  # two receivers competing for the same targets
CORR_SAME_TEAM_OTHER = 0.05
CORR_DST_VS_OPP_OFFENSE = -0.30  # your defense against the offense it faces
CORR_OPPOSING_OFFENSE = 0.15     # shootout effect across a game
CORR_DST_SAME_TEAM_OFFENSE = 0.05

_PASS_CATCHERS = {"WR", "TE"}


@dataclass
class ContestSettings:
    """The shape of the contest being evaluated."""

    #: Fraction of the field that gets paid. A 50/50 pays the top half.
    cash_fraction: float = 0.50
    #: What a winning entry returns, as a multiple of the entry fee. FanDuel
    #: 50/50s and double-ups typically land near 1.8x after rake.
    payout_multiple: float = 1.8
    #: How many opponent lineups to simulate.
    field_size: int = 200
    #: How sharp the simulated field is. Higher means opponents concentrate
    #: harder on the highest-projected players.
    field_sharpness: float = 1.5

    @property
    def breakeven_win_rate(self) -> float:
        """The win rate needed to break even at this payout."""
        return 1.0 / self.payout_multiple if self.payout_multiple > 0 else 1.0


@dataclass
class SimulationResult:
    """What the simulator concluded about one lineup."""

    win_probability: float = 0.0
    expected_roi: float = 0.0
    mean_score: float = 0.0
    median_score: float = 0.0
    p10_score: float = 0.0
    p90_score: float = 0.0
    cash_line: float = 0.0
    breakeven_win_rate: float = 0.0
    n_sims: int = 0
    field_size: int = 0

    @property
    def beats_breakeven(self) -> bool:
        return self.win_probability > self.breakeven_win_rate

    def as_dict(self) -> dict:
        return {
            "win_probability": round(self.win_probability, 4),
            "expected_roi": round(self.expected_roi, 4),
            "mean_score": round(self.mean_score, 2),
            "median_score": round(self.median_score, 2),
            "p10_score": round(self.p10_score, 2),
            "p90_score": round(self.p90_score, 2),
            "cash_line": round(self.cash_line, 2),
            "breakeven_win_rate": round(self.breakeven_win_rate, 4),
            "beats_breakeven": self.beats_breakeven,
            "n_sims": self.n_sims,
            "field_size": self.field_size,
        }


def _pair_correlation(a: Player, b: Player) -> float:
    """Assumed correlation between two players' weekly scores."""
    same_team = bool(a.team) and a.team == b.team
    same_game = a.game_key() == b.game_key()

    if same_team:
        positions = {a.position, b.position}
        if "DST" in positions:
            return CORR_DST_SAME_TEAM_OFFENSE
        if "QB" in positions:
            other = (positions - {"QB"}).pop() if len(positions) > 1 else "QB"
            if other in _PASS_CATCHERS:
                return CORR_QB_PASS_CATCHER
            if other == "RB":
                return CORR_QB_RB
            return CORR_SAME_TEAM_OTHER
        if a.position in _PASS_CATCHERS and b.position in _PASS_CATCHERS:
            return CORR_SAME_TEAM_CATCHERS
        return CORR_SAME_TEAM_OTHER

    if same_game:
        if a.position == "DST" or b.position == "DST":
            return CORR_DST_VS_OPP_OFFENSE
        return CORR_OPPOSING_OFFENSE

    return 0.0


def build_correlation_matrix(players: list[Player]) -> np.ndarray:
    """A positive-definite correlation matrix over the given players."""
    n = len(players)
    matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho = _pair_correlation(players[i], players[j])
            matrix[i, j] = matrix[j, i] = rho

    # Clip to the nearest positive-definite matrix; the hand-set correlations
    # above are not guaranteed to be internally consistent.
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    if eigenvalues.min() < 1e-6:
        eigenvalues = np.clip(eigenvalues, 1e-6, None)
        matrix = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
        scale = np.sqrt(np.diag(matrix))
        matrix = matrix / np.outer(scale, scale)
        np.fill_diagonal(matrix, 1.0)
    return matrix


def simulate_scores(players: list[Player], n_sims: int, seed: int | None = 11) -> np.ndarray:
    """Draw correlated weekly scores. Returns an (n_players, n_sims) array.

    Two correlated normal fields are drawn through the same Cholesky factor:
    one drives each player's scoring magnitude, the other decides which players
    bust. Correlating the bust field matters -- when a quarterback's day falls
    apart, his receivers' days fall apart with it, and a model that busts
    players independently understates exactly the joint downside a cash lineup
    is trying to avoid.
    """
    rng = np.random.default_rng(seed)
    n = len(players)
    if n == 0:
        return np.zeros((0, n_sims))

    correlation = build_correlation_matrix(players)
    try:
        cholesky = np.linalg.cholesky(correlation)
    except np.linalg.LinAlgError:
        cholesky = np.eye(n)

    magnitude = cholesky @ rng.standard_normal((n, n_sims))
    bust_field = cholesky @ rng.standard_normal((n, n_sims))

    scores = np.empty((n, n_sims))
    for i, player in enumerate(players):
        shift = POSITION_SHIFT.get(player.position, 0.0)
        rate, (bust_mu, bust_sigma), (main_mu, main_sigma) = mixture_components(
            max(player.projection, 0.05), max(player.stdev, 0.05), player.position
        )
        main = np.exp(main_mu + main_sigma * magnitude[i])
        if rate <= 0:
            scores[i] = main - shift
            continue
        # A player busts when their correlated bust draw falls in the lowest
        # `rate` of the standard normal, which reproduces the mixture weight
        # exactly while keeping busts correlated between teammates.
        threshold = _NORMAL.inv_cdf(rate)
        busted = bust_field[i] < threshold
        bust = np.exp(bust_mu + bust_sigma * magnitude[i])
        scores[i] = np.where(busted, bust, main) - shift
    return scores


def _slot_plan(rules) -> list[tuple[str, ...]]:
    """The roster slots to fill, each with the positions eligible for it."""
    plan: list[tuple[str, ...]] = []
    for position, (low, _) in rules.slots.items():
        plan.extend([(position,)] * low)
    plan.append(tuple(rules.flex_positions()))  # the FLEX slot
    return plan


def build_field(
    slate: Slate,
    settings: ContestSettings,
    seed: int | None = 23,
) -> list[list[int]]:
    """Generate plausible opponent lineups as lists of player-pool indices.

    Lineups are built one slot at a time under a running salary budget: before
    each pick, candidates are filtered to those that still leave enough money to
    fill the remaining slots, and still allow the lineup to reach a realistic
    salary floor. That produces cap-compliant lineups directly, which both runs
    far faster than sampling-and-rejecting and gives a much more varied field
    than repairing a bad sample greedily.

    Opponents are weighted toward value-efficient players, with the slot order
    shuffled per lineup so the field is not all built the same way.
    """
    rng = np.random.default_rng(seed)
    pool = [p for p in slate.playable() if p.projection > 0]
    if not pool:
        return []
    index = {p.player_id: i for i, p in enumerate(pool)}
    rules = slate.rules
    cap = rules.salary_cap
    floor_salary = int(cap * 0.95)

    by_position: dict[str, list[Player]] = {}
    for p in pool:
        by_position.setdefault(p.position, []).append(p)

    # Per-slot salary extremes, used to keep partial lineups completable.
    plan = _slot_plan(rules)
    slot_min, slot_max = [], []
    for slot in plan:
        salaries = [p.salary for pos in slot for p in by_position.get(pos, [])]
        if not salaries:
            return []
        slot_min.append(min(salaries))
        slot_max.append(max(salaries))

    # Selection weights: value-efficient players are picked more often.
    weight_cache: dict[tuple[str, ...], np.ndarray] = {}

    def candidates_for(slot: tuple[str, ...]) -> list[Player]:
        return [p for pos in slot for p in by_position.get(pos, [])]

    def weights_for(slot: tuple[str, ...]) -> np.ndarray:
        if slot not in weight_cache:
            group = candidates_for(slot)
            values = np.array([p.value for p in group], dtype=float)
            spread = values.std() or 1.0
            scaled = np.exp(settings.field_sharpness * (values - values.max()) / spread)
            total = scaled.sum()
            weight_cache[slot] = (
                scaled / total if total > 0 else np.ones(len(group)) / len(group)
            )
        return weight_cache[slot]

    lineups: list[list[int]] = []
    seen: set[tuple[str, ...]] = set()
    attempts = 0
    max_attempts = settings.field_size * 25

    while len(lineups) < settings.field_size and attempts < max_attempts:
        attempts += 1
        order = list(range(len(plan)))
        rng.shuffle(order)

        chosen: dict[int, Player] = {}
        taken: set[str] = set()
        spent = 0
        failed = False

        for position_in_order, slot_index in enumerate(order):
            slot = plan[slot_index]
            remaining = order[position_in_order + 1:]
            cheapest_rest = sum(slot_min[i] for i in remaining)
            dearest_rest = sum(slot_max[i] for i in remaining)

            # Keep the lineup both affordable and able to reach the floor.
            max_affordable = cap - spent - cheapest_rest
            min_required = floor_salary - spent - dearest_rest

            group = candidates_for(slot)
            base_weights = weights_for(slot)
            mask = np.array(
                [
                    (p.player_id not in taken)
                    and (p.salary <= max_affordable)
                    and (p.salary >= min_required)
                    for p in group
                ]
            )
            if not mask.any():
                failed = True
                break

            probabilities = base_weights * mask
            total = probabilities.sum()
            if total <= 0:
                probabilities = mask / mask.sum()
            else:
                probabilities = probabilities / total

            pick = group[rng.choice(len(group), p=probabilities)]
            chosen[slot_index] = pick
            taken.add(pick.player_id)
            spent += pick.salary

        if failed or len(chosen) != len(plan):
            continue

        players = [chosen[i] for i in range(len(plan))]
        counts: dict[str, int] = {}
        for p in players:
            counts[p.team] = counts.get(p.team, 0) + 1
        if counts and max(counts.values()) > rules.max_per_team:
            continue
        if len({p.game_key() for p in players}) < rules.min_games:
            continue

        key = tuple(sorted(p.player_id for p in players))
        if key in seen:
            continue
        seen.add(key)
        lineups.append([index[p.player_id] for p in players])

    return lineups


def evaluate_lineup(
    lineup: Lineup,
    slate: Slate,
    settings: ContestSettings | None = None,
    *,
    n_sims: int = 10_000,
    seed: int | None = 11,
    field: list[list[int]] | None = None,
) -> SimulationResult:
    """Estimate a lineup's win probability and ROI in the given contest."""
    settings = settings or ContestSettings()
    pool = [p for p in slate.playable() if p.projection > 0]
    if not pool:
        return SimulationResult(breakeven_win_rate=settings.breakeven_win_rate)

    index = {p.player_id: i for i, p in enumerate(pool)}
    missing = [p.name for p in lineup.players if p.player_id not in index]
    if missing:
        # A locked player with no projection cannot be simulated meaningfully.
        return SimulationResult(breakeven_win_rate=settings.breakeven_win_rate)

    scores = simulate_scores(pool, n_sims, seed=seed)
    mine = scores[[index[p.player_id] for p in lineup.players], :].sum(axis=0)

    if field is None:
        field = build_field(slate, settings, seed=(seed or 0) + 101)
    if not field:
        return SimulationResult(
            win_probability=0.0,
            mean_score=float(mine.mean()),
            median_score=float(np.median(mine)),
            p10_score=float(np.percentile(mine, 10)),
            p90_score=float(np.percentile(mine, 90)),
            breakeven_win_rate=settings.breakeven_win_rate,
            n_sims=n_sims,
        )

    field_scores = np.stack([scores[idx, :].sum(axis=0) for idx in field])  # (field, sims)

    # The cash line is the score at the payout cutoff within each simulation.
    cutoff = np.quantile(field_scores, 1.0 - settings.cash_fraction, axis=0)
    wins = mine > cutoff
    win_probability = float(wins.mean())

    return SimulationResult(
        win_probability=win_probability,
        expected_roi=win_probability * settings.payout_multiple - 1.0,
        mean_score=float(mine.mean()),
        median_score=float(np.median(mine)),
        p10_score=float(np.percentile(mine, 10)),
        p90_score=float(np.percentile(mine, 90)),
        cash_line=float(cutoff.mean()),
        breakeven_win_rate=settings.breakeven_win_rate,
        n_sims=n_sims,
        field_size=len(field),
    )
