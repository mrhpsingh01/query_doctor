import json
import pathlib

import pytest

from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.lab.mongo_seed import INDEXES
from querydoctor.mongo import normalize_plan, validate_mongo
from querydoctor.mongo.rules import (
    MONGO_RULES, CollectionInfo, IndexSpec, MongoContext, MongoThresholds, run_mongo_diagnosis,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[4] / "fixtures" / "mongo_plans"
COUNTS = {"customers": 10_000, "orders": 100_000, "events": 500_000}
BY_ID = {q.id: q for q in MONGO_CATALOG}


def seeded_indexes(name: str) -> list[IndexSpec]:
    return [IndexSpec(name=n, keys=[(f, d) for f, d in keys]) for keys, n in INDEXES.get(name, [])]


def lab_collections() -> dict[str, CollectionInfo]:
    return {name: CollectionInfo(count=count, indexes=seeded_indexes(name)) for name, count in COUNTS.items()}


def fixture_context(query_id: str, suffix: str = "stats", collections=True, **kw) -> MongoContext:
    q = BY_ID[query_id]
    raw = json.loads((FIXTURES / f"{query_id}.{suffix}.json").read_text(encoding="utf-8"))
    return MongoContext(
        plan=normalize_plan(raw), query=validate_mongo(q.sql, list(q.params)), collections=lab_collections() if collections else {}, **kw
    )


def fired(ctx: MongoContext) -> dict[str, list]:
    out: dict[str, list] = {}
    for f in run_mongo_diagnosis(ctx):
        out.setdefault(f.rule_id, []).append(f)
    return out


def plan_of(stage: dict, *, executed=True, namespace="qd_sample.orders", ms=10):
    raw = {"queryPlanner": {"namespace": namespace, "winningPlan": {k: v for k, v in stage.items()}}}
    if executed:
        raw["executionStats"] = {"executionTimeMillis": ms, "executionStages": stage}
    return normalize_plan(raw)


def ctx_for(stage, spec='{"find": "orders", "filter": {"status": "x"}}', params=(), **kw) -> MongoContext:
    executed = kw.pop("executed", True)
    return MongoContext(plan=plan_of(stage, executed=executed), query=validate_mongo(spec, list(params)), collections=kw.pop("collections", lab_collections()), **kw)


def collscan(docs, returned, filter_={"a": 1}):
    return {"stage": "COLLSCAN", "filter": filter_, "nReturned": returned, "docsExamined": docs, "executionTimeMillisEstimate": 5}


# ---- the eight catalog queries, from live-captured plans ------------------------------------------------------------

@pytest.mark.parametrize("query", MONGO_CATALOG, ids=lambda q: q.id)
def test_every_expected_rule_fires_on_the_captured_plan_of_its_catalog_query(query):
    found = set(fired(fixture_context(query.id)))
    assert set(query.expected_rule_ids) <= found, (query.id, found)


def test_the_redundant_event_index_is_reported_on_any_events_query():
    assert "MONGO_REDUNDANT_INDEX" in fired(fixture_context("mongo_blocking_sort"))
    assert "MONGO_REDUNDANT_INDEX" not in fired(fixture_context("mongo_collscan_by_customer"))  # orders has no redundant pair


def test_rules_stay_quiet_where_the_plan_is_fine():
    status = fired(fixture_context("mongo_status_and_date_range"))
    assert "MONGO_COLLSCAN" not in status and "MONGO_BLOCKING_SORT" not in status and "MONGO_UNSELECTIVE_INDEX" not in status  # 4% of orders
    unselective = fired(fixture_context("mongo_unselective_status_index"))
    assert "MONGO_COLLSCAN" not in unselective and "MONGO_SCAN_AMPLIFICATION" not in unselective
    unbounded = fired(fixture_context("mongo_unbounded_group"))
    assert "MONGO_COLLSCAN" not in unbounded  # a full scan with no filter is what it asked for


@pytest.mark.parametrize("query", MONGO_CATALOG, ids=lambda q: q.id)
@pytest.mark.parametrize("suffix", ["stats", "planner"])
@pytest.mark.parametrize("with_collections", [True, False])
def test_no_rule_crashes_on_any_committed_plan(query, suffix, with_collections):
    ctx = fixture_context(query.id, suffix, collections=with_collections)
    findings = run_mongo_diagnosis(ctx)
    assert all(f.rule_id in {r.rule_id for r in MONGO_RULES} and 0 < f.confidence <= 1 and f.next_actions for f in findings)
    assert run_mongo_diagnosis(ctx.model_copy(update={"query": None})) is not None  # also without the query


def test_the_rule_ids_are_the_ten_of_the_catalog():
    assert [r.rule_id for r in MONGO_RULES] == [
        "MONGO_COLLSCAN", "MONGO_SCAN_AMPLIFICATION", "MONGO_KEY_AMPLIFICATION", "MONGO_BLOCKING_SORT", "MONGO_INDEX_ORDER",
        "MONGO_UNBOUNDED_AGG", "MONGO_LARGE_LOOKUP", "MONGO_UNSELECTIVE_INDEX", "MONGO_REDUNDANT_INDEX", "MONGO_STAGE_ORDER",
    ]


# ---- collection scans -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("docs", "should_fire"), [(49_999, False), (50_000, True), (500_000, True)])
def test_a_filtered_collection_scan_fires_from_50_000_documents(docs, should_fire):
    found = fired(ctx_for(collscan(docs, 10)))
    assert ("MONGO_COLLSCAN" in found) is should_fire


def test_a_wasteful_collection_scan_is_critical_and_a_milder_one_a_warning():
    assert fired(ctx_for(collscan(100_000, 10)))["MONGO_COLLSCAN"][0].severity == "critical"  # 99.99% thrown away
    assert fired(ctx_for(collscan(100_000, 50_000)))["MONGO_COLLSCAN"][0].severity == "warning"


def test_an_unfiltered_scan_is_not_reported():
    assert "MONGO_COLLSCAN" not in fired(ctx_for({"stage": "COLLSCAN", "nReturned": 100_000, "docsExamined": 100_000, "executionTimeMillisEstimate": 5}))


def test_a_planner_only_plan_still_reports_a_collection_scan_with_less_confidence():
    f = fired(ctx_for({"stage": "COLLSCAN", "filter": {"a": 1}}, executed=False))["MONGO_COLLSCAN"][0]
    assert f.confidence < 0.7 and f.evidence["docs_examined"] == 100_000
    assert "MONGO_COLLSCAN" not in fired(ctx_for({"stage": "COLLSCAN", "filter": {"a": 1}}, executed=False, collections={}))  # size unknown


# ---- scan and key amplification --------------------------------------------------------------------------------------

def fetch(docs, returned):
    return {"stage": "FETCH", "nReturned": returned, "docsExamined": docs, "executionTimeMillisEstimate": 5, "inputStage": {"stage": "IXSCAN", "indexName": "status_1", "keyPattern": {"status": 1}, "nReturned": docs, "keysExamined": docs, "executionTimeMillisEstimate": 1}}


def test_scan_amplification_is_documents_examined_per_document_returned():
    f = fired(ctx_for(fetch(500_000, 20)))["MONGO_SCAN_AMPLIFICATION"][0]
    assert f.evidence["ratio"] == 25_000 and f.evidence["docs_examined"] == 500_000 and f.severity == "critical"


@pytest.mark.parametrize(("docs", "returned", "expected"), [(1_000, 50, "MONGO_SCAN_AMPLIFICATION"), (1_000, 51, None), (999, 0, None), (1_000, 0, "MONGO_SCAN_AMPLIFICATION"), (5_000, 250, "MONGO_SCAN_AMPLIFICATION"), (5_000, 251, None)])
def test_scan_amplification_boundaries_and_zero_results(docs, returned, expected):
    found = fired(ctx_for(fetch(docs, returned)))
    assert ("MONGO_SCAN_AMPLIFICATION" in found) is (expected is not None)


def test_zero_results_never_divide_by_zero_and_count_as_one():
    f = fired(ctx_for(fetch(5_000, 0)))["MONGO_SCAN_AMPLIFICATION"][0]
    assert f.evidence["ratio"] == 5_000 and f.severity == "critical"


def test_amplification_rules_are_silent_without_execution_statistics():
    found = fired(ctx_for(fetch(500_000, 20), executed=False))
    assert "MONGO_SCAN_AMPLIFICATION" not in found and "MONGO_KEY_AMPLIFICATION" not in found


@pytest.mark.parametrize(("keys", "returned", "fires"), [(1_000, 20, True), (1_000, 21, False), (999, 0, False), (60_000, 100, True), (60_000, 0, True)])
def test_key_amplification_boundaries(keys, returned, fires):
    stage = {"stage": "IXSCAN", "indexName": "i", "keyPattern": {"a": 1}, "keysExamined": keys, "nReturned": returned, "executionTimeMillisEstimate": 1}
    assert ("MONGO_KEY_AMPLIFICATION" in fired(ctx_for(stage))) is fires


# ---- sorting -----------------------------------------------------------------------------------------------------------

def sort(docs, disk=False):
    return {"stage": "SORT", "sortPattern": {"payload": 1}, "usedDisk": disk, "nReturned": docs, "executionTimeMillisEstimate": 9, "inputStage": {"stage": "COLLSCAN", "nReturned": docs, "docsExamined": docs, "executionTimeMillisEstimate": 3}}


@pytest.mark.parametrize(("docs", "fires"), [(9_999, False), (10_000, True), (299_000, True)])
def test_a_blocking_sort_fires_from_10_000_documents(docs, fires):
    assert ("MONGO_BLOCKING_SORT" in fired(ctx_for(sort(docs)))) is fires


def test_a_sort_that_spilled_to_disk_is_critical_even_when_small():
    f = fired(ctx_for(sort(50, disk=True)))["MONGO_BLOCKING_SORT"][0]
    assert f.severity == "critical" and f.evidence["used_disk"] is True
    assert fired(ctx_for(sort(20_000)))["MONGO_BLOCKING_SORT"][0].severity == "warning"


def test_a_sort_with_a_limit_is_judged_by_the_documents_it_read_not_the_few_it_returned():
    stage = {"stage": "SORT", "sortPattern": {"payload": 1}, "limitAmount": 20, "nReturned": 20, "executionTimeMillisEstimate": 9, "inputStage": {"stage": "COLLSCAN", "nReturned": 300_000, "docsExamined": 300_000, "executionTimeMillisEstimate": 3}}
    f = fired(ctx_for(stage))["MONGO_BLOCKING_SORT"][0]
    assert f.evidence["docs_sorted"] == 300_000


def test_a_planner_only_sort_uses_the_collection_size_with_less_confidence():
    f = fired(ctx_for({"stage": "SORT", "sortPattern": {"a": 1}, "inputStage": {"stage": "COLLSCAN"}}, executed=False))["MONGO_BLOCKING_SORT"][0]
    assert f.confidence < 0.7


# ---- compound index order ---------------------------------------------------------------------------------------------

def ixscan(pattern, name="i"):
    return {"stage": "FETCH", "nReturned": 1, "executionTimeMillisEstimate": 2, "inputStage": {"stage": "IXSCAN", "indexName": name, "keyPattern": pattern, "nReturned": 1, "keysExamined": 5, "executionTimeMillisEstimate": 1}}


SPEC = '{"find": "orders", "filter": {"customer_id": 7, "created_at": {"$gte": "2022-01-01", "$lt": "2023-01-01"}}, "sort": {"created_at": -1}}'


def test_a_range_field_before_an_equality_field_is_reported_with_the_recommended_order():
    f = fired(ctx_for(ixscan({"created_at": 1, "customer_id": 1}), SPEC))["MONGO_INDEX_ORDER"][0]
    assert f.evidence["recommended_order"] == ["customer_id", "created_at"] and f.evidence["key_order"] == ["created_at", "customer_id"]


def test_a_correct_equality_first_order_or_a_single_kind_of_predicate_is_fine():
    assert "MONGO_INDEX_ORDER" not in fired(ctx_for(ixscan({"customer_id": 1, "created_at": 1}), SPEC))
    only_equality = '{"find": "orders", "filter": {"customer_id": 7, "status": "x"}}'
    assert "MONGO_INDEX_ORDER" not in fired(ctx_for(ixscan({"status": 1, "customer_id": 1}), only_equality))
    assert "MONGO_INDEX_ORDER" not in fired(ctx_for(ixscan({"created_at": 1}), SPEC))  # one field: nothing to reorder


def test_index_order_works_from_a_planner_only_plan_and_for_aggregate_matches():
    assert "MONGO_INDEX_ORDER" in fired(ctx_for(ixscan({"created_at": 1, "customer_id": 1}), SPEC, executed=False))
    agg = '{"aggregate": "orders", "pipeline": [{"$match": {"customer_id": 7, "created_at": {"$gte": "2022-01-01"}}}]}'
    assert "MONGO_INDEX_ORDER" in fired(ctx_for(ixscan({"created_at": 1, "customer_id": 1}), agg))


# ---- aggregation rules -------------------------------------------------------------------------------------------------

def agg_ctx(pipeline, count=500_000, collection="events"):
    spec = json.dumps({"aggregate": collection, "pipeline": pipeline})
    plan = normalize_plan({"queryPlanner": {"namespace": f"qd_sample.{collection}", "winningPlan": {"stage": "COLLSCAN"}}, "stages": [{"$cursor": {"queryPlanner": {"namespace": f"qd_sample.{collection}", "winningPlan": {"stage": "COLLSCAN"}}}}]})
    return MongoContext(plan=plan, query=validate_mongo(spec, []), collections={collection: CollectionInfo(count=count)})


def test_a_group_over_a_big_collection_with_nothing_before_it_is_unbounded():
    f = fired(agg_ctx([{"$group": {"_id": "$customer_id", "n": {"$sum": 1}}}]))["MONGO_UNBOUNDED_AGG"][0]
    assert f.evidence["docs_in"] == 500_000 and f.evidence["stage"] == "$group"


@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$match": {"event_type": "login"}}, {"$group": {"_id": "$customer_id"}}],
        [{"$limit": 100}, {"$group": {"_id": "$customer_id"}}],
        [{"$sort": {"a": 1}}, {"$limit": 5}],
        [{"$project": {"a": 1}}],
    ],
)
def test_a_bounded_or_light_pipeline_is_not_unbounded(pipeline):
    assert "MONGO_UNBOUNDED_AGG" not in fired(agg_ctx(pipeline))


