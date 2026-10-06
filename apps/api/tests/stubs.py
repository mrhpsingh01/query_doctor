"""Test doubles for the target database so pipeline and API tests need neither Postgres nor Redis."""

from __future__ import annotations

import copy
import json
import pathlib
import types
from contextlib import contextmanager

from querydoctor.schema import IndexInfo, SchemaSnapshot, TableInfo

from app.services.events import EventBus
from app.services.pipeline import PipelineDeps

FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures" / "postgres_plans"


def raw_plan(name: str):
    return copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def strip_actuals(raw):
    """What PostgreSQL returns for plain EXPLAIN: the same plan without any measured values."""

    def clean(node):
        for key in [k for k in node if k.startswith("Actual") or k == "Rows Removed by Filter"]:
            node.pop(key)
        for child in node.get("Plans", []):
            clean(child)

    stripped = copy.deepcopy(raw)
    for entry in stripped:
        clean(entry["Plan"])
        entry.pop("Execution Time", None)
    return stripped


def orders_schema() -> SchemaSnapshot:
    return SchemaSnapshot(
        tables={
            "orders": TableInfo(
                name="orders",
                row_estimate=1_824_403,
                columns={"id": "integer", "customer_id": "integer", "total_amount": "numeric", "created_at": "timestamp"},
            )
        },
        indexes={"orders": [IndexInfo(name="orders_pkey", table="orders", columns=["id"], unique=True)]},
    )


class StubTarget:
    """Programmable stand-in for the target database."""

    def __init__(self, plan: str = "seqscan_filter_waste"):
        self.raw = raw_plan(plan)
        self.schema = orders_schema()
        self.explain_error: Exception | None = None
        self.on_schema = None  # callback run inside collect_schema
        self.planner_only_raw = None  # returned instead of `raw` for execute=False when set
        self.explain_calls: list[dict] = []

    @contextmanager
    def connect(self, dsn, **kwargs):
        yield types.SimpleNamespace(info=types.SimpleNamespace(backend_pid=4242))

    def collect_schema(self, conn, tables):
        if self.on_schema:
            self.on_schema()
        return self.schema

    def explain(self, conn, sql, params, *, execute):
        self.explain_calls.append({"sql": sql, "params": list(params), "execute": execute})
        if self.explain_error:
            raise self.explain_error
        if not execute and self.planner_only_raw is not None:
            return self.planner_only_raw
        return self.raw

    def deps(self, bus: EventBus, **overrides) -> PipelineDeps:
        return PipelineDeps(
            target_dsn="stub",
            bus=bus,
            connect=self.connect,
            explain=self.explain,
            collect_schema=self.collect_schema,
            **overrides,
        )
