"""Shared helpers for tests that talk to the live QueryDoctor stack (api on :8010)."""

import time

import pytest

BASE = "http://localhost:8010"
TERMINAL = {"completed", "failed", "cancelled"}


def project_id(http) -> int:
    return http.get("/api/projects").json()[0]["id"]


def analyse(http, sql, params, *, execute=False, timeout=90) -> dict:
    response = http.post(
        "/api/analyses", json={"sql": sql, "params": params, "execute": execute, "project_id": project_id(http)}
    )
    assert response.status_code == 202, response.text
    analysis_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = http.get(f"/api/analyses/{analysis_id}").json()
        if body["status"] in TERMINAL:
            return body
        time.sleep(0.5)
    pytest.fail(f"analysis {analysis_id} did not finish in {timeout}s")


def plan_nodes(node):
    yield node
    for child in node["children"]:
        yield from plan_nodes(child)