def test_a_small_collection_is_not_worth_flagging():
    assert "MONGO_UNBOUNDED_AGG" not in fired(agg_ctx([{"$group": {"_id": "$a"}}], count=49_999))
    assert "MONGO_UNBOUNDED_AGG" in fired(agg_ctx([{"$group": {"_id": "$a"}}], count=50_000))


def lookup_plan(docs, scans, executed=True):
    stage = {"$lookup": {"from": "orders", "as": "o", "localField": "_id", "foreignField": "customer_id"}, "totalDocsExamined": docs, "totalKeysExamined": 0, "collectionScans": scans, "indexesUsed": []}
    if executed:
        stage |= {"nReturned": 116, "executionTimeMillisEstimate": 40}
    cursor = {"$cursor": {"queryPlanner": {"namespace": "qd_sample.customers", "winningPlan": {"stage": "COLLSCAN"}}}}
    return normalize_plan({"stages": [cursor, stage]})


def lookup_ctx(plan, indexes=()):
    spec = '{"aggregate": "customers", "pipeline": [{"$lookup": {"from": "orders", "localField": "_id", "foreignField": "customer_id", "as": "o"}}]}'
    return MongoContext(plan=plan, query=validate_mongo(spec, []), collections={"customers": CollectionInfo(count=10_000), "orders": CollectionInfo(count=100_000, indexes=list(indexes))})


