from aadfs.consensus import build_consensus
from aadfs.scoring import StatLine
from aadfs.sources.base import ProjectionRow, SourceResult


def _result(source, rows, ok=True, error=None):
    return SourceResult(source=source, rows=rows, ok=ok, error=error)


def test_fanduel_average_is_used_when_nothing_else_exists(slate):
    report = build_consensus(slate, [])
    assert report.players_with_projection == len(slate.players)
    player = slate.players[0]
    assert player.projection == player.fd_projection
    assert player.source_count == 0  # FanDuel's own number is not an external source


def test_sources_are_blended_by_weight(slate):
    target = next(p for p in slate.players if p.position == "WR")
    target.fd_projection = None
    results = [
        _result("a", [ProjectionRow(name=target.name, team=target.team,
                                    position="WR", points=10.0)]),
        _result("b", [ProjectionRow(name=target.name, team=target.team,
                                    position="WR", points=20.0)]),
    ]
    build_consensus(slate, results, weights={"a": 3.0, "b": 1.0}, use_fanduel_fppg=False)
    # (10*3 + 20*1) / 4 = 12.5
    assert target.projection == 12.5
    assert target.source_count == 2


def test_statlines_are_rescored_under_fanduel_rules(slate):
    target = next(p for p in slate.players if p.position == "WR")
    row = ProjectionRow(
        name=target.name, team=target.team, position="WR",
        points=999.0,  # the source's own scoring must be ignored
        statline=StatLine(receptions=6, rec_yd=80, rec_td=0.5),
    )
    build_consensus(slate, [_result("a", [row])], use_fanduel_fppg=False)
    assert target.projection == 3.0 + 8.0 + 3.0


def test_disagreement_widens_the_distribution(slate):
    wrs = [p for p in slate.players if p.position == "WR"][:2]
    agree, disagree = wrs
    for p in wrs:
        p.fd_projection = None
    results = [
        _result("a", [
            ProjectionRow(name=agree.name, team=agree.team, position="WR", points=12.0),
            ProjectionRow(name=disagree.name, team=disagree.team, position="WR", points=4.0),
        ]),
        _result("b", [
            ProjectionRow(name=agree.name, team=agree.team, position="WR", points=12.0),
            ProjectionRow(name=disagree.name, team=disagree.team, position="WR", points=20.0),
        ]),
    ]
    build_consensus(slate, results, use_fanduel_fppg=False)
    assert agree.projection == disagree.projection == 12.0
    assert disagree.stdev > agree.stdev
    assert disagree.floor < agree.floor


def test_failed_sources_are_reported_not_raised(slate):
    report = build_consensus(
        slate, [_result("dead", [], ok=False, error="network error")]
    )
    failed = [s for s in report.sources if not s.ok]
    assert [s.source for s in failed] == ["dead"]
    assert failed[0].error == "network error"


def test_unmatched_rows_are_reported(slate):
    results = [_result("a", [ProjectionRow(name="Nobody At All", team="BUF", position="WR",
                                           points=10.0)])]
    report = build_consensus(slate, results)
    assert "Nobody At All" in report.sources[0].unmatched


def test_floor_is_below_projection_and_ceiling_above(slate):
    build_consensus(slate, [])
    for player in slate.players:
        if player.projection > 0:
            assert player.floor < player.projection < player.ceiling
            assert player.stdev > 0


def test_players_without_any_projection_are_listed(slate):
    for player in slate.players:
        player.fd_projection = None
    report = build_consensus(slate, [])
    assert report.players_with_projection == 0
    assert len(report.players_without_projection) > 0
