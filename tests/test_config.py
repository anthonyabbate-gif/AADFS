"""Data paths. A board written into an image instead of a volume is data lost."""

import json
import os
import subprocess
import sys

from aadfs import config


def _in_fresh_process(script: str, env_extra: dict) -> dict:
    """Run a snippet in a new interpreter and return its JSON output.

    Several modules resolve their paths at import time, which is exactly the
    behaviour worth testing -- but reloading them inside the test session
    replaces module objects other tests have already imported and patched. A
    separate process gives real isolation, and matches how a container starts.
    """
    env = dict(os.environ)
    env.update(env_extra)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_defaults_are_unchanged_without_the_env_var(monkeypatch):
    monkeypatch.delenv(config.DATA_ENV, raising=False)
    monkeypatch.delenv(config.DB_ENV, raising=False)
    assert str(config.cache_dir()) == "data/cache"
    assert str(config.rankings_dir()) == "data/rankings"
    assert str(config.database_path()) == "aadfs.db"


def test_data_root_moves_every_write_path(monkeypatch, tmp_path):
    monkeypatch.setenv(config.DATA_ENV, str(tmp_path))
    monkeypatch.delenv(config.DB_ENV, raising=False)
    for path in (config.cache_dir(), config.rankings_dir(),
                 config.salaries_dir(), config.projections_dir(),
                 config.database_path()):
        assert str(path).startswith(str(tmp_path)), path


def test_database_can_be_placed_independently(monkeypatch, tmp_path):
    monkeypatch.setenv(config.DATA_ENV, str(tmp_path))
    monkeypatch.setenv(config.DB_ENV, "/elsewhere/custom.db")
    assert str(config.database_path()) == "/elsewhere/custom.db"


def test_ensure_dirs_creates_everything(monkeypatch, tmp_path):
    monkeypatch.setenv(config.DATA_ENV, str(tmp_path / "root"))
    config.ensure_dirs()
    for path in (config.cache_dir(), config.rankings_dir(),
                 config.salaries_dir(), config.projections_dir()):
        assert path.is_dir()


_RESOLVE_SCRIPT = """
import json
from aadfs.rankings.pipeline import DEFAULT_CACHE_DIR, DEFAULT_OUTPUT_DIR, WEIGHTS_FILE
from aadfs.rankings.sources import RecentFormModel
from aadfs.store import DEFAULT_DB, Store
from aadfs.sources.base import DEFAULT_CACHE_DIR as BASE_CACHE

store = Store()
print(json.dumps({
    "rankings_out": str(DEFAULT_OUTPUT_DIR),
    "weights": str(WEIGHTS_FILE),
    "rankings_cache": str(DEFAULT_CACHE_DIR),
    "base_cache": str(BASE_CACHE),
    "db_default": str(DEFAULT_DB),
    "store_path": str(store.path),
    "store_exists": store.path.exists(),
    "recent_form_cache": str(RecentFormModel().cache_dir),
}))
"""


def test_modules_resolve_their_paths_under_the_data_root(tmp_path):
    """Import-time defaults must follow AADFS_DATA, or a volume mount is useless."""
    env = {"AADFS_DATA": str(tmp_path)}
    env.pop("AADFS_DB", None)
    resolved = _in_fresh_process(_RESOLVE_SCRIPT, {**env, "AADFS_DB": ""})
    for key, value in resolved.items():
        if key == "store_exists":
            assert value is True
            continue
        assert value.startswith(str(tmp_path)), f"{key} escaped the data root: {value}"


def test_without_the_env_var_a_fresh_process_keeps_the_old_layout(tmp_path):
    resolved = _in_fresh_process(_RESOLVE_SCRIPT, {"AADFS_DATA": "", "AADFS_DB": ""})
    assert resolved["rankings_out"] == "data/rankings"
    assert resolved["db_default"] == "aadfs.db"


def test_a_blank_env_var_is_treated_as_unset(monkeypatch):
    """Compose writes ${VAR:-} as an empty string; that must not mean '.'."""
    monkeypatch.setenv(config.DATA_ENV, "")
    monkeypatch.setenv(config.DB_ENV, "   ")
    assert str(config.data_root()) == "data"
    assert str(config.database_path()) == "aadfs.db"
