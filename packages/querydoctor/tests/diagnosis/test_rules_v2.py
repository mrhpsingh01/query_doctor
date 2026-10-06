import json
import pathlib

import pytest
from helpers import make_ctx, node

from querydoctor.diagnosis import ALL_RULES, AnalysisContext, run_diagnosis
from querydoctor.plan_parser import parse_explain
from querydoctor.safety import validate_query
from querydoctor.schema import IndexInfo, SchemaSnapshot, TableInfo

RULES = {r.rule_id: r for r in ALL_RULES}
FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "postgres_plans"


def only(rule_id, root, **kw):
    """Run exactly one rule through the engine; returns all of its findings."""
    return run_diagnosis(make_ctx(root, **kw), rules=[RULES[rule_id]])


def schema_with(table, *indexes: tuple[str, list[str]]) -> SchemaSnapshot:
    return SchemaSnapshot(
        tables={table: TableInfo(name=table, row_estimate=10_000)},
        indexes={table: [IndexInfo(name=n, table=table, columns=c) for n, c in indexes]},
    )


def test_all_ten_rule_ids_are_registered():
    assert {r.rule_id for r in ALL_RULES} == {
        "PG_SEQ_SCAN_FILTER_WASTE", "PG_HIGH_FILTER_WASTE", "PG_CARDINALITY_MISESTIMATE", "PG_REPEATED_NESTED_LOOP",
        "PG_SORT_SPILL", "PG_HASH_SPILL", "PG_INDEX_FILTER_REMAINDER", "PG_FUNCTION_ON_COLUMN", "PG_WIDE_RESULT",
        "PG_DUPLICATE_SUBTREE",
    }


# ---- PG_HASH_SPILL ------------------------------------------------------------------------------------------------

def test_hash_spill_on_multiple_batches():
    root = node("Hash", hash_batches=4, temp_written=2000, **{"Peak Memory Usage": 4096})
    (f,) = only("PG_HASH_SPILL", root, execution_time=1000.0)  # the hash is a small part of a 1 s query
    assert f.severity == "warning" and f.node_ids == ["0"]
    assert f.evidence["hash_batches"] == 4 and f.evidence["peak_memory_kb"] == 4096


@pytest.mark.parametrize("kwargs", [dict(hash_batches=1), dict(), dict(hash_batches=1, temp_written=0)])
def test_hash_in_memory_is_silent(kwargs):
    assert only("PG_HASH_SPILL", node("Hash", **kwargs)) == []


def test_hash_join_is_not_double_reported_when_its_hash_child_spilled():
    root = node("Hash Join", temp_written=900, plans=[node("Seq Scan"), node("Hash", hash_batches=8)])
    findings = [f for f in run_diagnosis(make_ctx(root)) if f.rule_id == "PG_HASH_SPILL"]
    assert [f.node_ids for f in findings] == [["0.1"]]


def test_hash_join_with_temp_blocks_and_no_spilling_hash_child_is_reported():
    root = node("Hash Join", temp_written=900, plans=[node("Seq Scan"), node("Hash", hash_batches=1)])
    findings = [f for f in run_diagnosis(make_ctx(root)) if f.rule_id == "PG_HASH_SPILL"]
    assert [f.node_ids for f in findings] == [["0"]]


# ---- PG_INDEX_FILTER_REMAINDER ------------------------------------------------------------------------------------

def index_scan(**kw):
    base = dict(index="idx_orders_customer", relation="orders", index_cond="(customer_id = 4242)",
                filter="(status = 'pending'::text)", rows=100, removed=9_900)
    return node("Index Scan", **{**base, **kw})


def test_index_scan_still_filtering_suggests_a_composite_index():
    (f,) = only("PG_INDEX_FILTER_REMAINDER", index_scan())
    assert f.evidence["filter_columns"] == ["status"]
    assert f.evidence["index_condition_columns"] == ["customer_id"]
    assert f.evidence["rows_removed"] == 9_900
    assert any("composite" in a or "partial" in a for a in f.next_actions)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(filter="(customer_id > 5)"),  # filters only on columns the index condition already uses
        dict(rows=700, removed=300),  # waste 30%
        dict(rows=10, removed=90),  # too few rows to matter
        dict(filter=None),
    ],
)
def test_index_filter_remainder_negative(kwargs):
    assert only("PG_INDEX_FILTER_REMAINDER", index_scan(**kwargs)) == []


