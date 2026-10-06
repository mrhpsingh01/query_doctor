from querydoctor.candidates import IndexCandidate, generate_candidates, prune_redundant
from querydoctor.candidates.models import IndexColumn
from querydoctor.diagnosis import Finding
from querydoctor.lab.catalog import build_catalog
from querydoctor.schema import IndexInfo, SchemaSnapshot, TableInfo


def table(name, rows=100_000, **columns):
    return TableInfo(name=name, row_estimate=rows, columns=columns or {"id": "integer"})


def shop_schema(indexes=None) -> SchemaSnapshot:
    return SchemaSnapshot(
        tables={
            "orders": table(
                "orders", id="integer", customer_id="integer", total_amount="numeric", created_at="timestamp",
                status="text", currency="text",
            ),
            "customers": table("customers", id="integer", email="text", country="text", segment="text"),
            "payments": table("payments", id="integer", order_id="integer", amount="numeric", method="text"),
        },
        indexes=indexes or {},
    )


def by_ddl(candidates):
    return {c.ddl: c for c in candidates}


HEADLINE = "SELECT id, total_amount FROM orders WHERE customer_id = $1 AND created_at >= $2 ORDER BY created_at DESC"


# ---- generation ---------------------------------------------------------------------------------------------------

def test_headline_query_yields_a_covering_and_a_key_only_candidate():
    found = by_ddl(generate_candidates(HEADLINE, shop_schema(), []))
    covering = found["CREATE INDEX ON orders (customer_id, created_at DESC) INCLUDE (id, total_amount)"]
    key_only = found["CREATE INDEX ON orders (customer_id, created_at DESC)"]
    assert covering.table == "orders"
    assert covering.columns == [IndexColumn(name="customer_id"), IndexColumn(name="created_at", desc=True)]
    assert covering.include == ["id", "total_amount"]
    assert key_only.include == []
    for c in (covering, key_only):
        assert isinstance(c, IndexCandidate)
        assert c.validation == "suggested" and c.rationale and c.predicate is None and c.expression is None


def test_key_columns_are_ordered_equality_then_join_then_range_then_order():
    sql = (
        "SELECT 1 FROM orders o JOIN payments p ON p.order_id = o.id "
        "WHERE o.status = $1 AND o.created_at > $2 ORDER BY o.total_amount"
    )
    orders = [c for c in generate_candidates(sql, shop_schema(), []) if c.table == "orders"]
    assert orders and all([c.name for c in x.columns] == ["status", "id", "created_at", "total_amount"] for x in orders)
    payments = [c for c in generate_candidates(sql, shop_schema(), []) if c.table == "payments"]
    assert [[c.name for c in x.columns] for x in payments] == [["order_id"]]


def test_function_on_column_yields_an_expression_index_tagged_with_the_finding():
    finding = Finding(
        rule_id="PG_FUNCTION_ON_COLUMN", severity="warning", title="t", summary="s",
        evidence={"table": "customers", "function": "lower", "column": "email"}, node_ids=["0"], confidence=0.9,
        next_actions=[],
    )
    (c,) = [x for x in generate_candidates("SELECT id FROM customers WHERE lower(email) = $1", shop_schema(), [finding])]
    assert c.ddl == "CREATE INDEX ON customers (lower(email))"
    assert c.expression == "lower(email)" and c.columns == []
    assert c.source_rule_ids == ["PG_FUNCTION_ON_COLUMN"]


def test_constant_equality_adds_a_partial_index_variant():
    sql = "SELECT id FROM orders WHERE status = 'pending' AND created_at >= $1"
    found = by_ddl(generate_candidates(sql, shop_schema(), []))
    partial = found["CREATE INDEX ON orders (created_at) WHERE status = 'pending'"]
    assert partial.predicate == "status = 'pending'"
    assert "CREATE INDEX ON orders (status, created_at)" in found  # the plain composite is still offered


def test_select_star_gets_no_covering_variant():
    ddls = [c.ddl for c in generate_candidates("SELECT * FROM orders WHERE customer_id = $1", shop_schema(), [])]
    assert ddls == ["CREATE INDEX ON orders (customer_id)"]


def test_candidates_are_stable_unique_and_tied_to_findings_on_their_table():
    finding = Finding(
        rule_id="PG_SEQ_SCAN_FILTER_WASTE", severity="critical", title="t", summary="s", evidence={"table": "orders"},
        node_ids=["0.0"], confidence=0.9, next_actions=[],
    )
    first = generate_candidates(HEADLINE, shop_schema(), [finding])
    second = generate_candidates(HEADLINE, shop_schema(), [finding])
    assert [c.id for c in first] == [c.id for c in second]
    assert len({c.id for c in first}) == len(first)
    assert all(c.source_rule_ids == ["PG_SEQ_SCAN_FILTER_WASTE"] for c in first)


