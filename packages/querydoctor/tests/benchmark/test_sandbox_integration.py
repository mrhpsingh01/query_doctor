import psycopg
import pytest
from labdsn import BENCH_ADMIN_DSN

from querydoctor.benchmark.sandbox import Sandbox, drop_stale

pytestmark = pytest.mark.integration


def databases() -> list[str]:
    with psycopg.connect(BENCH_ADMIN_DSN, autocommit=True) as conn:
        return [r[0] for r in conn.execute("SELECT datname FROM pg_database WHERE datname LIKE 'bench\\_%' ORDER BY 1")]


@pytest.fixture(autouse=True)
def no_leftovers():
    assert databases() == [], "a previous test leaked a sandbox"
    yield
    drop_stale(BENCH_ADMIN_DSN, max_age_s=0)  # keep the shared bench instance clean even if a test failed


def test_create_clones_the_template_and_drop_removes_it():
    sandbox = Sandbox(BENCH_ADMIN_DSN)
    name = sandbox.create()
    assert name.startswith("bench_") and name in databases()
    with psycopg.connect(sandbox.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 100_000
    sandbox.drop()
    assert databases() == []
    sandbox.drop()  # idempotent


def test_drop_terminates_sessions_that_are_still_connected():
    with Sandbox(BENCH_ADMIN_DSN) as sandbox:
        busy = psycopg.connect(sandbox.dsn)
        busy.execute("SELECT 1")
    assert databases() == []
    busy.close()


def test_the_context_manager_drops_the_sandbox_when_the_body_raises():
    with pytest.raises(RuntimeError):
        with Sandbox(BENCH_ADMIN_DSN):
            assert len(databases()) == 1
            raise RuntimeError("boom")
    assert databases() == []


def test_the_template_is_never_modified_by_work_in_a_sandbox():
    def template_state():
        with psycopg.connect(BENCH_ADMIN_DSN.replace("/postgres", "/querydoctor_sample")) as conn:
            return (
                conn.execute("SELECT count(*) FROM orders").fetchone()[0],
                sorted(r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")),
            )

    before = template_state()
    with Sandbox(BENCH_ADMIN_DSN) as sandbox:
        with psycopg.connect(sandbox.dsn, autocommit=True) as conn:
            conn.execute("CREATE INDEX ON orders (customer_id)")
            conn.execute("DELETE FROM payments")
    assert template_state() == before


def test_drop_stale_removes_only_old_sandboxes():
    old = Sandbox(BENCH_ADMIN_DSN)
    old.create()
    assert drop_stale(BENCH_ADMIN_DSN, max_age_s=3600) == 0  # too young to be stale
    assert len(databases()) == 1
    assert drop_stale(BENCH_ADMIN_DSN, max_age_s=0) == 1
    assert databases() == []
