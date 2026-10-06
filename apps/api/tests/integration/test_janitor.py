"""A crashed worker can leave a benchmark sandbox behind; the janitor removes stale ones when a worker starts."""

import psycopg
import pytest
from querydoctor.benchmark.sandbox import Sandbox

from app.workers.janitor import cleanup_stale_sandboxes

pytestmark = pytest.mark.integration

BENCH = "postgresql://qd_bench:qd_bench@127.0.0.1:5434/postgres"


def leftovers() -> list[str]:
    with psycopg.connect(BENCH, autocommit=True) as conn:
        return [r[0] for r in conn.execute(r"SELECT datname FROM pg_database WHERE datname LIKE 'bench\_%'")]


def test_a_sandbox_abandoned_by_a_crashed_worker_is_removed_but_a_fresh_one_is_not():
    abandoned = Sandbox(BENCH)
    abandoned.create()  # ... and the process "crashes" before drop()
    assert cleanup_stale_sandboxes(BENCH, max_age_s=3600) == 0  # still young: a benchmark may be running right now
    assert len(leftovers()) == 1
    assert cleanup_stale_sandboxes(BENCH, max_age_s=0) == 1
    assert leftovers() == []


def test_the_janitor_never_raises_when_the_bench_database_is_unreachable():
    assert cleanup_stale_sandboxes("postgresql://qd_bench:qd_bench@127.0.0.1:1/postgres", max_age_s=0) == 0
