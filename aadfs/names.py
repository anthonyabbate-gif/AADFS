"""Name and team normalisation.

Every projection source spells players differently. Getting this wrong is the
most common way a DFS tool quietly produces a bad lineup: an unmatched player
falls back to a default projection and either never gets rostered or gets
rostered for the wrong reason. So matching is deliberately conservative and
always reports what it could not match.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz, process

# Canonical team codes follow FanDuel's spelling.
TEAM_ALIASES = {
    "ARZ": "ARI", "ARI": "ARI", "ATL": "ATL", "BLT": "BAL", "BAL": "BAL",
    "BUF": "BUF", "CAR": "CAR", "CHI": "CHI", "CIN": "CIN", "CLV": "CLE",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GB": "GB",
    "GNB": "GB", "HOU": "HOU", "HST": "HOU", "IND": "IND", "JAX": "JAC",
    "JAC": "JAC", "KC": "KC", "KAN": "KC", "LV": "LV", "LVR": "LV",
    "OAK": "LV", "LAC": "LAC", "SD": "LAC", "SDG": "LAC", "LAR": "LAR",
    "LA": "LAR", "STL": "LAR", "MIA": "MIA", "MIN": "MIN", "NE": "NE",
    "NWE": "NE", "NO": "NO", "NOR": "NO", "NYG": "NYG", "NYJ": "NYJ",
    "PHI": "PHI", "PIT": "PIT", "SF": "SF", "SFO": "SF", "SEA": "SEA",
    "TB": "TB", "TAM": "TB", "TEN": "TEN", "WAS": "WAS", "WSH": "WAS",
    "WFT": "WAS",
}

TEAM_FULL_NAMES = {
    "arizona cardinals": "ARI", "atlanta falcons": "ATL",
    "baltimore ravens": "BAL", "buffalo bills": "BUF",
    "carolina panthers": "CAR", "chicago bears": "CHI",
    "cincinnati bengals": "CIN", "cleveland browns": "CLE",
    "dallas cowboys": "DAL", "denver broncos": "DEN",
    "detroit lions": "DET", "green bay packers": "GB",
    "houston texans": "HOU", "indianapolis colts": "IND",
    "jacksonville jaguars": "JAC", "kansas city chiefs": "KC",
    "las vegas raiders": "LV", "los angeles chargers": "LAC",
    "los angeles rams": "LAR", "miami dolphins": "MIA",
    "minnesota vikings": "MIN", "new england patriots": "NE",
    "new orleans saints": "NO", "new york giants": "NYG",
    "new york jets": "NYJ", "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT", "san francisco 49ers": "SF",
    "seattle seahawks": "SEA", "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN", "washington commanders": "WAS",
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
#: Apostrophes and periods are dropped outright so that "Ja'Marr" and "JaMarr",
#: or "St." and "St", collapse together. Everything else non-alphanumeric
#: becomes a space, so hyphenated names split into words.
_INTRA_WORD = re.compile(r"['\u2019.]+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# Position labels vary; DST is the worst offender.
POSITION_ALIASES = {
    "QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB",
    "WR": "WR", "TE": "TE",
    "D": "DST", "DEF": "DST", "DST": "DST", "D/ST": "DST", "DEFENSE": "DST",
}


def normalize_team(value: str | None) -> str | None:
    """Map any common spelling of a team to the FanDuel code."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered in TEAM_FULL_NAMES:
        return TEAM_FULL_NAMES[lowered]
    upper = raw.upper().replace(".", "")
    if upper in TEAM_ALIASES:
        return TEAM_ALIASES[upper]
    # Handle "Green Bay" / "49ers" style partial names.
    for full, code in TEAM_FULL_NAMES.items():
        if lowered in full or full.endswith(lowered):
            return code
    return upper or None


def normalize_position(value: str | None) -> str | None:
    if not value:
        return None
    key = str(value).strip().upper()
    return POSITION_ALIASES.get(key, key or None)


def normalize_name(value: str | None) -> str:
    """Reduce a player name to a comparable key.

    Strips accents, punctuation, and generational suffixes, so that
    "Marvin Harrison Jr." and "Marvin Harrison, Jr" collapse together.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = _INTRA_WORD.sub("", text)
    text = _NON_ALNUM.sub(" ", text)
    parts = [p for p in _WS.sub(" ", text).strip().split(" ") if p]
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def name_key(name: str | None, team: str | None = None, position: str | None = None) -> str:
    """A key that disambiguates same-named players by team and position."""
    return "|".join(
        [normalize_name(name), normalize_team(team) or "", normalize_position(position) or ""]
    )


def dst_key(team: str | None) -> str:
    """D/ST rows are identified by team only; names are hopeless there."""
    return f"DST|{normalize_team(team) or ''}"


@dataclass
class MatchResult:
    """Outcome of matching one external row onto the salary file."""

    target_id: str | None
    score: float
    method: str  # "exact" | "name_team" | "name_only" | "fuzzy" | "unmatched"


class PlayerMatcher:
    """Matches external rows onto a fixed roster of salary-file players.

    Built once per slate from the FanDuel salary file, then queried per source.
    """

    #: Fuzzy matches below this similarity are rejected rather than guessed.
    FUZZY_THRESHOLD = 88.0

    def __init__(self, players: list["Player"]) -> None:  # noqa: F821 - runtime import cycle
        self._by_full: dict[str, str] = {}
        self._by_name: dict[str, list[str]] = {}
        self._dst: dict[str, str] = {}
        self._names: list[str] = []
        self._name_to_ids: dict[str, list[str]] = {}
        for p in players:
            if p.position == "DST":
                self._dst[dst_key(p.team)] = p.player_id
                continue
            full = name_key(p.name, p.team, p.position)
            self._by_full.setdefault(full, p.player_id)
            norm = normalize_name(p.name)
            self._by_name.setdefault(norm, []).append(p.player_id)
            self._name_to_ids.setdefault(norm, []).append(p.player_id)
        self._names = list(self._name_to_ids)

    def match(
        self, name: str | None, team: str | None = None, position: str | None = None
    ) -> MatchResult:
        pos = normalize_position(position)
        if pos == "DST":
            # Source may give the team in the name field ("Ravens D/ST").
            key = dst_key(team) if team else dst_key(name)
            pid = self._dst.get(key)
            if pid is None and name:
                pid = self._dst.get(dst_key(name))
            if pid:
                return MatchResult(pid, 100.0, "exact")
            return MatchResult(None, 0.0, "unmatched")

        full = name_key(name, team, position)
        if full in self._by_full:
            return MatchResult(self._by_full[full], 100.0, "exact")

        norm = normalize_name(name)
        if not norm:
            return MatchResult(None, 0.0, "unmatched")

        candidates = self._by_name.get(norm, [])
        if len(candidates) == 1:
            return MatchResult(candidates[0], 97.0, "name_only")
        if len(candidates) > 1:
            # Ambiguous without a team; refuse rather than coin-flip.
            return MatchResult(None, 0.0, "unmatched")

        best = process.extractOne(norm, self._names, scorer=fuzz.WRatio)
        if best and best[1] >= self.FUZZY_THRESHOLD:
            ids = self._name_to_ids[best[0]]
            if len(ids) == 1:
                return MatchResult(ids[0], float(best[1]), "fuzzy")
        return MatchResult(None, 0.0, "unmatched")
