import pytest
from labdsn import TARGET_RO_DSN

from querydoctor.candidates import IndexCandidate, IndexColumn, score_candidates
from querydoctor.candidates.models import IndexCandidate as Candidate
from querydoctor.db import explain, guarded_connection
from querydoctor.lab.catalog import build_catalog
from querydoctor.plan_parser import iter_nodes, parse_explain

pytestmark = pytest.mark.integration


def candidate(ddl, table="orders", columns=("customer_id",), include=()):
    return Candidate(
        id=IndexCandidate.make_id(ddl), table=table, columns=[IndexColumn(name=c) for c in columns],
        include=list(include), ddl=ddl, rationale=["test"], source_rule_ids=[], validation="suggested",
    )


@pytest.fixture
def conn():
    with guarded_connection(TARGET_RO_DSN) as c:
        yield c


def hypothetical_count(conn) -> int:
    n = conn.execute("SELECT count(*) FROM hypopg_list_indexes").fetchone()[0]
    conn.rollback()
    return n


def real_index_names(conn) -> list[str]:
    names = [r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1")]
    conn.rollback()
    return names


def test_the_planner_picks_a_hypothetical_index_and_nothing_real_is_created(conn):
    q = build_catalog("small")[0]
    original = parse_explain(explain(conn, q.sql, q.params, execute=False))
    before = real_index_names(conn)
    good = candidate("CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)",
                     columns=("customer_id", "created_at"), include=("id", "total_amount"))
    # Callers always pass table sizes (from the schema snapshot); without them a Seq Scan's "rows read" is unknown.
    (scored,) = score_candidates(conn, q.sql, q.params, original, [good], table_rows={"orders": 100_000})
    assert scored.error is None
    assert scored.comparison.uses_candidate is True
    assert scored.comparison.cost_reduction > 0.4  # an index scan or a bitmap scan, depending on fresh statistics
    assert scored.comparison.planner_score > 0.5
    types = {n.node_type for n in iter_nodes(scored.plan.root)}
    assert types & {"Index Scan", "Index Only Scan", "Bitmap Heap Scan"}
    assert "Seq Scan" in scored.comparison.removed_operators
    assert hypothetical_count(conn) == 0
    assert real_index_names(conn) == before


def test_an_irrelevant_candidate_is_not_used_by_the_planner(conn):
    q = build_catalog("small")[0]
    original = parse_explain(explain(conn, q.sql, q.params, execute=False))
    useless = candidate("CREATE INDEX ON payments (method)", table="payments", columns=("method",))
    (scored,) = score_candidates(conn, q.sql, q.params, original, [useless])
    assert scored.comparison.uses_candidate is False and scored.comparison.planner_score == 0.0


def test_a_broken_candidate_is_reported_and_still_resets_the_hypothetical_indexes(conn):
    q = build_catalog("small")[0]
    original = parse_explain(explain(conn, q.sql, q.params, execute=False))
    good = candidate("CREATE INDEX ON orders (customer_id)")
    broken = candidate("CREATE INDEX ON orders (no_such_column)", columns=("no_such_column",))
    results = score_candidates(conn, q.sql, q.params, original, [broken, good])
    assert results[0].error and results[0].comparison is None
    assert results[1].error is None and results[1].comparison.uses_candidate is True
    assert hypothetical_count(conn) == 0


def test_expression_and_partial_candidates_can_be_tested(conn):
    sql = "SELECT id, email FROM customers WHERE lower(email) = $1"
    original = parse_explain(explain(conn, sql, ["user42@example.com"], execute=False))
    expr = Candidate(
        id="e", table="customers", expression="lower(email)", ddl="CREATE INDEX ON customers (lower(email))",
        rationale=["x"], source_rule_ids=[], validation="suggested",
    )
    (scored,) = score_candidates(conn, sql, ["user42@example.com"], original, [expr], table_rows={"customers": 10_000})
    assert scored.error is None and scored.comparison.uses_candidate is True
