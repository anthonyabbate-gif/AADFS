"""Shared plumbing for projection sources.

Every source turns some external feed into a list of `ProjectionRow`. Sources
are deliberately independent and failure-tolerant: one dead endpoint on a Sunday
morning must never take the whole app down, so `SourceResult` carries the error
instead of raising.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from aadfs.scoring import StatLine, score_statline

DEFAULT_CACHE_DIR = Path("data/cache")
DEFAULT_TIMEOUT = 25.0
USER_AGENT = "aadfs/0.1 (personal DFS research tool)"


@dataclass
class ProjectionRow:
    """One source's opinion about one player."""

    name: str
    position: str | None = None
    team: str | None = None
    points: float | None = None
    #: Raw projected stats, when the source gives them. Preferred over `points`
    #: because it lets us apply FanDuel scoring rather than trust theirs.
    statline: StatLine | None = None
    opponent: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def fanduel_points(self) -> float | None:
        """Points under FanDuel scoring, from stats when available."""
        if self.statline is not None:
            return score_statline(self.statline)
        return self.points


@dataclass
class SourceResult:
    """What a source returned, including how it failed if it did."""

    source: str
    rows: list[ProjectionRow] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    fetched_at: float = field(default_factory=time.time)
    from_cache: bool = False

    @property
    def count(self) -> int:
        return len(self.rows)


class ProjectionSource(Protocol):
    """A named provider of weekly projections."""

    name: str
    #: Relative trust used when blending. Tune these as you learn what works.
    weight: float

    def fetch(self, season: int, week: int) -> SourceResult: ...


class HttpCache:
    """A small on-disk response cache.

    Saturday scans get re-run a lot while you tinker; caching keeps you from
    hammering anyone's endpoint and makes the whole pipeline replayable offline.
    """

    def __init__(self, directory: Path | str = DEFAULT_CACHE_DIR, ttl_seconds: int = 6 * 3600):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.dir / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        if self.ttl >= 0 and time.time() - path.stat().st_mtime > self.ttl:
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: Any) -> None:
        try:
            self._path(key).write_text(json.dumps(value))
        except (OSError, TypeError):
            pass  # a cache miss is never worth failing a fetch over


def fetch_json(
    url: str,
    *,
    cache: HttpCache | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[Any, bool]:
    """GET a JSON document, returning (payload, came_from_cache)."""
    key = json.dumps([url, params or {}, sorted((headers or {}).items())], sort_keys=True)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit, True

    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    merged.update(headers or {})
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, headers=merged, params=params)
        response.raise_for_status()
        payload = response.json()

    if cache is not None:
        cache.set(key, payload)
    return payload, False


def safe_fetch(source_name: str, fn) -> SourceResult:
    """Run a source's fetch, converting any failure into a reportable result."""
    try:
        rows, from_cache = fn()
        return SourceResult(source=source_name, rows=rows, ok=True, from_cache=from_cache)
    except httpx.HTTPStatusError as exc:
        return SourceResult(
            source=source_name, ok=False,
            error=f"HTTP {exc.response.status_code} from {exc.request.url}",
        )
    except httpx.RequestError as exc:
        return SourceResult(source=source_name, ok=False, error=f"network error: {exc}")
    except Exception as exc:  # a malformed feed should not end the scan
        return SourceResult(source=source_name, ok=False, error=f"{type(exc).__name__}: {exc}")
