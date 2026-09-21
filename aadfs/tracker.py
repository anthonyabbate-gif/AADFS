"""Track how your lineups and entries actually did.

Two separate questions get answered here, and they are worth keeping apart:

* **Were the projections right?** Compare projected to actual points per player,
  using nflverse results once the week is scored.
* **Was the lineup construction right?** Compare your lineup's actual score to
  the best lineup that was available from the same salary pool. The gap between
  them is what better construction could have bought you.

Contest results come from the entry-history CSV FanDuel lets you export. There
is no public FanDuel API, so that export is the supported path.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from aadfs.models import Lineup, Slate
from aadfs.names import normalize_name, normalize_team

_ENTRY_ALIASES = {
    "entry_id": ("entryid", "entry", "id"),
    "contest_id": ("contestid", "contest"),
    "contest_name": ("contestname", "title", "contest", "name"),
    "played_on": ("date", "playedon", "contestdate", "starttime"),
    "entry_fee": ("entryfee", "fee", "buyin", "cost"),
    "winnings": ("winnings", "winningsnonticket", "prize", "payout", "won"),
    "score": ("score", "points", "fantasypoints", "finalscore"),
    "position": ("position", "place", "rank", "finish"),
    "field_size": ("entries", "fieldsize", "totalentries", "opponents"),
}


def _norm(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
    if not text or text in {"-", "--"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def parse_entry_history(source: str | Path | io.StringIO,
                        season: int | None = None,
                        week: int | None = None) -> list[dict]:
    """Parse FanDuel's entry-history export into rows ready for the store."""
    if isinstance(source, (str, Path)) and Path(source).exists():
        text = Path(source).read_text(encoding="utf-8-sig")
    else:
        text = source.getvalue() if isinstance(source, io.StringIO) else str(source)

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Entry history file has no header row.")

    normalized = {_norm(f): f for f in reader.fieldnames}
    columns: dict[str, str] = {}
    for field_name, options in _ENTRY_ALIASES.items():
        for option in options:
            if option in normalized:
                columns[field_name] = normalized[option]
                break

    if "score" not in columns and "winnings" not in columns:
        raise ValueError(
            "Could not find a score or winnings column. Columns seen: "
            + ", ".join(reader.fieldnames)
        )

    rows: list[dict] = []
    for record in reader:
        def text_of(key: str) -> str | None:
            column = columns.get(key)
            return (record.get(column) or "").strip() or None if column else None

        rows.append(
            {
                "entry_id": text_of("entry_id"),
                "contest_id": text_of("contest_id"),
                "contest_name": text_of("contest_name"),
                "played_on": text_of("played_on"),
                "season": season,
                "week": week,
                "entry_fee": _number(text_of("entry_fee")),
                "winnings": _number(text_of("winnings")),
                "score": _number(text_of("score")),
                "position": (
                    int(_number(text_of("position")))
                    if _number(text_of("position")) is not None else None
                ),
                "field_size": (
                    int(_number(text_of("field_size")))
                    if _number(text_of("field_size")) is not None else None
                ),
            }
        )
    return rows


@dataclass
class BankrollSummary:
    """Aggregate performance across imported entries."""

    entries: int = 0
    total_fees: float = 0.0
    total_winnings: float = 0.0
    cashes: int = 0

    @property
    def net(self) -> float:
        return round(self.total_winnings - self.total_fees, 2)

    @property
    def roi(self) -> float:
        return round(self.net / self.total_fees, 4) if self.total_fees else 0.0

    @property
    def cash_rate(self) -> float:
        return round(self.cashes / self.entries, 4) if self.entries else 0.0

    def as_dict(self) -> dict:
        return {
            "entries": self.entries,
            "total_fees": round(self.total_fees, 2),
            "total_winnings": round(self.total_winnings, 2),
            "net": self.net,
            "roi": self.roi,
            "cash_rate": self.cash_rate,
            "breakeven_cash_rate_at_1_8x": round(1 / 1.8, 4),
        }


def summarize_entries(entries: list[dict]) -> BankrollSummary:
    """Roll imported entries up into bankroll performance."""
    summary = BankrollSummary()
    for entry in entries:
        fee = entry.get("entry_fee") or 0.0
        winnings = entry.get("winnings") or 0.0
        summary.entries += 1
        summary.total_fees += fee
        summary.total_winnings += winnings
        if winnings > 0:
            summary.cashes += 1
    return summary


