import pytest
from labdsn import MONGO_RO_URI, MONGO_ROOT_URI
from pymongo import MongoClient

from querydoctor.lab.mongo_catalog import MONGO_CATALOG, build_mongo_catalog
from querydoctor.lab.mongo_seed import seed_mongo
from querydoctor.mongo import validate_mongo

BY_ID = {q.id: q for q in MONGO_CATALOG}
RULES = {
    "MONGO_COLLSCAN_LARGE", "MONGO_SCAN_AMPLIFICATION", "MONGO_KEY_AMPLIFICATION", "MONGO_BLOCKING_SORT", "MONGO_COMPOUND_ORDER",
    "MONGO_UNBOUNDED_AGGREGATION", "MONGO_LOOKUP_NO_INDEX", "MONGO_UNSELECTIVE_INDEX", "MONGO_REDUNDANT_INDEX", "MONGO_STAGE_ORDER",
}


def test_the_catalog_has_eight_distinct_queries_for_mongodb():
    assert len(MONGO_CATALOG) == 8 and len(BY_ID) == 8
    assert {q.engine for q in MONGO_CATALOG} == {"mongodb"}
    assert all(q.id.startswith("mongo_") and q.title and q.description for q in MONGO_CATALOG)


@pytest.mark.parametrize("query", MONGO_CATALOG, ids=lambda q: q.id)
def test_every_catalog_query_validates_with_its_parameters(query):
    out = validate_mongo(query.sql, list(query.params))
    assert out.param_count == len(query.params)
    assert set(query.expected_rule_ids) <= RULES
    if query.expected_candidate_table:
        assert query.expected_candidate_table in out.collections and query.expected_candidate_columns


def test_the_catalog_uses_the_same_heavy_and_light_customers_as_the_sql_lab():
    from querydoctor.lab.catalog import build_catalog

    sql = {q.id: q for q in build_catalog("small")}
    assert BY_ID["mongo_collscan_by_customer"].params == [sql["recent_orders_by_customer"].params[0]]
    assert build_mongo_catalog("tiny")[0].params != BY_ID["mongo_collscan_by_customer"].params


def test_the_catalog_covers_every_rule_that_a_query_can_trigger():
    covered = {r for q in MONGO_CATALOG for r in q.expected_rule_ids}
    assert covered == RULES - {"MONGO_REDUNDANT_INDEX"}  # the redundant pair is found from the index list, on any events query


# ---- live: the plans the catalog is designed to produce -------------------------------------------------------------

def stages(plan) -> list[str]:
    out, stack = [], [plan]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "stage" in node:
                out.append(node["stage"])
            stack += [v for v in node.values() if isinstance(v, (dict, list))]
        elif isinstance(node, list):
            stack += node
    return out


@pytest.fixture(scope="module")
def ro():
    seed_mongo(MONGO_ROOT_URI, "small", if_empty=True)  # the lab database, read-only for analyses
    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        yield client["qd_sample"]


def explain(db, query_id: str) -> dict:
    q = validate_mongo(BY_ID[query_id].sql, list(BY_ID[query_id].params)).query
    if q.kind == "find":
        cmd = {"find": q.collection, "filter": q.filter}
        if q.sort:
            cmd["sort"] = q.sort
        if q.projection:
            cmd["projection"] = q.projection
    else:
        cmd = {"aggregate": q.collection, "pipeline": q.pipeline, "cursor": {}}
    return db.command({"explain": cmd, "verbosity": "executionStats"})


@pytest.mark.integration
def test_collection_scans_where_the_index_is_missing(ro):
    assert "COLLSCAN" in stages(explain(ro, "mongo_collscan_by_customer")["queryPlanner"]["winningPlan"])


@pytest.mark.integration
def test_the_status_and_range_before_equality_queries_use_the_wrong_index_and_scan_far_more_than_they_return(ro):
    status = explain(ro, "mongo_status_and_date_range")
    s = status["executionStats"]
    assert "IXSCAN" in stages(status["queryPlanner"]["winningPlan"]) and s["totalDocsExamined"] > 20 * max(s["nReturned"], 1)
    ranged = explain(ro, "mongo_range_before_equality")
    r = ranged["executionStats"]
    assert "IXSCAN" in stages(ranged["queryPlanner"]["winningPlan"]) and r["totalKeysExamined"] > 50 * max(r["nReturned"], 1)


@pytest.mark.integration
def test_the_blocking_sort_is_a_sort_stage_over_an_index_scan(ro):
    found = stages(explain(ro, "mongo_blocking_sort")["queryPlanner"]["winningPlan"])
    assert "SORT" in found and "IXSCAN" in found


@pytest.mark.integration
def test_the_aggregates_group_and_lookup_as_designed(ro):
    group = explain(ro, "mongo_unbounded_group")
    assert "COLLSCAN" in str(group) and "$group" in str(group)
    lookup = explain(ro, "mongo_lookup_without_index")
    assert "$lookup" in str(lookup)
    after = explain(ro, "mongo_match_after_group")
    assert str(after).find("$group") != -1 and "COLLSCAN" in str(after)


@pytest.mark.integration
def test_the_unselective_index_matches_most_of_the_collection(ro):
    plan = explain(ro, "mongo_unselective_status_index")
    s = plan["executionStats"]
    assert "IXSCAN" in stages(plan["queryPlanner"]["winningPlan"])
    assert s["nReturned"] > 0.5 * ro["orders"].estimated_document_count()
