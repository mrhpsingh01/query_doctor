import copy
import json
import pathlib

import psycopg
import pytest

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "postgres_plans"

from labdsn import TARGET_ADMIN_DSN  # noqa: E402


@pytest.fixture
def load_plan():
    """Return a fresh deep copy of a committed plan fixture so tests can mutate it."""

    def _load(name: str):
        return copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))

    return _load


_lab_up: bool | None = None


def _lab_reachable() -> bool:
    global _lab_up
    if _lab_up is None:
        try:
            with psycopg.connect(TARGET_ADMIN_DSN, connect_timeout=2):
                _lab_up = True
        except Exception:
            _lab_up = False
    return _lab_up


def pytest_collection_modifyitems(config, items):
    """Integration tests need the Docker lab; skip them (visibly) when it is not running."""
    for item in items:
        if "integration" in item.keywords and not _lab_reachable():
            item.add_marker(pytest.mark.skip(reason="Docker lab (target-postgres :5433) is not reachable"))
