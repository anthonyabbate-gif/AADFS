"""Mixed-integer lineup optimisation for FanDuel NFL Classic.

The solver maximises a configurable objective over the legal roster space. For
cash games that objective leans on floor rather than raw projection, but the
mechanics are the same either way, so the weights are all exposed rather than
baked in.

If a set of constraints turns out to be infeasible, the solver relaxes the
soft ones in a defined order and reports exactly what it gave up, rather than
returning nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from aadfs.models import Lineup, Player, Slate
from aadfs.scoring import RosterRules


@dataclass
class OptimizerConfig:
    """Everything that shapes which lineup comes back."""

    n_lineups: int = 1

    # Objective weights. They need not sum to 1; only their ratio matters.
    weight_projection: float = 0.55
    weight_floor: float = 0.45
    weight_ceiling: float = 0.0
    #: Penalty applied per point of standard deviation. Positive = risk-averse.
    risk_penalty: float = 0.0

    # Salary usage.
    min_salary: int = 58_200
    max_salary: int | None = None

    # Roster-shape controls.
    max_per_team: int | None = None          # defaults to the slate's rules
    min_games: int | None = None             # defaults to the slate's rules
    max_per_game: int | None = None

    # Cash-game safety rails.
    #: Never roster a D/ST that is playing against one of your own skill players.
    avoid_dst_vs_own_offense: bool = True
    #: Exclude anyone FanDuel flags as out/IR/suspended.
    exclude_out: bool = True
    #: Exclude questionable/doubtful players unless explicitly locked.
    exclude_questionable: bool = False
    #: Require every rostered player to clear this projection.
    min_projection: float = 0.0
    #: Require at least this many independent sources per rostered player.
    min_sources: int = 0

    # Correlation controls (mostly for non-cash use).
    require_qb_stack: int = 0        # teammates of the QB to force in
    allow_qb_vs_dst: bool = False    # roster a QB opposing your own D/ST

    # Manual overrides.
    locked: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)

    # Multi-lineup diversity.
    max_overlap: int = 7             # shared players allowed between lineups


@dataclass
class OptimizationResult:
    """Lineups plus an account of anything the solver had to give up."""

    lineups: list[Lineup] = field(default_factory=list)
    relaxations: list[str] = field(default_factory=list)
    infeasible_reason: str | None = None
    pool_size: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.lineups)


def _solver():
    """The CBC solver, under whichever name this PuLP version exposes it."""
    for name in ("PULP_CBC_CMD", "COIN_CMD"):
        factory = getattr(pulp, name, None)
        if factory is None:
            continue
        try:
            solver = factory(msg=False)
            if solver.available():
                return solver
        except Exception:
            continue
    return None


def _objective_value(player: Player, config: OptimizerConfig) -> float:
    return (
        config.weight_projection * player.projection
        + config.weight_floor * player.floor
        + config.weight_ceiling * player.ceiling
        - config.risk_penalty * player.stdev
    )


def _eligible_pool(slate: Slate, config: OptimizerConfig) -> list[Player]:
    """Players the solver is allowed to consider, after filters and overrides."""
    pool: list[Player] = []
    for player in slate.players:
        if player.player_id in config.excluded:
            continue
        if player.salary <= 0:
            continue
        locked = player.player_id in config.locked
        if not locked:
            if config.exclude_out and player.is_out:
                continue
            if config.exclude_questionable and player.is_questionable:
                continue
            if player.projection < config.min_projection:
                continue
            if config.min_sources and player.source_count < config.min_sources:
                continue
        pool.append(player)
    return pool


def _build_problem(
    pool: list[Player],
    slate: Slate,
    config: OptimizerConfig,
    rules: RosterRules,
    min_salary: int,
    banned: list[set[str]],
    drop: set[str],
):
    """Construct the MILP. `drop` names soft constraints to leave out."""
    problem = pulp.LpProblem("fanduel_nfl_lineup", pulp.LpMaximize)
    x = {p.player_id: pulp.LpVariable(f"x_{p.player_id}", cat="Binary") for p in pool}

    problem += pulp.lpSum(_objective_value(p, config) * x[p.player_id] for p in pool)

    # --- roster shape ---
    problem += pulp.lpSum(x.values()) == rules.roster_size, "roster_size"
    for position, (low, high) in rules.slots.items():
        members = [x[p.player_id] for p in pool if p.position == position]
        problem += pulp.lpSum(members) >= low, f"min_{position}"
        problem += pulp.lpSum(members) <= high, f"max_{position}"
    flex_members = [x[p.player_id] for p in pool if p.position in rules.flex_positions()]
    problem += pulp.lpSum(flex_members) == rules.flex_group_total, "flex_group"

    # --- salary ---
    cap = config.max_salary or rules.salary_cap
    problem += pulp.lpSum(p.salary * x[p.player_id] for p in pool) <= cap, "salary_cap"
    if min_salary > 0:
        problem += pulp.lpSum(p.salary * x[p.player_id] for p in pool) >= min_salary, "min_salary"

    # --- team and game spread ---
    max_per_team = config.max_per_team or rules.max_per_team
    teams = {p.team for p in pool if p.team}
    for team in teams:
        members = [
            x[p.player_id] for p in pool
            if p.team == team
            and (rules.dst_counts_toward_team_limit or p.position != "DST")
        ]
        if members:
            problem += pulp.lpSum(members) <= max_per_team, f"team_cap_{team}"

    games = sorted({p.game_key() for p in pool})
    game_vars = {}
    min_games = config.min_games or rules.min_games
    if min_games > 1 and "min_games" not in drop:
        for game in games:
            members = [x[p.player_id] for p in pool if p.game_key() == game]
            used = pulp.LpVariable(f"g_{abs(hash(game))}", cat="Binary")
            game_vars[game] = used
            # `used` can only be 1 if the game actually contributes a player.
            problem += used <= pulp.lpSum(members), f"game_used_{game}"
        problem += pulp.lpSum(game_vars.values()) >= min_games, "min_games"

    if config.max_per_game:
        for game in games:
            members = [x[p.player_id] for p in pool if p.game_key() == game]
            if members:
                problem += pulp.lpSum(members) <= config.max_per_game, f"game_cap_{game}"

    # --- locks ---
    for pid in config.locked:
        if pid in x:
            problem += x[pid] == 1, f"lock_{pid}"

    # --- cash safety rails ---
    if config.avoid_dst_vs_own_offense and "dst_conflict" not in drop:
        for dst in [p for p in pool if p.position == "DST"]:
            opponents = [
                x[p.player_id] for p in pool
                if p.position != "DST" and p.team and p.team == dst.opponent
            ]
            for opponent_var in opponents:
                problem += x[dst.player_id] + opponent_var <= 1, f"dst_conf_{dst.player_id}_{id(opponent_var)}"

    if not config.allow_qb_vs_dst and "qb_vs_dst" not in drop:
        for qb in [p for p in pool if p.position == "QB"]:
            for dst in [p for p in pool if p.position == "DST" and p.team == qb.opponent]:
                problem += x[qb.player_id] + x[dst.player_id] <= 1, f"qbdst_{qb.player_id}_{dst.player_id}"

    # --- stacking ---
    if config.require_qb_stack > 0 and "qb_stack" not in drop:
        for qb in [p for p in pool if p.position == "QB"]:
            mates = [
                x[p.player_id] for p in pool
                if p.team == qb.team and p.position in ("WR", "TE", "RB")
            ]
            if mates:
                problem += (
                    pulp.lpSum(mates) >= config.require_qb_stack * x[qb.player_id],
                    f"stack_{qb.player_id}",
                )

    # --- diversity from earlier lineups ---
    for i, previous in enumerate(banned):
        members = [x[pid] for pid in previous if pid in x]
        if members:
            problem += pulp.lpSum(members) <= config.max_overlap, f"overlap_{i}"

    return problem, x


def optimize(slate: Slate, config: OptimizerConfig | None = None) -> OptimizationResult:
    """Generate lineups, relaxing soft constraints only if forced to."""
    config = config or OptimizerConfig()
    rules = slate.rules
    pool = _eligible_pool(slate, config)
    result = OptimizationResult(pool_size=len(pool))

    if len(pool) < rules.roster_size:
        result.infeasible_reason = (
            f"Only {len(pool)} players survived the filters; {rules.roster_size} are needed. "
            "Loosen the projection/source minimums or un-exclude players."
        )
        return result

    # Soft constraints are dropped in this order when nothing is feasible.
    relaxation_ladder: list[tuple[set[str], int, str]] = [
        (set(), config.min_salary, ""),
        (set(), max(config.min_salary - 1500, 0), "lowered the minimum salary by $1,500"),
        ({"qb_stack"}, max(config.min_salary - 1500, 0), "dropped the QB stack requirement"),
        ({"qb_stack"}, 0, "removed the minimum salary floor"),
        ({"qb_stack", "dst_conflict", "qb_vs_dst"}, 0,
         "allowed your D/ST to face your own players"),
        ({"qb_stack", "dst_conflict", "qb_vs_dst", "min_games"}, 0,
         "dropped the minimum-games requirement (lineup may not be enterable)"),
    ]

    solver = _solver()
    if solver is None:
        result.infeasible_reason = (
            "No MILP solver is available. Reinstall PuLP so that its bundled CBC "
            "solver is present (pip install --force-reinstall pulp)."
        )
        return result

    banned: list[set[str]] = []
    for _ in range(max(config.n_lineups, 1)):
        lineup = None
        for drop, min_salary, description in relaxation_ladder:
            problem, x = _build_problem(
                pool, slate, config, rules, min_salary, banned, drop
            )
            status = problem.solve(solver)
            if pulp.LpStatus[status] != "Optimal":
                continue
            chosen = [p for p in pool if x[p.player_id].value() and x[p.player_id].value() > 0.5]
            if len(chosen) != rules.roster_size:
                continue
            if description and description not in result.relaxations:
                result.relaxations.append(description)
            lineup = Lineup(players=chosen, rules=rules, label=f"L{len(result.lineups) + 1}")
            break

        if lineup is None:
            if not result.lineups:
                result.infeasible_reason = (
                    "No legal lineup exists under these settings. The usual causes are too "
                    "many locks, an over-aggressive exclusion list, or a projection minimum "
                    "that empties a position."
                )
            break

        result.lineups.append(lineup)
        banned.append(set(lineup.ids()))

    return result
