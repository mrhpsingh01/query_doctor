import os

import httpx
import pytest


def pytest_collection_modifyitems(config, items):
    """Stack tests need the api container; skip them unless it answers (set QD_REQUIRE_STACK=1 to fail instead)."""
    if os.environ.get("QD_REQUIRE_STACK"):
        return
    try:
        up = httpx.get("http://127.0.0.1:8010/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        up = False
    if not up:
        skip = pytest.mark.skip(reason="QueryDoctor stack (api :8010) is not running")
        for item in items:
            if "stack" in item.keywords:
                item.add_marker(skip)
