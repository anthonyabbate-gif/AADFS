"""The local web app.

A single-user tool that runs on your own machine, so state is held in memory
for the active slate and persisted to SQLite only when you save a lineup or
import results. Nothing is sent anywhere.
"""

from __future__ import annotations

import io
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from aadfs.consensus import build_consensus
from aadfs.ingest.fanduel import (
    lineups_to_entry_csv, lineups_to_readable_csv, parse_salary_csv,
)
from aadfs.models import Slate
from aadfs.optimizer import OptimizerConfig
from aadfs.pipeline import (
    build_lineups, default_sources, guess_season_and_week, load_variance,
)
from aadfs.simulate import ContestSettings
from aadfs.sources.base import HttpCache, SourceResult
from aadfs.sources.csv_source import parse_projection_csv
from aadfs.store import Store
from aadfs.tracker import parse_entry_history, summarize_entries

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@dataclass
class AppState:
    """Everything the browser session is currently working with."""

    slate: Slate | None = None
    source_results: list[SourceResult] = field(default_factory=list)
    player_sd: dict[str, float] = field(default_factory=dict)
    position_sd: dict[str, float] = field(default_factory=dict)
    history_error: str | None = None
    lineups: list = field(default_factory=list)
    simulations: list = field(default_factory=list)
    relaxations: list[str] = field(default_factory=list)
    last_config: dict = field(default_factory=dict)

    def require_slate(self) -> Slate:
        if self.slate is None:
            raise HTTPException(
                status_code=400,
                detail="No slate loaded. Upload a FanDuel players-list CSV first.",
            )
        return self.slate


state = AppState()
store = Store()
app = FastAPI(title="AADFS — FanDuel NFL cash lineup builder")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def _player_payload(player) -> dict:
    return {
        "id": player.player_id,
        "name": player.name,
        "position": player.position,
        "team": player.team,
        "opponent": player.opponent,
        "salary": player.salary,
        "projection": round(player.projection, 2),
        "floor": round(player.floor, 2),
        "ceiling": round(player.ceiling, 2),
        "stdev": round(player.stdev, 2),
        "value": round(player.value, 2),
        "sources": player.source_count,
        "source_values": player.source_values,
        "injury": player.injury_status or "",
        "injury_detail": player.injury_detail or "",
        "is_out": player.is_out,
        "is_questionable": player.is_questionable,
        "notes": player.notes,
        "game": player.game_key(),
    }


