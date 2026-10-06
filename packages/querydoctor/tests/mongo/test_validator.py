import json

import pytest

from querydoctor.mongo import MongoQuery, MongoValidationError, ValidatedMongoQuery, normalize_shape, validate_mongo
from querydoctor.safety import ValidationError

FIND = {"find": "orders", "filter": {"customer_id": {"$param": 1}, "created_at": {"$gte": {"$param": 2}}}, "sort": {"created_at": -1}, "projection": {"total_amount": 1}, "limit": 20}
AGG = {"aggregate": "orders", "pipeline": [{"$match": {"status": {"$param": 1}}}, {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 5}]}


def v(spec, params=(), **kw):
    return validate_mongo(spec if isinstance(spec, str) else json.dumps(spec), list(params), **kw)


def refuse(spec, code, params=(), **kw):
    with pytest.raises(MongoValidationError) as exc:
        v(spec, params, **kw)
    assert exc.value.code == code, (exc.value.code, exc.value.message)
    assert isinstance(exc.value, ValidationError)  # the API's error handling treats every engine alike
    return exc.value


def test_a_find_with_placeholders_is_accepted_and_the_parameters_are_substituted():
    out = v(FIND, [4242, "2022-01-01"])
    assert isinstance(out, ValidatedMongoQuery) and isinstance(out.query, MongoQuery)
    assert out.query.kind == "find" and out.query.collection == "orders" and out.query.limit == 20
    assert out.query.filter == {"customer_id": 4242, "created_at": {"$gte": "2022-01-01"}}
    assert out.query.sort == {"created_at": -1} and out.query.projection == {"total_amount": 1}
    assert out.param_count == 2 and out.collections == ["orders"]
    assert json.loads(out.canonical_json)["filter"]["customer_id"] == {"$param": 1}  # the stored text keeps the placeholders


def test_an_aggregate_is_accepted():
    out = v(AGG, ["pending"])
    assert out.query.kind == "aggregate" and out.query.pipeline[0] == {"$match": {"status": "pending"}} and out.param_count == 1


def test_substitution_is_deterministic_and_the_canonical_form_ignores_whitespace_and_key_order_at_the_top():
    a = v(json.dumps(FIND, indent=3), [1, "x"])
    b = v(json.dumps(dict(reversed(list(FIND.items())))), [1, "x"])
    assert a.canonical_json == b.canonical_json and a.fingerprint == b.fingerprint


@pytest.mark.parametrize("params", [[], [1], [1, 2, 3]])
def test_the_parameter_count_must_match(params):
    refuse(FIND, "param_mismatch", params)


@pytest.mark.parametrize("bad", [[1, 2], {"a": 1}, float("inf"), "x" * 2000])
def test_parameters_must_be_plain_scalars(bad):
    refuse(FIND, "bad_param", [4242, bad]) if not isinstance(bad, float) else refuse(FIND, "bad_param", [4242, bad])


