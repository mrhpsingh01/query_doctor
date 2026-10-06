"""Eight deliberately slow MongoDB queries over the sample shop, each with the problem it demonstrates.

Specs are JSON texts in the QueryDoctor MongoDB dialect (``{"$param": n}`` placeholders). ``MONGO_CATALOG`` mirrors the
PostgreSQL catalog: the same ``SampleQuery`` shape, ``engine="mongodb"``, the rule(s) that should fire and, where an
index is the fix, the collection and key columns of the expected candidate.
"""

from __future__ import annotations

import json

from querydoctor.lab.catalog import SampleQuery
from querydoctor.lab.generator import light_customer, top_customers


def _spec(**kw) -> str:
    return json.dumps(kw, separators=(", ", ": "))


def build_mongo_catalog(scale: str = "small") -> list[SampleQuery]:
    heavy = top_customers(scale, 1)[0]
    light = light_customer(scale)
    return [
        SampleQuery(
            id="mongo_collscan_by_customer", engine="mongodb", title="Orders of one customer: a collection scan",
            sql=_spec(find="orders", filter={"customer_id": {"$param": 1}}), params=[light],
            description="orders has no index on customer_id, so every order is read to find one customer's.",
            expected_rule_ids=["MONGO_COLLSCAN", "MONGO_SCAN_AMPLIFICATION"],
            expected_candidate_table="orders", expected_candidate_columns=["customer_id"],
        ),
        SampleQuery(
            id="mongo_status_and_date_range", engine="mongodb", title="A rare status in a narrow date window",
            sql=_spec(find="orders", filter={"status": {"$param": 1}, "created_at": {"$gte": {"$param": 2}, "$lt": {"$param": 3}}}), params=["refunded", "2023-11-01", "2023-11-08"],
            description="The status index narrows to all refunded orders, then every one is fetched to test the date.",
            expected_rule_ids=["MONGO_SCAN_AMPLIFICATION"], expected_candidate_table="orders",
            expected_candidate_columns=["status", "created_at"],
        ),
        SampleQuery(
            id="mongo_blocking_sort", engine="mongodb", title="Sorting a large result in memory",
            sql=_spec(find="events", filter={"event_type": {"$param": 1}}, sort={"payload": 1}, limit=20), params=["page_view"],
            description="The twenty first events by payload: hundreds of thousands are read and sorted in memory to find them.",
            expected_rule_ids=["MONGO_BLOCKING_SORT"], expected_candidate_table="events", expected_candidate_columns=["event_type", "payload"],
        ),
        SampleQuery(
            id="mongo_range_before_equality", engine="mongodb", title="An index with the range field first",
            sql=_spec(find="orders", filter={"created_at": {"$gte": {"$param": 1}, "$lt": {"$param": 2}}, "customer_id": {"$param": 3}}), params=["2022-01-01", "2023-06-01", light],
            description="The existing index starts with created_at, so the whole date range is scanned for one customer.",
            expected_rule_ids=["MONGO_KEY_AMPLIFICATION", "MONGO_INDEX_ORDER"], expected_candidate_table="orders",
            expected_candidate_columns=["customer_id", "created_at"],
        ),
        SampleQuery(
            id="mongo_lookup_without_index", engine="mongodb", title="$lookup into an unindexed field",
            sql=_spec(aggregate="customers", pipeline=[
                {"$match": {"segment": {"$param": 1}, "_id": {"$lte": {"$param": 2}}}},
                {"$lookup": {"from": "orders", "localField": "_id", "foreignField": "customer_id", "as": "orders"}},
                {"$project": {"email": 1, "order_count": {"$size": "$orders"}}},
            ]), params=["regular", 150],
            description="For every customer the join scans the whole orders collection, because orders.customer_id has no index.",
            expected_rule_ids=["MONGO_LARGE_LOOKUP"], expected_candidate_table="orders", expected_candidate_columns=["customer_id"],
        ),
        SampleQuery(
            id="mongo_match_after_group", engine="mongodb", title="Filtering after grouping",
            sql=_spec(aggregate="orders", pipeline=[
                {"$group": {"_id": "$customer_id", "total": {"$sum": "$total_amount"}}},
                {"$match": {"_id": {"$param": 1}}},
            ]), params=[light],
            description="Every order is grouped, then all groups but one are thrown away; the filter could run first.",
            expected_rule_ids=["MONGO_STAGE_ORDER"], expected_candidate_table="orders", expected_candidate_columns=["customer_id"],
        ),
        SampleQuery(
            id="mongo_unbounded_group", engine="mongodb", title="An unbounded $group over a big collection",
            sql=_spec(aggregate="events", pipeline=[{"$group": {"_id": "$customer_id", "n": {"$sum": 1}}}]), params=[],
            description="Every event is read and grouped with no filter and no limit.",
            expected_rule_ids=["MONGO_UNBOUNDED_AGG"],
        ),
        SampleQuery(
            id="mongo_unselective_status_index", engine="mongodb", title="An index that matches most of the collection",
            sql=_spec(find="orders", filter={"status": {"$param": 1}}, projection={"total_amount": 1}), params=["completed"],
            description="The status index is used, but most orders are completed, so it saves almost nothing.",
            expected_rule_ids=["MONGO_UNSELECTIVE_INDEX"],
        ),
    ]


MONGO_CATALOG = build_mongo_catalog()
