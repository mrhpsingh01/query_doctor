import json

import pytest

from querydoctor.candidates import IndexCandidate, IndexColumn
from querydoctor.diagnosis.models import Finding
from querydoctor.mongo import validate_mongo
from querydoctor.mongo.candidates import build_create_index, generate_mongo_candidates, prune_mongo_redundant
from querydoctor.mongo.rules import CollectionInfo, IndexSpec


def vq(spec: dict, params=()):
    return validate_mongo(json.dumps(spec), list(params))


def gen(spec, collections=None, findings=()):
    return generate_mongo_candidates(vq(spec), collections or {}, list(findings))


def keys_of(c: IndexCandidate):
    return [(col.name, -1 if col.desc else 1) for col in c.columns]


def finding(rule_id, **evidence):
    return Finding(rule_id=rule_id, severity="warning", title="t", summary="s", evidence=evidence, node_ids=[], confidence=0.9, next_actions=["x"])


def test_equality_then_sort_then_range_with_the_sort_direction_and_a_display_ddl():
    [c] = gen({"find": "orders", "filter": {"status": "x", "created_at": {"$gte": "2022-01-01"}}, "sort": {"created_at": -1}})
    assert keys_of(c) == [("status", 1), ("created_at", -1)]  # created_at is both sort and range: once, in sort position and direction
    assert c.ddl == 'db.orders.createIndex({"status": 1, "created_at": -1})'
    assert c.table == "orders" and c.validation == "suggested" and c.columns == [IndexColumn(name="status"), IndexColumn(name="created_at", desc=True)]
    assert c.id == IndexCandidate.make_id(c.ddl) and c.include == [] and c.predicate is None and c.expression is None


def test_all_three_kinds_of_field_are_ordered_equality_sort_range():
    [c] = gen({"find": "orders", "filter": {"c": {"$gt": 1}, "a": 5, "d": {"$in": [1, 2]}}, "sort": {"b": 1}})
    assert keys_of(c) == [("a", 1), ("d", 1), ("b", 1), ("c", 1)]  # exactly the cap: equality, equality, sort, range


def test_the_key_count_is_capped():
    filter_ = {f"f{i}": i for i in range(8)}
    [c] = gen({"find": "orders", "filter": filter_})
    assert len(c.columns) == 4 and [col.name for col in c.columns] == ["f0", "f1", "f2", "f3"]


def test_conditions_an_index_cannot_narrow_do_not_shape_the_key():
    [c] = gen({"find": "orders", "filter": {"a": 1, "b": {"$ne": 2}, "c": {"$regex": "^x"}, "d": {"$exists": True}, "$or": [{"e": 1}, {"f": 2}]}})
    assert keys_of(c) == [("a", 1)]
    assert gen({"find": "orders", "filter": {"b": {"$ne": 2}}}) == []
    assert gen({"find": "orders", "filter": {}}) == []


def test_and_is_flattened_and_dotted_fields_are_kept():
    [c] = gen({"find": "orders", "filter": {"$and": [{"a": 1}, {"payment.method": "card"}, {"created_at": {"$lt": "x"}}]}})
    assert keys_of(c) == [("a", 1), ("payment.method", 1), ("created_at", 1)]


def test_an_aggregate_uses_its_leading_match_and_the_sort_right_after_it():
    pipeline = [{"$match": {"status": "x", "n": {"$gt": 1}}}, {"$sort": {"created_at": -1}}, {"$group": {"_id": "$a"}}]
    [c] = gen({"aggregate": "orders", "pipeline": pipeline})
    assert keys_of(c) == [("status", 1), ("created_at", -1), ("n", 1)]
    # a $match after $group, or a pipeline that starts with $group, gives nothing to index
    assert gen({"aggregate": "orders", "pipeline": [{"$group": {"_id": "$a"}}, {"$match": {"n": 1}}]}) == []
    [only_match] = gen({"aggregate": "orders", "pipeline": [{"$match": {"a": 1}}, {"$group": {"_id": "$b"}}]})
    assert keys_of(only_match) == [("a", 1)]