def test_a_lookup_that_scans_the_target_collection_is_critical():
    f = fired(lookup_ctx(lookup_plan(11_600_000, 116)))["MONGO_LARGE_LOOKUP"][0]
    assert f.severity == "critical" and f.evidence["from"] == "orders" and f.evidence["foreign_field"] == "customer_id" and f.evidence["collection_scans"] == 116


@pytest.mark.parametrize(("docs", "fires"), [(99_999, False), (100_000, True)])
def test_a_lookup_that_examined_few_documents_is_fine(docs, fires):
    assert ("MONGO_LARGE_LOOKUP" in fired(lookup_ctx(lookup_plan(docs, 0)))) is fires


def test_a_planner_only_lookup_into_an_unindexed_field_is_flagged_but_an_indexed_one_is_not():
    assert "MONGO_LARGE_LOOKUP" in fired(lookup_ctx(lookup_plan(0, 0, executed=False)))
    indexed = [IndexSpec(name="customer_id_1", keys=[("customer_id", 1)])]
    assert "MONGO_LARGE_LOOKUP" not in fired(lookup_ctx(lookup_plan(0, 0, executed=False), indexes=indexed))


@pytest.mark.parametrize(
    ("pipeline", "fires"),
    [
        ([{"$group": {"_id": "$customer_id", "t": {"$sum": 1}}}, {"$match": {"_id": 7}}], True),
        ([{"$group": {"_id": "$customer_id", "t": {"$sum": 1}}}, {"$match": {"t": {"$gt": 5}}}], False),  # filters on a computed field
        ([{"$group": {"_id": {"a": "$a", "b": "$b"}}}, {"$match": {"_id": 1}}], False),  # a compound key cannot simply move
        ([{"$sort": {"a": 1}}, {"$match": {"status": "x"}}], True),
        ([{"$project": {"status": 1, "a": 1}}, {"$match": {"status": "x"}}], True),
        ([{"$project": {"s": "$status"}}, {"$match": {"s": "x"}}], False),  # renamed: depends on the projection
        ([{"$match": {"status": "x"}}, {"$group": {"_id": "$a"}}], False),
        ([{"$match": {"a": 1}}], False),
    ],
)
def test_a_match_that_could_run_earlier_is_reported_only_when_moving_it_is_safe(pipeline, fires):
    assert ("MONGO_STAGE_ORDER" in fired(agg_ctx(pipeline, count=10, collection="orders"))) is fires


