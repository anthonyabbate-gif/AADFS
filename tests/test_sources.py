import io

from aadfs.scoring import score_statline
from aadfs.sources.base import HttpCache, ProjectionRow, SourceResult, safe_fetch
from aadfs.sources.csv_source import parse_projection_csv
from aadfs.sources.espn import ESPNSource
from aadfs.sources.nflverse import add_fanduel_points, calibrate_position_variance
from aadfs.sources.sleeper import SleeperSource, _to_statline


def test_csv_source_detects_common_column_names():
    text = "Player,Pos,Team,FPTS\nJa'Marr Chase,WR,CIN,18.4\nJosh Allen,QB,BUF,21.0\n"
    result = parse_projection_csv(io.StringIO(text), source_name="mine")
    assert result.ok and result.count == 2
    assert result.rows[0].name == "Ja'Marr Chase"
    assert result.rows[0].points == 18.4


def test_csv_source_reads_alternate_headers_and_extras():
    text = "name,position,team,projection,floor,ceiling,ownership\nX Y,RB,SF,14.2,8,22,31.5%\n"
    result = parse_projection_csv(io.StringIO(text))
    row = result.rows[0]
    assert row.points == 14.2
    assert row.extra == {"floor": 8.0, "ceiling": 22.0, "ownership": 31.5}


def test_csv_source_explains_a_missing_projection_column():
    result = parse_projection_csv(io.StringIO("Player,Team\nA B,SF\n"))
    assert not result.ok
    assert "projection column" in result.error


def test_csv_source_explains_a_missing_name_column():
    result = parse_projection_csv(io.StringIO("Team,FPTS\nSF,12\n"))
    assert not result.ok
    assert "name column" in result.error


def test_csv_source_skips_unparseable_rows_but_keeps_the_rest():
    text = "Player,FPTS\nGood Player,12.5\nBad Player,n/a\n,9\n"
    result = parse_projection_csv(io.StringIO(text))
    assert result.count == 1
    assert result.rows[0].name == "Good Player"


def test_sleeper_stats_map_onto_fanduel_scoring():
    line = _to_statline({"pass_yd": 250, "pass_td": 2, "pass_int": 1, "rush_yd": 30})
    # 10 + 8 - 1 + 3
    assert score_statline(line) == 20.0


def test_sleeper_ignores_unknown_stat_keys():
    line = _to_statline({"rec": 5, "rec_yd": 60, "some_new_metric": 99})
    assert score_statline(line) == 2.5 + 6.0


def test_projection_row_prefers_statline_over_supplied_points():
    row = ProjectionRow(name="X", points=100.0, statline=_to_statline({"rec": 4}))
    assert row.fanduel_points() == 2.0


def test_projection_row_falls_back_to_points():
    assert ProjectionRow(name="X", points=13.5).fanduel_points() == 13.5


def test_safe_fetch_converts_exceptions_into_a_result():
    def boom():
        raise ValueError("feed changed shape")

    result = safe_fetch("broken", boom)
    assert not result.ok
    assert "feed changed shape" in result.error
    assert result.rows == []


def test_remote_sources_degrade_instead_of_raising(monkeypatch):
    """A dead endpoint must produce a reportable result, never an exception."""
    import httpx

    def explode(*args, **kwargs):
        raise httpx.ConnectError("name resolution failed")

    for module in ("aadfs.sources.sleeper", "aadfs.sources.espn"):
        monkeypatch.setattr(f"{module}.fetch_json", explode)

    for source in (SleeperSource(), ESPNSource()):
        result = source.fetch(2025, 3)
        assert isinstance(result, SourceResult)
        assert not result.ok
        assert "network error" in result.error
        assert result.rows == []


def test_remote_sources_parse_a_well_formed_payload(monkeypatch):
    """Guard the response shape each source expects."""
    payload = [
        {
            "player": {"full_name": "Josh Allen", "position": "QB"},
            "team": "BUF",
            "player_id": "4984",
            "stats": {"pass_yd": 250, "pass_td": 2, "rush_yd": 30},
        }
    ]
    monkeypatch.setattr(
        "aadfs.sources.sleeper.fetch_json", lambda *a, **k: (payload, False)
    )
    result = SleeperSource().fetch(2025, 3)
    assert result.ok
    row = result.rows[0]
    assert row.name == "Josh Allen"
    assert row.team == "BUF"
    assert row.fanduel_points() == 10.0 + 8.0 + 3.0


def test_http_cache_round_trip(tmp_path):
    cache = HttpCache(tmp_path, ttl_seconds=60)
    assert cache.get("key") is None
    cache.set("key", {"a": 1})
    assert cache.get("key") == {"a": 1}


def test_http_cache_expires(tmp_path):
    cache = HttpCache(tmp_path, ttl_seconds=-1)
    cache.set("key", {"a": 1})
    cache.ttl = 0
    assert cache.get("key") is None


def test_nflverse_scoring_matches_the_scoring_module():
    import pandas as pd

    frame = pd.DataFrame([{
        "passing_yards": 300, "passing_tds": 2, "interceptions": 1,
        "rushing_yards": 40, "rushing_tds": 1, "receptions": 0,
        "receiving_yards": 0, "receiving_tds": 0,
    }])
    scored = add_fanduel_points(frame)
    # 12 + 8 - 1 + 4 + 6
    assert scored["fd_points"].iloc[0] == 29.0


def test_position_variance_falls_back_when_history_is_empty():
    import pandas as pd

    result = calibrate_position_variance(pd.DataFrame())
    assert set(result) >= {"QB", "RB", "WR", "TE", "DST"}
    assert all(v > 0 for v in result.values())
