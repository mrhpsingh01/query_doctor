import json
import pathlib

import pytest
from labdsn import MONGO_RO_URI
from pymongo.errors import ExecutionTimeout, OperationFailure, ServerSelectionTimeoutError

from querydoctor.db.errors import ExplainFailed, ExplainTimeout
from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.mongo import build_command, explain_query, normalize_plan, validate_mongo
from querydoctor.plan_parser import ParsedPlan, iter_nodes

FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "mongo_plans"


def load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def types(plan: ParsedPlan) -> list[str]:
    return [n.node_type for n in iter_nodes(plan.root)]


def test_a_blocking_sort_over_an_index_scan_becomes_a_tree_with_work_counters():
    raw = load("mongo_blocking_sort.stats")
    plan = normalize_plan(raw)
    assert isinstance(plan, ParsedPlan) and plan.engine == "mongodb" and plan.executed is True
    assert types(plan) == ["SORT", "FETCH", "IXSCAN"]
    sort, fetch, scan = list(iter_nodes(plan.root))
    assert (sort.node_id, fetch.node_id, scan.node_id) == ("0", "0.0", "0.0.0") and fetch.parent_id == "0" and scan.parent_id == "0.0"
    stats = raw["executionStats"]
    assert sort.actual_rows == stats["nReturned"] and plan.execution_time_ms == stats["executionTimeMillis"]
    assert fetch.docs_examined == stats["executionStages"]["inputStage"]["docsExamined"] and fetch.docs_examined > 100_000
    assert scan.keys_examined == stats["totalKeysExamined"] and scan.index_name in ("event_type_1", "event_type_1_created_at_1")
    assert sort.sort_method == "memory" and sort.io_pressure == "none"
    assert plan.root.estimated_cost == 0 and all(n.estimated_rows == 0 for n in iter_nodes(plan.root))
    assert plan.planning_time_ms is None


def test_stage_times_are_cumulative_so_exclusive_time_is_the_difference():
    plan = normalize_plan(load("mongo_blocking_sort.stats"))
    sort, fetch, scan = list(iter_nodes(plan.root))
    assert sort.total_time_ms == sort.actual_time_ms and sort.exclusive_time_ms == pytest.approx(max(sort.total_time_ms - fetch.total_time_ms, 0))
    assert fetch.exclusive_time_ms == pytest.approx(max(fetch.total_time_ms - scan.total_time_ms, 0))
    assert scan.exclusive_time_ms == scan.total_time_ms
    assert all(n.exclusive_time_ms >= 0 for n in iter_nodes(plan.root))


def test_a_collection_scan_names_its_collection_and_reports_how_much_it_threw_away():
    plan = normalize_plan(load("mongo_collscan_by_customer.stats"))
    scan = next(n for n in iter_nodes(plan.root) if n.node_type == "COLLSCAN")
    assert scan.relation_name == "orders" and scan.docs_examined >= 100_000 and scan.actual_rows < 5_000
    assert scan.rows_removed_by_filter == scan.docs_examined - scan.actual_rows
    assert scan.filter_waste > 0.95 and scan.filter_condition and "customer_id" in scan.filter_condition


def test_an_index_scan_carries_its_name_and_bounds():
    plan = normalize_plan(load("mongo_range_before_equality.stats"))
    scan = next(n for n in iter_nodes(plan.root) if n.node_type == "IXSCAN")
    assert scan.index_name == "created_at_1_customer_id_1" and scan.index_condition and scan.keys_examined > 10_000


def test_a_planner_only_explain_is_not_executed_and_has_no_measurements():
    plan = normalize_plan(load("mongo_collscan_by_customer.planner"))
    assert plan.executed is False and plan.execution_time_ms is None
    assert all(n.actual_rows is None and n.actual_time_ms is None and n.docs_examined is None for n in iter_nodes(plan.root))
    assert "COLLSCAN" in types(plan)