def _lineup_payload(lineup, simulation=None) -> dict:
    return {
        "label": lineup.label,
        "salary": lineup.salary,
        "salary_remaining": lineup.salary_remaining,
        "projection": lineup.projection,
        "floor": lineup.floor,
        "ceiling": lineup.ceiling,
        "stdev": lineup.stdev,
        "games": lineup.game_count,
        "team_counts": lineup.team_counts,
        "players": [
            {**_player_payload(p), "slot": slot} for slot, p in lineup.slotted()
        ],
        "simulation": simulation.as_dict() if simulation else None,
    }


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Surface real error text in the UI; this app only ever runs locally."""
    if isinstance(exc, HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return JSONResponse(
        {"detail": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]},
        status_code=500,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    season, week = guess_season_and_week()
    return templates.TemplateResponse(
        request, "index.html", {"season": season, "week": week}
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/status")
async def status():
    slate = state.slate
    return {
        "has_slate": slate is not None,
        "slate": (
            {
                "name": slate.name,
                "season": slate.season,
                "week": slate.week,
                "players": len(slate.players),
                "teams": len(slate.teams),
                "games": len(slate.games),
                "projected": sum(1 for p in slate.players if p.projection > 0),
            }
            if slate else None
        ),
        "sources": [
            {"source": r.source, "ok": r.ok, "rows": r.count, "error": r.error}
            for r in state.source_results
        ],
        "history_error": state.history_error,
        "lineups": len(state.lineups),
    }


@app.post("/api/slate/upload")
async def upload_slate(
    salary_file: UploadFile = File(...),
    projection_files: list[UploadFile] | None = File(None),
    season: int = Form(0),
    week: int = Form(0),
    history_seasons: str = Form(""),
):
    """Load a FanDuel players list, plus any projection CSVs you already have."""
    raw = (await salary_file.read()).decode("utf-8-sig", errors="replace")
    try:
        slate = parse_salary_csv(io.StringIO(raw), slate_name=salary_file.filename or "slate")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    guessed_season, guessed_week = guess_season_and_week()
    slate.season = season or guessed_season
    slate.week = week or guessed_week

    results: list[SourceResult] = []
    for upload in projection_files or []:
        if not upload.filename:
            continue
        text = (await upload.read()).decode("utf-8-sig", errors="replace")
        results.append(
            parse_projection_csv(io.StringIO(text), source_name=Path(upload.filename).stem)
        )

    seasons = [int(s) for s in history_seasons.replace(",", " ").split() if s.strip().isdigit()]
    if not seasons:
        seasons = [slate.season - 1]
    player_sd, position_sd, _, history_error = load_variance(seasons)

    state.slate = slate
    state.source_results = results
    state.player_sd = player_sd
    state.position_sd = position_sd
    state.history_error = history_error
    state.lineups, state.simulations, state.relaxations = [], [], []

    report = build_consensus(
        slate, results, historical_sd=player_sd, position_sd=position_sd
    )
    store.save_slate(slate.name, slate.season, slate.week, len(slate.players))

    return {
        "slate": {
            "name": slate.name, "season": slate.season, "week": slate.week,
            "players": len(slate.players), "teams": sorted(slate.teams),
            "games": sorted(slate.games),
        },
        "consensus": {
            "projected": report.players_with_projection,
            "missing": report.players_without_projection[:25],
            "missing_count": len(report.players_without_projection),
            "sources": [
                {"source": s.source, "ok": s.ok, "matched": s.matched,
                 "unmatched": s.unmatched_count, "unmatched_sample": s.unmatched[:8],
                 "error": s.error}
                for s in report.sources
            ],
        },
        "history_error": history_error,
        "position_stdev": position_sd,
    }


@app.post("/api/projections/fetch")
async def fetch_projections(refresh: bool = False):
    """Pull the remote projection sources and re-blend."""
    slate = state.require_slate()
    cache = HttpCache(ttl_seconds=0 if refresh else 6 * 3600)
    results = [s.fetch(slate.season, slate.week) for s in default_sources(cache)]

    # Keep any CSV-based sources already loaded alongside the fetched ones.
    csv_results = [r for r in state.source_results if r.source not in {x.source for x in results}]
    state.source_results = csv_results + results

    report = build_consensus(
        slate, state.source_results,
        historical_sd=state.player_sd, position_sd=state.position_sd,
    )
    return {
        "projected": report.players_with_projection,
        "missing_count": len(report.players_without_projection),
        "sources": [
            {"source": s.source, "ok": s.ok, "matched": s.matched,
             "unmatched": s.unmatched_count, "unmatched_sample": s.unmatched[:8],
             "error": s.error}
            for s in report.sources
        ],
    }


@app.get("/api/players")
async def players():
    slate = state.require_slate()
    return {
        "players": [_player_payload(p) for p in sorted(slate.players, key=lambda p: -p.projection)]
    }


@app.post("/api/build")
async def build(payload: dict):
    """Build lineups from the current slate and the supplied settings."""
    slate = state.require_slate()
    if not any(p.projection > 0 for p in slate.players):
        raise HTTPException(
            status_code=400,
            detail="No player has a projection yet. Fetch sources or upload a projections CSV.",
        )

    config = OptimizerConfig(
        n_lineups=int(payload.get("n_lineups", 1)),
        weight_projection=float(payload.get("weight_projection", 0.55)),
        weight_floor=float(payload.get("weight_floor", 0.45)),
        weight_ceiling=float(payload.get("weight_ceiling", 0.0)),
        risk_penalty=float(payload.get("risk_penalty", 0.0)),
        min_salary=int(payload.get("min_salary", 58_200)),
        max_per_team=payload.get("max_per_team") or None,
        max_per_game=payload.get("max_per_game") or None,
        avoid_dst_vs_own_offense=bool(payload.get("avoid_dst_vs_own_offense", True)),
        exclude_out=bool(payload.get("exclude_out", True)),
        exclude_questionable=bool(payload.get("exclude_questionable", False)),
        min_projection=float(payload.get("min_projection", 0.0)),
        min_sources=int(payload.get("min_sources", 0)),
        require_qb_stack=int(payload.get("require_qb_stack", 0)),
        locked=set(payload.get("locked") or []),
        excluded=set(payload.get("excluded") or []),
        max_overlap=int(payload.get("max_overlap", 7)),
    )
    contest = ContestSettings(
        cash_fraction=float(payload.get("cash_fraction", 0.5)),
        payout_multiple=float(payload.get("payout_multiple", 1.8)),
        field_size=int(payload.get("field_size", 200)),
        field_sharpness=float(payload.get("field_sharpness", 1.5)),
    )

    outcome = build_lineups(
        slate, config, contest,
        simulate=bool(payload.get("simulate", True)),
        n_sims=int(payload.get("n_sims", 10_000)),
    )

    state.lineups = outcome.optimization.lineups
    state.simulations = outcome.simulations
    state.relaxations = outcome.optimization.relaxations
    state.last_config = payload

    if not outcome.optimization.lineups:
        raise HTTPException(
            status_code=400,
            detail=outcome.optimization.infeasible_reason or "No lineup could be built.",
        )

    simulations = outcome.simulations or [None] * len(outcome.optimization.lineups)
    return {
        "lineups": [
            _lineup_payload(lineup, simulation)
            for lineup, simulation in zip(outcome.optimization.lineups, simulations)
        ],
        "relaxations": outcome.optimization.relaxations,
        "pool_size": outcome.optimization.pool_size,
        "contest": {
            "cash_fraction": contest.cash_fraction,
            "payout_multiple": contest.payout_multiple,
            "breakeven_win_rate": round(contest.breakeven_win_rate, 4),
        },
    }


@app.post("/api/lineups/save")
async def save_lineups():
    """Persist the current lineups so results can be compared to them later."""
    slate = state.require_slate()
    if not state.lineups:
        raise HTTPException(status_code=400, detail="No lineups to save.")
    slate_id = store.save_slate(slate.name, slate.season, slate.week, len(slate.players))
    simulations = state.simulations or [None] * len(state.lineups)
    ids = [
        store.save_lineup(
            slate_id, lineup, state.last_config,
            simulation.as_dict() if simulation else None,
        )
        for lineup, simulation in zip(state.lineups, simulations)
    ]
    return {"saved": len(ids), "lineup_ids": ids}


@app.get("/api/export/entries.csv", response_class=PlainTextResponse)
async def export_entries():
    if not state.lineups:
        raise HTTPException(status_code=400, detail="No lineups to export.")
    return PlainTextResponse(
        lineups_to_entry_csv(state.lineups),
        headers={"Content-Disposition": "attachment; filename=fanduel_entries.csv"},
    )


@app.get("/api/export/lineups.csv", response_class=PlainTextResponse)
async def export_readable():
    if not state.lineups:
        raise HTTPException(status_code=400, detail="No lineups to export.")
    return PlainTextResponse(
        lineups_to_readable_csv(state.lineups),
        headers={"Content-Disposition": "attachment; filename=lineups.csv"},
    )


@app.post("/api/results/upload")
async def upload_results(
    file: UploadFile = File(...), season: int = Form(0), week: int = Form(0)
):
    """Import FanDuel's entry-history export."""
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        rows = parse_entry_history(io.StringIO(text), season=season or None, week=week or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    added = store.save_entries(rows)
    return {"parsed": len(rows), "new": added, **summarize_entries(store.all_entries()).as_dict()}


@app.get("/api/results/summary")
async def results_summary():
    entries = store.all_entries()
    return {
        "summary": summarize_entries(entries).as_dict(),
        "entries": entries[:200],
        "saved_lineups": store.recent_lineups(25),
    }
