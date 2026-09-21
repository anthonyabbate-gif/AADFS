import io
from pathlib import Path

import pytest

from aadfs.consensus import build_consensus
from aadfs.ingest.fanduel import parse_salary_csv

FIXTURES = Path(__file__).parent / "fixtures"
SALARY_CSV = FIXTURES / "fanduel_salaries_sample.csv"


@pytest.fixture
def slate():
    """A parsed slate with no projections yet."""
    return parse_salary_csv(SALARY_CSV)


@pytest.fixture
def projected_slate(slate):
    """A slate with FanDuel's own averages blended in as the only source."""
    build_consensus(slate, [])
    return slate


@pytest.fixture
def salary_text():
    return SALARY_CSV.read_text()


def csv_io(text: str) -> io.StringIO:
    return io.StringIO(text)
