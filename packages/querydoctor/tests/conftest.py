import copy
import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "postgres_plans"


@pytest.fixture
def load_plan():
    """Return a fresh deep copy of a committed plan fixture so tests can mutate it."""

    def _load(name: str):
        return copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))

    return _load