def test_an_aggregation_becomes_a_chain_from_the_last_stage_to_the_cursor_and_its_plan():
    plan = normalize_plan(load("mongo_unbounded_group.stats"))
    assert types(plan) == ["$group", "$cursor", "PROJECTION_SIMPLE", "COLLSCAN"]
    root = plan.root
    assert root.actual_rows == 10_000 and root.node_id == "0" and root.children[0].node_type == "$cursor"
    cursor = root.children[0]
    assert cursor.actual_rows == 500_000 and cursor.children[0].node_id == "0.0.0"
    assert root.exclusive_time_ms == pytest.approx(max(root.total_time_ms - cursor.total_time_ms, 0))
    assert plan.executed is True and plan.execution_time_ms is not None


def test_lookup_stages_expose_the_documents_they_scanned_and_the_scan_count():
    plan = normalize_plan(load("mongo_lookup_without_index.stats"))
    lookup = next(n for n in iter_nodes(plan.root) if n.node_type == "$lookup")
    assert lookup.docs_examined > 1_000_000 and lookup.keys_examined == 0
    assert lookup.raw["collectionScans"] > 0 and lookup.raw["indexesUsed"] == []


@pytest.mark.parametrize("query", MONGO_CATALOG, ids=lambda q: q.id)
def test_every_captured_catalog_plan_normalizes_in_both_verbosities(query):
    for suffix, executed in (("stats", True), ("planner", False)):
        plan = normalize_plan(load(f"{query.id}.{suffix}"))
        assert plan.executed is executed and plan.engine == "mongodb" and plan.fingerprint
        assert [n.node_id for n in iter_nodes(plan.root)][0] == "0"
        assert len({n.node_id for n in iter_nodes(plan.root)}) == len(types(plan))


def test_the_fingerprint_depends_on_the_shape_not_the_measurements():
    a, b = normalize_plan(load("mongo_collscan_by_customer.stats")), normalize_plan(load("mongo_collscan_by_customer.planner"))
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != normalize_plan(load("mongo_blocking_sort.stats")).fingerprint


def test_or_plans_with_several_input_stages_keep_all_branches():
    raw = {"queryPlanner": {"namespace": "db.orders", "winningPlan": {"stage": "FETCH", "inputStage": {"stage": "OR", "inputStages": [
        {"stage": "IXSCAN", "indexName": "a_1"}, {"stage": "IXSCAN", "indexName": "b_1"}]}}}}
    plan = normalize_plan(raw)
    assert types(plan) == ["FETCH", "OR", "IXSCAN", "IXSCAN"]
    assert [n.node_id for n in iter_nodes(plan.root)] == ["0", "0.0", "0.0.0", "0.0.1"]


def test_sharded_plans_show_each_shards_plan():
    raw = {"queryPlanner": {"namespace": "db.orders", "winningPlan": {"stage": "SINGLE_SHARD", "shards": [
        {"shardName": "s1", "winningPlan": {"stage": "COLLSCAN", "direction": "forward"}}]}}}
    plan = normalize_plan(raw)
    assert types(plan) == ["SINGLE_SHARD", "COLLSCAN"] and plan.root.children[0].relation_name == "orders"


def test_slot_based_plans_are_read_through_their_logical_plan():
    raw = {"queryPlanner": {"namespace": "db.orders", "winningPlan": {"queryPlan": {"stage": "GROUP", "inputStage": {"stage": "COLLSCAN"}}, "slotBasedPlan": {"slots": "..."}}},
           "executionStats": {"executionTimeMillis": 12, "executionStages": {"queryPlan": {"stage": "GROUP", "inputStage": {"stage": "COLLSCAN"}}, "nReturned": 4, "executionTimeMillisEstimate": 11}}}
    plan = normalize_plan(raw)
    assert types(plan) == ["GROUP", "COLLSCAN"] and plan.root.actual_rows == 4 and plan.execution_time_ms == 12