@dataclass
class BacktestResult:
    """How a week actually went, once results are in."""

    season: int
    week: int
    lineup_score: float | None = None
    optimal_score: float | None = None
    scored_players: int = 0
    unscored_players: list[str] = field(default_factory=list)
    projection_mae: float | None = None
    projection_bias: float | None = None
    player_errors: list[dict] = field(default_factory=list)

    @property
    def gap_to_optimal(self) -> float | None:
        if self.lineup_score is None or self.optimal_score is None:
            return None
        return round(self.optimal_score - self.lineup_score, 2)

    def as_dict(self) -> dict:
        return {
            "season": self.season,
            "week": self.week,
            "lineup_score": self.lineup_score,
            "optimal_score": self.optimal_score,
            "gap_to_optimal": self.gap_to_optimal,
            "scored_players": self.scored_players,
            "unscored_players": self.unscored_players[:25],
            "projection_mae": self.projection_mae,
            "projection_bias": self.projection_bias,
            "player_errors": self.player_errors[:40],
        }


def apply_actuals(
    slate: Slate,
    actuals: dict[str, float],
    dst_actuals: dict[str, float] | None = None,
) -> tuple[int, list[str]]:
    """Attach actual scores to slate players. Returns (matched, unmatched names).

    Skill players are keyed by normalised name; team defenses are keyed by team
    code, because nflverse scores them in a separate team-level dataset.
    """
    matched, unmatched = 0, []
    dst_actuals = dst_actuals or {}
    for player in slate.players:
        if player.position == "DST":
            value = dst_actuals.get(normalize_team(player.team))
            if value is None:
                unmatched.append(f"{player.name} (DST)")
                continue
            player.notes.append(f"actual {value:.2f}")
            setattr(player, "actual", float(value))
            matched += 1
            continue
        value = actuals.get(normalize_name(player.name))
        if value is None:
            if player.salary > 0 and player.projection > 0:
                unmatched.append(player.name)
            continue
        player.notes.append(f"actual {value:.2f}")
        setattr(player, "actual", float(value))
        matched += 1
    return matched, unmatched


def score_lineup(lineup: Lineup) -> float | None:
    """A lineup's actual score, if every player has one."""
    total = 0.0
    for player in lineup.players:
        value = getattr(player, "actual", None)
        if value is None:
            return None
        total += float(value)
    return round(total, 2)


def backtest_week(
    slate: Slate,
    actuals: dict[str, float],
    lineup: Lineup | None = None,
    season: int | None = None,
    week: int | None = None,
    dst_actuals: dict[str, float] | None = None,
) -> BacktestResult:
    """Compare projections and lineup construction against what happened."""
    from aadfs.optimizer import OptimizerConfig, optimize

    result = BacktestResult(
        season=season or slate.season or 0,
        week=week or slate.week or 0,
    )
    matched, unmatched = apply_actuals(slate, actuals, dst_actuals)
    result.scored_players = matched
    result.unscored_players = unmatched

    errors = []
    for player in slate.players:
        actual = getattr(player, "actual", None)
        if actual is None or player.projection <= 0:
            continue
        error = actual - player.projection
        errors.append(error)
        result.player_errors.append(
            {
                "name": player.name, "position": player.position, "team": player.team,
                "salary": player.salary, "projected": round(player.projection, 2),
                "actual": round(actual, 2), "error": round(error, 2),
            }
        )

    if errors:
        result.projection_mae = round(sum(abs(e) for e in errors) / len(errors), 2)
        result.projection_bias = round(sum(errors) / len(errors), 2)
    result.player_errors.sort(key=lambda r: -abs(r["error"]))

    if lineup is not None:
        result.lineup_score = score_lineup(lineup)

    # The best lineup available in hindsight: optimise on actual scores.
    hindsight = Slate(
        players=[p for p in slate.players if getattr(p, "actual", None) is not None],
        name=slate.name, season=slate.season, week=slate.week, rules=slate.rules,
    )
    if len(hindsight.players) >= slate.rules.roster_size:
        saved = {p.player_id: (p.projection, p.floor, p.ceiling) for p in hindsight.players}
        for p in hindsight.players:
            p.projection = p.floor = p.ceiling = float(getattr(p, "actual"))
        best = optimize(
            hindsight,
            OptimizerConfig(
                weight_projection=1.0, weight_floor=0.0, min_salary=0,
                avoid_dst_vs_own_offense=False, exclude_out=False,
            ),
        )
        if best.lineups:
            result.optimal_score = best.lineups[0].projection
        for p in hindsight.players:
            p.projection, p.floor, p.ceiling = saved[p.player_id]

    return result