def test_booleans_nulls_numbers_and_strings_are_allowed_parameters():
    for value in (True, None, 1.5, -3, "text"):
        assert v(FIND, [value, 1]).query.filter["customer_id"] == value


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"find": "orders", "filter": {"$where": "sleep(1000)"}}, "denied_operator"),
        ({"find": "orders", "filter": {"$or": [{"a": 1}, {"$where": "true"}]}}, "denied_operator"),
        ({"aggregate": "orders", "pipeline": [{"$match": {"$where": "true"}}]}, "denied_operator"),
        ({"aggregate": "orders", "pipeline": [{"$addFields": {"x": {"$function": {"body": "function(){}", "args": [], "lang": "js"}}}}]}, "denied_operator"),
        ({"aggregate": "orders", "pipeline": [{"$group": {"_id": None, "x": {"$accumulator": {"init": "function(){}"}}}}]}, "denied_operator"),
        ({"aggregate": "orders", "pipeline": [{"$match": {}}, {"$out": "stolen"}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$merge": {"into": "stolen"}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$lookup": {"from": "customers", "pipeline": [{"$out": "x"}], "as": "c"}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$facet": {"a": [{"$match": {}}, {"$merge": {"into": "x"}}]}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$lookup": {"from": "customers", "pipeline": [{"$facet": {"a": [{"$out": "x"}]}}], "as": "c"}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$currentOp": {}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$listSessions": {}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$planCacheStats": {}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$collStats": {"storageStats": {}}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$unionWith": "customers"}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$graphLookup": {}}]}, "denied_stage"),
        ({"aggregate": "orders", "pipeline": [{"$match": {}, "$limit": 1}]}, "bad_pipeline"),
        ({"aggregate": "orders", "pipeline": ["$match"]}, "bad_pipeline"),
        ({"aggregate": "orders", "pipeline": "x"}, "bad_pipeline"),
        ({"aggregate": "orders"}, "bad_pipeline"),
    ],
)
def test_hostile_operators_and_stages_are_refused_wherever_they_hide(spec, code):
    refuse(spec, code)


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"mapReduce": "orders", "map": "function(){}", "reduce": "function(){}"}, "unknown_key"),
        ({"find": "orders", "filter": {}, "comment": "x"}, "unknown_key"),
        ({"find": "orders", "update": {"$set": {"a": 1}}}, "unknown_key"),
        ({"delete": "orders", "deletes": []}, "unknown_key"),
        ({"find": "orders", "aggregate": "orders"}, "mixed_kind"),
        ({"filter": {}}, "unknown_key"),
        ({}, "empty"),
        ({"find": ""}, "bad_collection"),
        ({"find": "system.users"}, "bad_collection"),
        ({"find": "a$b"}, "bad_collection"),
        ({"find": "x" * 200}, "bad_collection"),
        ({"find": 5}, "bad_collection"),
        ({"find": "orders", "limit": 0}, "bad_limit"),
        ({"find": "orders", "limit": 10_000_000}, "bad_limit"),
        ({"find": "orders", "limit": "5"}, "bad_limit"),
        ({"find": "orders", "filter": []}, "bad_filter"),
        ({"find": "orders", "sort": []}, "bad_sort"),
        ({"find": "orders", "sort": {"a": 2}}, "bad_sort"),
        ({"find": "orders", "projection": 5}, "bad_projection"),
        ({"aggregate": "orders", "pipeline": [{"$lookup": {"from": "system.users", "as": "u", "localField": "a", "foreignField": "b"}}]}, "bad_collection"),
    ],
)
def test_malformed_specs_are_refused_with_a_specific_code(spec, code):
    refuse(spec, code)


@pytest.mark.parametrize("text", ["", "   ", "not json", "{", '{"find": "orders",}', "[1, 2]", '"orders"', "null", "5"])
def test_text_that_is_not_a_json_object_is_refused(text):
    err = refuse(text, "invalid_json" if text.strip() not in ('"orders"', "null", "5", "[1, 2]") else "not_object")
    assert err.message


def test_duplicate_keys_are_refused_because_drivers_disagree_about_which_one_wins():
    refuse('{"find": "orders", "filter": {"a": 1, "a": 2}}', "duplicate_keys")
    refuse('{"find": "orders", "find": "customers"}', "duplicate_keys")


def test_size_and_depth_are_capped():
    refuse({"find": "orders", "filter": {"a": "x" * 60_000}}, "too_large")
    nested: dict = {"a": 1}
    for _ in range(25):
        nested = {"$and": [nested]}
    refuse({"find": "orders", "filter": nested}, "too_deep")
    shallow: dict = {"a": 1}
    for _ in range(5):
        shallow = {"$and": [shallow]}
    assert v({"find": "orders", "filter": shallow}).query.kind == "find"
    refuse({"find": "orders", "filter": {"a": "x" * 200}}, "too_large", max_bytes=100)
    refuse({"find": "orders", "filter": {"$and": [{"$and": [{"a": 1}]}]}}, "too_deep", max_depth=3)