def test_missing_fields_are_tolerated_and_unusable_input_is_refused_clearly():
    sparse = normalize_plan({"queryPlanner": {"winningPlan": {"stage": "COLLSCAN"}}})
    assert sparse.root.node_type == "COLLSCAN" and sparse.root.docs_examined is None and sparse.root.relation_name is None
    for bad in ({}, {"ok": 1}, {"queryPlanner": {}}, {"stages": []}, "not json", [1], 5):
        with pytest.raises(ValueError):
            normalize_plan(bad)


def test_plans_can_be_passed_as_json_text():
    from_text = normalize_plan(json.dumps(load("mongo_collscan_by_customer.stats")))
    assert from_text.engine == "mongodb" and from_text == normalize_plan(load("mongo_collscan_by_customer.stats"))


# ---- the explain command --------------------------------------------------------------------------------------------

class FakeDb:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.commands = result or {}, error, []

    def command(self, cmd):
        self.commands.append(cmd)
        if self.error:
            raise self.error
        return self.result


def vq(text, params=()):
    return validate_mongo(text, list(params))


def test_a_find_is_explained_with_the_requested_verbosity_and_a_time_limit():
    q = vq('{"find": "orders", "filter": {"customer_id": {"$param": 1}}, "sort": {"created_at": -1}, "limit": 5}', [7])
    db = FakeDb({"queryPlanner": {}})
    explain_query(db, q, [7], execute=False)
    explain_query(db, q, [7], execute=True)
    first, second = db.commands
    assert first["verbosity"] == "queryPlanner" and second["verbosity"] == "executionStats"
    assert first["explain"] == {"find": "orders", "filter": {"customer_id": 7}, "sort": {"created_at": -1}, "limit": 5, "maxTimeMS": 10_000}
    assert first["maxTimeMS"] == 10_000


def test_an_aggregate_command_has_a_cursor_and_the_substituted_pipeline():
    q = vq('{"aggregate": "orders", "pipeline": [{"$match": {"status": {"$param": 1}}}]}', ["x"])
    cmd = build_command(q)
    assert cmd == {"aggregate": "orders", "pipeline": [{"$match": {"status": "x"}}], "cursor": {}, "maxTimeMS": 10_000}


@pytest.mark.parametrize(
    ("error", "expected", "word"),
    [
        (ExecutionTimeout("operation exceeded time limit", 50, {}), ExplainTimeout, "time limit"),
        (OperationFailure("not authorized on admin to execute command", 13, {"errmsg": "not authorized"}), ExplainFailed, "authorized"),
        (ServerSelectionTimeoutError("mongo.internal.corp:27017: timed out, Timeout: 5s, Topology Description: <...>"), ExplainFailed, "could not be reached"),
    ],
)
def test_server_errors_become_typed_errors_without_host_details(error, expected, word):
    with pytest.raises(expected) as exc:
        explain_query(FakeDb(error=error), vq('{"find": "orders"}'), [], execute=True)
    assert word in str(exc.value).lower() and "mongo.internal.corp" not in str(exc.value)


# ---- live -----------------------------------------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.parametrize("query", MONGO_CATALOG, ids=lambda q: q.id)
def test_every_catalog_query_explains_live_into_a_parseable_plan(query):
    from pymongo import MongoClient

    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        out = vq(query.sql, query.params)
        plan = normalize_plan(explain_query(client["qd_sample"], out, query.params, execute=True))
        planner_only = normalize_plan(explain_query(client["qd_sample"], out, query.params, execute=False))
    assert plan.executed and plan.execution_time_ms is not None and not planner_only.executed
    assert plan.fingerprint == planner_only.fingerprint


@pytest.mark.integration
def test_an_operation_that_overruns_its_time_limit_raises_the_typed_timeout():
    from pymongo import MongoClient

    heavy = next(q for q in MONGO_CATALOG if q.id == "mongo_unbounded_group")
    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        with pytest.raises(ExplainTimeout):
            explain_query(client["qd_sample"], vq(heavy.sql), [], execute=True, max_time_ms=1)
