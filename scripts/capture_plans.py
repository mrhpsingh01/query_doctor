"""Capture real EXPLAIN ANALYZE plans for every catalog query from the running lab into fixtures/postgres_plans.

    python scripts/capture_plans.py            # needs `docker compose up` and a seeded small-scale target
"""

import json
import pathlib
import sys

from querydoctor.db import ExplainFailed, ExplainTimeout, explain, guarded_connection
from querydoctor.lab.catalog import build_catalog

DSN = "postgresql://qd_readonly:qd_readonly@localhost:5433/querydoctor_sample"
OUT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "postgres_plans"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    failures = 0
    with guarded_connection(DSN, statement_timeout_ms=60_000) as conn:
        for query in build_catalog("small"):
            try:
                raw = explain(conn, query.sql, query.params, execute=True)
            except (ExplainFailed, ExplainTimeout) as exc:
                print(f"FAILED {query.id}: {exc}")
                failures += 1
                continue
            path = OUT / f"live_{query.id}.json"
            path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {path.name} ({raw[0]['Execution Time']:.0f} ms)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