# ---- indexes -----------------------------------------------------------------------------------------------------------

def collection_ctx(indexes, collection="events"):
    spec = f'{{"find": "{collection}", "filter": {{"a": 1}}}}'
    plan = plan_of(collscan(10, 1), executed=False)
    return MongoContext(plan=plan, query=validate_mongo(spec, []), collections={collection: CollectionInfo(count=10, indexes=indexes)})


def index(name, *keys, **kw):
    return IndexSpec(name=name, keys=[(k, 1) for k in keys], **kw)


def test_a_prefix_of_another_index_is_redundant():
    f = fired(collection_ctx([index("_id_", "_id"), index("a_1", "a"), index("a_1_b_1", "a", "b")]))["MONGO_REDUNDANT_INDEX"]
    assert [x.evidence["index"] for x in f] == ["a_1"] and f[0].evidence["covered_by"] == "a_1_b_1"


def test_unique_partial_and_ttl_indexes_and_non_prefixes_are_never_called_redundant():
    for kw in ({"unique": True}, {"partial": True}, {"ttl": True}):
        assert "MONGO_REDUNDANT_INDEX" not in fired(collection_ctx([index("a_1", "a", **kw), index("a_1_b_1", "a", "b")]))
    assert "MONGO_REDUNDANT_INDEX" not in fired(collection_ctx([index("b_1", "b"), index("a_1_b_1", "a", "b")]))  # b is not a prefix
    reverse = IndexSpec(name="a_-1", keys=[("a", -1)])
    assert "MONGO_REDUNDANT_INDEX" not in fired(collection_ctx([reverse, index("a_1_b_1", "a", "b")]))  # different direction