def test_index_filter_remainder_supersedes_the_generic_filter_waste_finding():
    ids = [f.rule_id for f in run_diagnosis(make_ctx(index_scan()))]
    assert "PG_INDEX_FILTER_REMAINDER" in ids
    assert "PG_HIGH_FILTER_WASTE" not in ids


# ---- PG_FUNCTION_ON_COLUMN ----------------------------------------------------------------------------------------

def scan_with_filter(filter_text, relation="customers"):
    return node("Seq Scan", relation=relation, filter=filter_text, rows=1, removed=9_999)


def test_function_on_indexed_column_is_flagged_with_the_blocked_index():
    root = scan_with_filter("(lower(email) = 'user42@example.com'::text)")
    (f,) = only("PG_FUNCTION_ON_COLUMN", root, schema=schema_with("customers", ("idx_customers_email", ["email"])))
    assert f.evidence["function"] == "lower" and f.evidence["column"] == "email"
    assert f.evidence["blocked_index"] == "idx_customers_email"
    assert any("expression index" in a and "lower(email)" in a for a in f.next_actions)


def test_cast_on_a_column_is_flagged():
    root = scan_with_filter("((id)::text = '5'::text)")
    (f,) = only("PG_FUNCTION_ON_COLUMN", root, schema=schema_with("customers", ("customers_pkey", ["id"])))
    assert f.evidence["function"] == "cast" and f.evidence["column"] == "id"


def test_date_trunc_on_a_column_is_flagged():
    root = scan_with_filter("(date_trunc('day'::text, created_at) = '2023-01-01 00:00:00'::timestamp without time zone)", "orders")
    (f,) = only("PG_FUNCTION_ON_COLUMN", root, schema=schema_with("orders", ("idx_created", ["created_at"])))
    assert f.evidence["function"] == "date_trunc" and f.evidence["column"] == "created_at"


@pytest.mark.parametrize(
    "filter_text,schema",
    [
        ("(lower(email) = 'x'::text)", SchemaSnapshot()),  # no index on the column: nothing is being blocked
        ("(lower(email) = 'x'::text)", schema_with("customers", ("idx_other", ["country"]))),
        ("(status = 'x'::text)", schema_with("customers", ("idx_status", ["status"]))),  # cast of a literal only
        ("(email = 'x'::text)", schema_with("customers", ("idx_customers_email", ["email"]))),
        ("(this is not sql ((", schema_with("customers", ("idx_customers_email", ["email"]))),
    ],
)
def test_function_on_column_negative(filter_text, schema):
    assert only("PG_FUNCTION_ON_COLUMN", scan_with_filter(filter_text), schema=schema) == []


def test_function_on_column_only_looks_at_seq_scans():
    root = node("Index Scan", relation="customers", filter="(lower(email) = 'x'::text)")
    assert only("PG_FUNCTION_ON_COLUMN", root, schema=schema_with("customers", ("i", ["email"]))) == []


# ---- PG_WIDE_RESULT -----------------------------------------------------------------------------------------------

STAR = validate_query("SELECT * FROM orders")
NO_STAR = validate_query("SELECT id FROM orders")


def test_select_star_with_wide_rows_is_flagged_by_severity():
    (info,) = only("PG_WIDE_RESULT", node("Seq Scan", width=250, output=["a"] * 12), query=STAR)
    (warn,) = only("PG_WIDE_RESULT", node("Seq Scan", width=450), query=STAR)
    assert info.severity == "info" and warn.severity == "warning"
    assert info.evidence["plan_width"] == 250 and info.evidence["output_columns"] == 12


def test_select_star_across_many_columns_is_flagged_even_when_the_rows_are_narrow():
    (f,) = only("PG_WIDE_RESULT", node("Gather", width=135, output=[f"t.c{i}" for i in range(21)]), query=STAR)
    assert f.severity == "info" and f.evidence["output_columns"] == 21
    assert "21 columns" in f.summary
    assert only("PG_WIDE_RESULT", node("Gather", width=135, output=["a"] * 8), query=STAR) == []


@pytest.mark.parametrize(
    "query,width",
    [(STAR, 100), (NO_STAR, 450), (None, 450)],
)
def test_wide_result_negative(query, width):
    assert only("PG_WIDE_RESULT", node("Seq Scan", width=width), query=query) == []


def test_wide_result_only_considers_the_final_row_width():
    root = node("Sort", width=40, plans=[node("Seq Scan", width=500)])
    assert only("PG_WIDE_RESULT", root, query=STAR) == []


# ---- PG_DUPLICATE_SUBTREE -----------------------------------------------------------------------------------------

def dup_scan(filter_text="(status = 'x'::text)", cost=5_000.0):
    return node("Seq Scan", relation="orders", filter=filter_text, cost=cost)