def test_regular_expressions_are_length_capped_and_use_known_flags():
    assert v({"find": "customers", "filter": {"email": {"$regex": "^user1", "$options": "i"}}}).query.kind == "find"
    refuse({"find": "customers", "filter": {"email": {"$regex": "(a+)+" * 100}}}, "bad_regex")
    refuse({"find": "customers", "filter": {"email": {"$regex": "x", "$options": "iq"}}}, "bad_regex")
    refuse({"find": "customers", "filter": {"email": {"$regex": 5}}}, "bad_regex")


def test_every_stage_of_the_allowlist_is_accepted():
    for stage in (
        {"$match": {"a": 1}}, {"$group": {"_id": "$a"}}, {"$sort": {"a": 1}}, {"$limit": 5}, {"$skip": 5}, {"$project": {"a": 1}},
        {"$unwind": "$items"}, {"$lookup": {"from": "customers", "localField": "customer_id", "foreignField": "_id", "as": "c"}},
        {"$count": "n"}, {"$addFields": {"x": 1}}, {"$set": {"x": 1}}, {"$unset": "x"}, {"$facet": {"a": [{"$match": {}}]}},
        {"$bucket": {"groupBy": "$a", "boundaries": [0, 10], "default": "other"}}, {"$sample": {"size": 3}},
    ):
        assert v({"aggregate": "orders", "pipeline": [stage]}).query.kind == "aggregate", stage


def test_collections_include_lookup_targets_inside_nested_pipelines():
    spec = {"aggregate": "orders", "pipeline": [
        {"$lookup": {"from": "customers", "localField": "customer_id", "foreignField": "_id", "as": "c"}},
        {"$lookup": {"from": "payments", "as": "p", "let": {"id": "$_id"}, "pipeline": [{"$match": {"$expr": {"$eq": ["$order_id", "$$id"]}}}]}},
    ]}
    assert v(spec).collections == ["orders", "customers", "payments"]


def test_the_shape_replaces_literals_and_the_fingerprint_ignores_them():
    a = v(FIND, [1, "2020-01-01"])
    b = v(FIND, [99999, "2023-12-31"])
    assert a.shape == b.shape and a.fingerprint == b.fingerprint
    assert "99999" not in b.shape and "2023" not in b.shape and "?" in b.shape
    assert v({"find": "orders", "filter": {"status": "a"}}).fingerprint != v({"find": "orders", "filter": {"state": "a"}}).fingerprint
    assert v({"find": "orders", "filter": {"status": "a"}}).fingerprint != v({"find": "customers", "filter": {"status": "a"}}).fingerprint
    in1 = v({"find": "orders", "filter": {"status": {"$in": ["a"]}}})
    in3 = v({"find": "orders", "filter": {"status": {"$in": ["a", "b", "c"]}}})
    assert in1.fingerprint == in3.fingerprint  # a list of literals is one shape however long it is


def test_normalize_shape_directly():
    q = v({"aggregate": "orders", "pipeline": [{"$match": {"n": {"$gt": 5}}}, {"$limit": 10}]}).query
    shape = normalize_shape(q)
    assert "5" not in shape.replace("orders", "") and "10" not in shape and shape.count("?") == 2
    assert normalize_shape(q) == shape


def test_placeholders_may_not_be_object_keys_or_carry_extra_keys():
    refuse({"find": "orders", "filter": {"a": {"$param": 1, "x": 2}}}, "bad_param", [1])
    refuse({"find": "orders", "filter": {"a": {"$param": "one"}}}, "bad_param", ["x"])
    refuse({"find": "orders", "filter": {"a": {"$param": 0}}}, "bad_param", ["x"])
    refuse({"find": "orders", "filter": {"a": {"$param": 3}}}, "param_mismatch", ["x"])  # $param 3 needs three values