def test_exact_duplicates_report_only_one_of_the_pair():
    found = fired(collection_ctx([index("a_1", "a"), index("a_dup", "a")]))["MONGO_REDUNDANT_INDEX"]
    assert len(found) == 1


def test_an_index_matching_most_of_the_collection_is_unselective_only_when_it_matches_a_big_share_of_many_keys():
    def ctx(returned, keys=20_000):
        stage = {"stage": "FETCH", "nReturned": returned, "docsExamined": returned, "executionTimeMillisEstimate": 5, "inputStage": {"stage": "IXSCAN", "indexName": "status_1", "keyPattern": {"status": 1}, "nReturned": returned, "keysExamined": keys, "executionTimeMillisEstimate": 2}}
        return ctx_for(stage)

    assert "MONGO_UNSELECTIVE_INDEX" in fired(ctx(62_000, keys=62_000))
    assert "MONGO_UNSELECTIVE_INDEX" not in fired(ctx(19_999, keys=19_999))  # under 20% of 100,000
    assert "MONGO_UNSELECTIVE_INDEX" in fired(ctx(20_000, keys=20_000))
    assert "MONGO_UNSELECTIVE_INDEX" not in fired(ctx(9_000, keys=9_000))  # too few keys to matter
    assert "MONGO_UNSELECTIVE_INDEX" not in fired(ctx_for(plan_of_stage_without_stats()))


def plan_of_stage_without_stats():
    return {"stage": "IXSCAN", "indexName": "x", "keyPattern": {"a": 1}}


# ---- configuration and robustness --------------------------------------------------------------------------------------

def test_thresholds_can_be_changed():
    strict = MongoThresholds(collscan_min_docs=10)
    assert "MONGO_COLLSCAN" in fired(ctx_for(collscan(1_000, 5), thresholds=strict))
    assert "MONGO_COLLSCAN" not in fired(ctx_for(collscan(1_000, 5)))


def test_a_crashing_rule_is_skipped_not_fatal():
    class Boom:
        rule_id = "BOOM"

        def evaluate(self, ctx):
            raise RuntimeError("bug")

    ctx = ctx_for(collscan(100_000, 10))
    assert [f.rule_id for f in run_mongo_diagnosis(ctx, rules=[Boom(), MONGO_RULES[0]])] == ["MONGO_COLLSCAN"]


def test_findings_are_ordered_most_severe_first():
    severities = [f.severity for f in run_mongo_diagnosis(fixture_context("mongo_range_before_equality"))]
    order = {"critical": 0, "warning": 1, "info": 2}
    assert severities == sorted(severities, key=order.get) and severities