def test_identical_expensive_subtrees_are_reported_once_with_all_locations():
    root = node("Append", plans=[dup_scan(), dup_scan()])
    (f,) = only("PG_DUPLICATE_SUBTREE", root)
    assert f.node_ids == ["0.0", "0.1"]
    assert f.evidence["occurrences"] == 2 and f.evidence["table"] == "orders"


@pytest.mark.parametrize(
    "children",
    [
        [dup_scan(), dup_scan("(status = 'y'::text)")],  # different predicate
        [dup_scan()],  # only one occurrence
        [dup_scan(cost=10.0), dup_scan(cost=10.0)],  # too cheap to matter
    ],
)
def test_duplicate_subtree_negative(children):
    assert only("PG_DUPLICATE_SUBTREE", node("Append", plans=children)) == []


def test_nested_duplicates_are_reported_at_the_largest_matching_subtree_only():
    branch = lambda: node("Sort", plans=[dup_scan()])  # noqa: E731
    findings = only("PG_DUPLICATE_SUBTREE", node("Append", plans=[branch(), branch()]))
    assert [f.node_ids for f in findings] == [["0.0", "0.1"]]


# ---- robustness ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_every_fixture_plan_diagnoses_without_crashing(path):
    plan = parse_explain(json.loads(path.read_text(encoding="utf-8")))
    run_diagnosis(AnalysisContext(plan=plan, query=STAR))
    for rule in ALL_RULES:  # and no rule raises when called directly (the engine would otherwise hide it)
        for n in __import__("querydoctor.plan_parser", fromlist=["iter_nodes"]).iter_nodes(plan.root):
            rule.evaluate(n, AnalysisContext(plan=plan, query=STAR))


def test_zero_row_estimates_never_divide_by_zero():
    root = node("Index Scan", est_rows=0, rows=0, removed=0, filter="(x = 1)", index_cond="(y = 2)", loops=1)
    run_diagnosis(make_ctx(root, query=STAR))


# ---- review finding I3: spill rules must use exclusive temp counts ----------------------------------------------------

def test_an_in_memory_sort_above_a_spilling_hash_join_is_not_reported_as_a_sort_spill():
    join = node("Hash Join", temp_written=900, plans=[node("Seq Scan"), node("Hash", hash_batches=1)])
    root = node("Sort", sort_method="quicksort", temp_written=900, plans=[join])
    findings = run_diagnosis(make_ctx(root, execution_time=1000.0))
    spill_ids = {(f.rule_id, tuple(f.node_ids)) for f in findings if f.rule_id in ("PG_SORT_SPILL", "PG_HASH_SPILL")}
    assert spill_ids == {("PG_HASH_SPILL", ("0.0",))}


def test_a_hash_join_above_a_spilling_sort_is_not_reported_as_a_hash_spill():
    sort = node("Sort", sort_method="external merge", temp_written=700)
    root = node("Hash Join", temp_written=700, plans=[sort, node("Hash", hash_batches=1)])
    ids = {(f.rule_id, tuple(f.node_ids)) for f in run_diagnosis(make_ctx(root, execution_time=1000.0))
           if f.rule_id in ("PG_SORT_SPILL", "PG_HASH_SPILL")}
    assert ids == {("PG_SORT_SPILL", ("0.0",))}


def test_parallel_plans_attribute_time_to_the_right_node():
    scan = node("Seq Scan", time=90.0, loops=3, relation="orders", rows=1000)
    root = node("Gather", time=100.0, plans=[scan], **{"Workers Launched": 2})
    ctx = make_ctx(root, execution_time=100.0)
    assert ctx.plan.root.children[0].exclusive_time_ms == pytest.approx(90.0)


# ---- review finding I6: diagnosis must stay fast on large plans -------------------------------------------------------

def _join_chain(depth: int) -> dict:
    root = node("Seq Scan", relation="t0", cost=5_000.0, filter="(a = 0)")
    for i in range(1, depth + 1):
        root = node("Nested Loop", cost=5_000.0 + i, plans=[node("Seq Scan", relation=f"t{i}", cost=5_000.0, filter=f"(a = {i})"), root])
    return root


@pytest.mark.parametrize("depth", [150])
def test_diagnosing_a_deep_plan_takes_seconds_not_minutes(depth):
    import time

    ctx = make_ctx(_join_chain(depth), execution_time=1000.0)
    started = time.monotonic()
    run_diagnosis(ctx)
    assert time.monotonic() - started < 3.0