def test_rationale_explains_each_field_and_the_source_rules_are_recorded():
    findings = [finding("MONGO_COLLSCAN"), finding("MONGO_BLOCKING_SORT"), finding("MONGO_REDUNDANT_INDEX")]
    [c] = gen({"find": "events", "filter": {"event_type": "login"}, "sort": {"payload": 1}}, findings=findings)
    assert "equality on event_type" in c.rationale and any("ORDER BY payload" in r for r in c.rationale)
    assert c.source_rule_ids == ["MONGO_BLOCKING_SORT", "MONGO_COLLSCAN"]  # only the rules an index can fix


def test_a_match_on_the_group_key_after_a_group_is_indexed_as_if_it_ran_first():
    [c] = gen({"aggregate": "orders", "pipeline": [{"$group": {"_id": "$customer_id", "t": {"$sum": "$total"}}}, {"$match": {"_id": 7}}]})
    assert keys_of(c) == [("customer_id", 1)]
    assert gen({"aggregate": "orders", "pipeline": [{"$group": {"_id": {"a": "$a"}}}, {"$match": {"_id": 7}}]}) == []  # a compound key


def test_the_wrong_order_finding_produces_the_efficient_order_and_says_why():
    spec = {"find": "orders", "filter": {"customer_id": 7, "created_at": {"$gte": "2022-01-01", "$lt": "2023-01-01"}}}
    [c] = gen(spec, findings=[finding("MONGO_INDEX_ORDER")])
    assert keys_of(c) == [("customer_id", 1), ("created_at", 1)]
    assert any("range field before the equality field" in r for r in c.rationale) and c.source_rule_ids == ["MONGO_INDEX_ORDER"]


def test_a_lookup_into_an_unindexed_field_is_fixed_on_the_target_collection():
    spec = {"aggregate": "customers", "pipeline": [{"$match": {"segment": "x"}}, {"$lookup": {"from": "orders", "localField": "_id", "foreignField": "customer_id", "as": "o"}}]}
    found = gen(spec, findings=[finding("MONGO_LARGE_LOOKUP", **{"from": "orders", "foreign_field": "customer_id"})])
    assert {(c.table, c.ddl) for c in found} == {("customers", 'db.customers.createIndex({"segment": 1})'), ("orders", 'db.orders.createIndex({"customer_id": 1})')}


def test_existing_prefixes_and_the_id_index_make_a_candidate_redundant():
    existing = {"orders": CollectionInfo(count=10, indexes=[
        IndexSpec(name="status_1", keys=[("status", 1)]), IndexSpec(name="s_c", keys=[("a", 1), ("b", -1), ("c", 1)])])}
    by_status = gen({"find": "orders", "filter": {"status": "x"}})
    by_prefix = gen({"find": "orders", "filter": {"a": 1}, "sort": {"b": -1}})  # (a, b -1) is a prefix of s_c
    by_id = gen({"find": "orders", "filter": {"_id": 5}})
    for found, name in ((by_status, "status_1"), (by_prefix, "s_c"), (by_id, "_id")):
        kept, pruned = prune_mongo_redundant(found, existing)
        assert kept == [] and len(pruned) == 1 and (name in pruned[0][1] or name == "_id" and "_id" in pruned[0][1]), (name, pruned)