def test_tables_missing_from_the_schema_and_unusable_queries_yield_nothing():
    assert generate_candidates("WITH x AS (SELECT 1 AS a) SELECT a FROM x WHERE a = 1", shop_schema(), []) == []
    assert generate_candidates("SELECT 1", shop_schema(), []) == []
    assert generate_candidates("not sql ((", shop_schema(), []) == []


def test_identifiers_that_need_quoting_are_quoted():
    schema = SchemaSnapshot(tables={"Mixed": table("Mixed", **{"Order ID": "integer"})})
    ddls = [c.ddl for c in generate_candidates('SELECT 1 FROM "Mixed" WHERE "Order ID" = $1', schema, [])]
    assert ddls == ['CREATE INDEX ON "Mixed" ("Order ID")']


def test_every_catalog_query_with_an_expected_candidate_produces_it():
    for q in build_catalog("small"):
        if not q.expected_candidate_table:
            continue
        found = generate_candidates(q.sql, shop_schema(), [])
        names = [([c.name for c in x.columns] or [x.expression]) for x in found if x.table == q.expected_candidate_table]
        assert q.expected_candidate_columns in names, (q.id, names)


# ---- redundancy ---------------------------------------------------------------------------------------------------

def cand(*cols, table_="orders", include=(), predicate=None, expression=None):
    columns = [IndexColumn(name=c) for c in cols]
    ddl = f"CREATE INDEX ON {table_} ({', '.join(cols)})" + (f" INCLUDE ({', '.join(include)})" if include else "")
    return IndexCandidate(
        id=ddl, table=table_, columns=columns, include=list(include), predicate=predicate, expression=expression,
        ddl=ddl, rationale=["r"], source_rule_ids=[], validation="suggested",
    )


def existing(name, cols, table_="orders", include=(), predicate=None, unique=False):
    return IndexInfo(name=name, table=table_, columns=list(cols), include=list(include), predicate=predicate, unique=unique)


def prune(cands, indexes=None, **schema_overrides):
    schema = shop_schema({"orders": indexes} if indexes else {})
    kept, dropped = prune_redundant(cands, schema)
    return kept, {c.ddl: reason for c, reason in dropped}


def test_exact_duplicate_of_an_existing_index_is_dropped():
    kept, dropped = prune([cand("customer_id", "created_at")], [existing("idx_cust", ["customer_id", "created_at"])])
    assert kept == []
    assert "duplicate of existing index idx_cust" in next(iter(dropped.values()))


def test_left_prefix_of_an_existing_index_is_dropped_and_primary_keys_count():
    kept, dropped = prune(
        [cand("customer_id"), cand("id")],
        [existing("idx_cust", ["customer_id", "created_at"]), existing("orders_pkey", ["id"], unique=True)],
    )
    assert kept == []
    assert all("existing index" in r for r in dropped.values())


def test_a_unique_index_already_identifies_a_single_row_so_even_a_covering_variant_is_dropped():
    kept, dropped = prune(
        [cand("id", include=["total_amount"])], [existing("orders_pkey", ["id"], unique=True)]
    )
    assert kept == []
    reason = next(iter(dropped.values()))
    assert "existing index orders_pkey" in reason and "at most one row" in reason


def test_a_candidate_that_adds_covering_columns_is_not_redundant():
    kept, _ = prune(
        [cand("customer_id", include=["total_amount"])], [existing("idx_cust", ["customer_id", "created_at"])]
    )
    assert len(kept) == 1


def test_an_existing_partial_index_does_not_cover_a_plain_candidate():
    kept, _ = prune([cand("customer_id")], [existing("idx_p", ["customer_id"], predicate="status = 'x'")])
    assert len(kept) == 1


def test_a_prefix_of_another_candidate_is_dropped_but_same_key_variants_both_stay():
    short, long = cand("customer_id"), cand("customer_id", "created_at")
    covering = cand("customer_id", "created_at", include=["total_amount"])
    kept, dropped = prune([short, long, covering])
    assert [c.ddl for c in kept] == [long.ddl, covering.ddl]
    assert "covered by candidate" in dropped[short.ddl]


def test_wide_candidates_tiny_tables_and_unknown_columns_are_dropped_with_reasons():
    wide = cand("a", "b", "c", "d", "e", "f", "g")
    tiny_schema = shop_schema()
    tiny_schema.tables["payments"] = table("payments", rows=200, id="integer", order_id="integer")
    tiny = cand("order_id", table_="payments")
    unknown = cand("no_such_column")
    kept, dropped = prune_redundant([wide, tiny, unknown], tiny_schema)
    assert kept == []
    reasons = {c.ddl: r for c, r in dropped}
    assert "too many key columns" in reasons[wide.ddl]
    assert "table too small" in reasons[tiny.ddl]
    assert "unknown column" in reasons[unknown.ddl]
