"""Turning many sources' opinions into one ranking.

Three decisions matter here, and each is made deliberately:

**Rank, not points.** Sources are blended on the order they imply, not the
numbers they print. One source's 14.2 and another's 11.8 may say exactly the
same thing about a player if their whole scales differ; their ranks do not
suffer that problem. Sources that only publish an order can then sit alongside
sources that publish projections, which is the point of casting a wide net.

**Coverage is handled honestly.** Sources cover different players. A player
ranked by two sources and missed by six has not been "ranked 3rd" in any
meaningful sense. Missing entries are imputed at a penalty rather than ignored,
so that a player nobody else rates cannot float to the top on one enthusiastic
source.

**Disagreement survives.** The spread of a player's rank across sources is kept
and reported. When eight sources put someone between 4th and 6th, that is a
different claim from a player averaging 5th because half the sources say 1st and
half say 9th -- and it is exactly the kind of thing a consensus normally destroys.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import defaultdict

from aadfs.names import normalize_name, normalize_position, normalize_team
from aadfs.rankings.models import ConsensusBoard, ConsensusPlayer, SourceRanking

#: A player a source did not rank is treated as sitting this far past the end of
#: that source's list, rather than being dropped. Without a penalty, thin
#: coverage would look like strong agreement.
MISSING_RANK_PADDING = 6

#: A source this many ranks from the consensus on a player is flagged.
OUTLIER_THRESHOLD = 12.0

#: A player needs this many sources before the consensus is reported plainly.
MIN_SOURCES_FOR_CONFIDENCE = 3

#: Spread assumed for a player whose rank nobody has disagreed about yet --
#: either because only one source covers them, or because the sources happen to
#: agree exactly. Measured agreement of zero is not the same as certainty, and
#: treating it as such would put every player in a tier of their own.
UNKNOWN_SPREAD = 1.5


def _entry_key(name: str, position: str | None) -> tuple[str, str]:
    return normalize_name(name), (normalize_position(position) or "")


def ranks_within_position(source: SourceRanking) -> dict[tuple[str, str], float]:
    """Each player's rank within their position, for one source.

    Sources that publish an explicit rank are used as given. Sources that only
    publish projections are ranked by those projections, which is what makes a
    projection feed and a rankings feed comparable at all.
    """
    grouped: dict[str, list] = defaultdict(list)
    for entry in source.entries:
        position = normalize_position(entry.position)
        if not position:
            continue
        grouped[position].append(entry)

    result: dict[tuple[str, str], float] = {}
    for position, entries in grouped.items():
        explicit = [e for e in entries if e.rank is not None]
        if len(explicit) >= len(entries) / 2:
            # The source ranks its own players; respect that order.
            ordered = sorted(
                entries,
                key=lambda e: (e.rank if e.rank is not None else math.inf,
                               -(e.points or 0.0)),
            )
        else:
            ordered = sorted(entries, key=lambda e: -(e.points or 0.0))

        for index, entry in enumerate(ordered, start=1):
            key = _entry_key(entry.name, position)
            # First occurrence wins; duplicates inside one feed are ignored.
            result.setdefault(key, float(index))
    return result


#: Tiers exist to make a board scannable. Past this many players a tier stops
#: being a useful grouping, however genuinely muddled that stretch of the
#: ranking is, so it is split regardless.
MAX_TIER_SIZE = 6


def _tier_players(players: list[ConsensusPlayer], separation: float = 1.0) -> None:
    """Assign tiers in place.

    A tier runs until a player is far enough clear of the player who started it
    that their uncertainty bands no longer overlap. Measuring from the tier's
    first member rather than from a running boundary matters: a boundary that
    each uncertain player nudges outward compounds, and the middle of the board
    -- where disagreement is widest -- collapses into one enormous tier.

    Using measured disagreement rather than fixed group sizes means tiers come
    out wide where the sources are unsure and tight where they agree, which is
    the entire reason to draw them.
    """
    if not players:
        return

    tier = 1
    anchor = players[0]
    size = 0
    for player in players:
        spread = max(player.rank_stdev, UNKNOWN_SPREAD)
        anchor_spread = max(anchor.rank_stdev, UNKNOWN_SPREAD)
        gap = player.consensus_rank - anchor.consensus_rank
        separated = gap > separation * (anchor_spread + spread)

        if size and (separated or size >= MAX_TIER_SIZE):
            tier += 1
            anchor = player
            size = 0
        player.tier = tier
        size += 1


def build_consensus(
    sources: list[SourceRanking],
    season: int,
    week: int,
    *,
    weights: dict[str, float] | None = None,
    min_sources: int = 1,
) -> ConsensusBoard:
    """Blend every source into one board."""
    weights = weights or {}
    usable = [s for s in sources if s.ok and s.entries]

    per_source: dict[str, dict[tuple[str, str], float]] = {}
    meta: dict[tuple[str, str], dict] = {}
    points: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)

    for source in usable:
        ranked = ranks_within_position(source)
        per_source[source.source] = ranked
        for entry in source.entries:
            position = normalize_position(entry.position)
            if not position:
                continue
            key = _entry_key(entry.name, position)
            record = meta.setdefault(
                key, {"name": entry.name, "position": position,
                      "team": None, "opponent": None}
            )
            # Prefer a real team code over nothing; sources vary in what they give.
            if record["team"] is None and entry.team:
                record["team"] = normalize_team(entry.team)
            if record["opponent"] is None and entry.opponent:
                record["opponent"] = normalize_team(entry.opponent)
            if entry.points is not None:
                points[key][source.source] = float(entry.points)

    # How long each source's list is, per position, for the missing-rank penalty.
    list_length: dict[tuple[str, str], int] = defaultdict(int)
    for name, ranked in per_source.items():
        for (_, position), rank in ranked.items():
            slot = (name, position)
            list_length[slot] = max(list_length[slot], int(rank))

    players: list[ConsensusPlayer] = []
    for key, record in meta.items():
        position = record["position"]
        observed: dict[str, float] = {}
        contributions: list[tuple[float, float]] = []  # (rank, weight)

        for name, ranked in per_source.items():
            weight = weights.get(name, next(
                (s.weight for s in usable if s.source == name), 1.0
            ))
            if weight <= 0:
                continue
            rank = ranked.get(key)
            if rank is not None:
                observed[name] = rank
                contributions.append((rank, weight))
            elif list_length.get((name, position)):
                # Unranked by this source: park them past the end of its list.
                penalty = list_length[(name, position)] + MISSING_RANK_PADDING
                contributions.append((float(penalty), weight * 0.5))

        if not observed or len(observed) < min_sources:
            continue

        total_weight = sum(w for _, w in contributions) or 1.0
        consensus = sum(r * w for r, w in contributions) / total_weight
        values = list(observed.values())

        player = ConsensusPlayer(
            name=record["name"],
            position=position,
            team=record["team"],
            opponent=record["opponent"],
            consensus_rank=round(consensus, 2),
            source_count=len(observed),
            source_ranks={k: round(v, 1) for k, v in observed.items()},
            source_points={k: round(v, 2) for k, v in points.get(key, {}).items()},
            rank_stdev=round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
            best_rank=min(values),
            worst_rank=max(values),
        )
        if player.source_points:
            player.mean_points = round(
                sum(player.source_points.values()) / len(player.source_points), 2
            )
        if player.source_count < MIN_SOURCES_FOR_CONFIDENCE:
            player.notes.append(f"only {player.source_count} source(s) rank this player")

        for name, rank in observed.items():
            if abs(rank - consensus) >= OUTLIER_THRESHOLD:
                player.outliers.append(
                    f"{name} {'much higher' if rank < consensus else 'much lower'} "
                    f"({rank:.0f} vs {consensus:.0f})"
                )
        players.append(player)

    # Rank and tier within each position.
    for position in {p.position for p in players}:
        group = sorted(
            (p for p in players if p.position == position),
            key=lambda p: (p.consensus_rank, -(p.mean_points or 0.0)),
        )
        for index, player in enumerate(group, start=1):
            player.overall_rank = index
        _tier_players(group)

    players.sort(key=lambda p: (p.position, p.overall_rank))

    return ConsensusBoard(
        season=season,
        week=week,
        players=players,
        sources=sources,
        generated_at=dt.datetime.now().isoformat(timespec="seconds"),
    )