def test_a_candidate_that_extends_an_existing_index_is_kept_and_so_is_one_with_other_directions():
    existing = {"orders": CollectionInfo(count=10, indexes=[IndexSpec(name="a_1", keys=[("a", 1)])])}
    longer = gen({"find": "orders", "filter": {"a": 1, "b": 2}})
    reversed_ = generate_mongo_candidates(vq({"find": "orders", "filter": {"a": 1}, "sort": {"b": -1}}), existing, [])
    kept_longer, _ = prune_mongo_redundant(longer, existing)
    assert [c.ddl for c in kept_longer] == ['db.orders.createIndex({"a": 1, "b": 1})']
    assert len(prune_mongo_redundant(reversed_, existing)[0]) == 1
    descending = {"orders": CollectionInfo(count=10, indexes=[IndexSpec(name="a_-1", keys=[("a", -1)])])}
    assert len(prune_mongo_redundant(gen({"find": "orders", "filter": {"a": 1}}), descending)[0]) == 1  # (a, 1) is not (a, -1)


def test_a_candidate_covered_by_a_longer_candidate_is_dropped():
    short = _candidate_for("orders", [("a", 1)])
    long = _candidate_for("orders", [("a", 1), ("b", 1)])
    other_collection = _candidate_for("events", [("a", 1)])
    kept, pruned = prune_mongo_redundant([short, long, other_collection], {})
    assert kept == [long, other_collection] and pruned[0][0] is short and "longer candidate" in pruned[0][1]


def _candidate_for(table, keys):
    from querydoctor.mongo.candidates import display_ddl

    ddl = display_ddl(table, keys)
    return IndexCandidate(id=IndexCandidate.make_id(ddl), table=table, columns=[IndexColumn(name=f, desc=d == -1) for f, d in keys], ddl=ddl, rationale=["x"])


def test_the_create_index_keys_come_from_the_structure_never_from_the_display_string():
    c = _candidate_for("orders", [("a", 1), ("b", -1)])
    c.ddl = 'db.orders.dropDatabase() // {"zzz": 1}'  # a poisoned display string changes nothing
    assert build_create_index(c) == [("a", 1), ("b", -1)]


@pytest.mark.parametrize("name", ["$where", "", "a\x00b", "x" * 300])
def test_unusable_field_names_are_refused_when_building_the_index(name):
    c = IndexCandidate(id="x", table="orders", columns=[IndexColumn(name=name)], ddl="d", rationale=["r"])
    with pytest.raises(ValueError):
        build_create_index(c)


def test_too_many_or_no_key_fields_are_refused():
    with pytest.raises(ValueError):
        build_create_index(IndexCandidate(id="x", table="o", columns=[], ddl="d", rationale=["r"]))
    with pytest.raises(ValueError):
        build_create_index(IndexCandidate(id="x", table="o", columns=[IndexColumn(name=f"f{i}") for i in range(5)], ddl="d", rationale=["r"]))


def test_candidates_are_deterministic_and_unique_by_ddl():
    spec = {"find": "orders", "filter": {"a": 1, "b": {"$gt": 2}}, "sort": {"c": 1}}
    assert [c.model_dump() for c in gen(spec)] == [c.model_dump() for c in gen(spec)]
    findings = [finding("MONGO_LARGE_LOOKUP", **{"from": "orders", "foreign_field": "a"}), finding("MONGO_LARGE_LOOKUP", **{"from": "orders", "foreign_field": "a"})]
    found = gen({"aggregate": "customers", "pipeline": [{"$match": {"s": 1}}]}, findings=findings)
    assert len({c.id for c in found}) == len(found) == 2


def test_the_catalog_queries_produce_the_expected_candidates():
    from querydoctor.lab.mongo_catalog import MONGO_CATALOG

    for q in MONGO_CATALOG:
        if not q.expected_candidate_table:
            continue
        findings = [finding(r, **({"from": "orders", "foreign_field": "customer_id"} if r == "MONGO_LARGE_LOOKUP" else {})) for r in q.expected_rule_ids]
        found = generate_mongo_candidates(validate_mongo(q.sql, list(q.params)), {}, findings)
        assert any(c.table == q.expected_candidate_table and [col.name for col in c.columns] == q.expected_candidate_columns for c in found), (q.id, [c.ddl for c in found])
