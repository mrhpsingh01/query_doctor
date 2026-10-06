"""Rules about sorting and the order of compound-index fields."""

from __future__ import annotations

from typing import Any

from querydoctor.diagnosis.models import Finding
from querydoctor.mongo.rules.base import MongoContext, finding

RANGE_OPS = {"$gt", "$gte", "$lt", "$lte"}


class BlockingSort:
    rule_id = "MONGO_BLOCKING_SORT"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t, out = ctx.thresholds, []
        for node in ctx.nodes("SORT", "SORT_SIMPLE", "SORT_DEFAULT"):
            used_disk = bool(node.raw.get("usedDisk"))
            fed = [c.actual_rows for c in node.children if c.actual_rows is not None]  # a sort with a limit returns few but reads many
            if ctx.executed and (fed or node.actual_rows is not None):
                docs, confidence = int(max(fed) if fed else node.actual_rows), 0.95
            else:
                docs, confidence = ctx.count_of(ctx.collection), 0.6
                if docs is None:
                    continue
            if docs < t.sort_min_docs and not used_disk:
                continue
            out.append(
                finding(
                    self.rule_id, "critical" if used_disk else "warning", "Blocking sort",
                    f"{docs:,} documents are sorted in {'memory and spilled to disk' if used_disk else 'memory'} because no index returns them in order.",
                    {"sort_pattern": node.raw.get("sortPattern"), "docs_sorted": docs, "used_disk": used_disk, "bytes_sorted": node.raw.get("totalDataSizeSorted")},
                    [node.node_id], confidence,
                    ["Create an index that ends with the sort fields (after the equality fields) so rows come out in order", "Filter or limit before sorting"],
                )
            )
        return out


def classify_filter(filter_: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Top-level fields of a filter split into (equality, range) fields."""
    equality: list[str] = []
    ranges: list[str] = []
    for field, condition in filter_.items():
        if field.startswith("$"):
            continue
        if isinstance(condition, dict) and any(op in condition for op in RANGE_OPS):
            ranges.append(field)
        elif isinstance(condition, dict) and set(condition) - {"$eq", "$in"} and not (set(condition) <= {"$eq", "$in"}):
            continue  # $ne, $regex, $exists...: neither
        else:
            equality.append(field)
    return equality, ranges


def _query_filter(ctx: MongoContext) -> dict[str, Any]:
    if not ctx.query:
        return {}
    q = ctx.query.query
    if q.kind == "find":
        return q.filter
    first = (q.pipeline or [{}])[0]
    return first.get("$match", {}) if isinstance(first, dict) else {}


class IndexOrder:
    rule_id = "MONGO_INDEX_ORDER"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        equality, ranges = classify_filter(_query_filter(ctx))
        if not equality or not ranges:
            return []
        sort = list((ctx.query.query.sort or {}).keys()) if ctx.query and ctx.query.query.kind == "find" else []
        out: list[Finding] = []
        for node in ctx.nodes("IXSCAN"):
            pattern = list((node.raw.get("keyPattern") or {}).keys())
            used_ranges = [f for f in ranges if f in pattern]
            used_equality = [f for f in equality if f in pattern]
            wrong = [(r, e) for r in used_ranges for e in used_equality if pattern.index(r) < pattern.index(e)]
            if not wrong:
                continue
            recommended = [*equality, *[s for s in sort if s not in equality and s not in ranges], *ranges]
            out.append(
                finding(
                    self.rule_id, "warning", "Compound index fields in an inefficient order",
                    f"The index {node.index_name or pattern} puts the range field {wrong[0][0]} before the equality field {wrong[0][1]}, so it scans every key in the range.",
                    {"index": node.index_name, "key_order": pattern, "equality_fields": equality, "range_fields": ranges, "recommended_order": recommended},
                    [node.node_id], 0.9 if ctx.executed else 0.8,
                    [f"Create an index on ({', '.join(recommended)}): equality first, then sort, then range", "Drop the old index once the new one is verified if no other query needs it"],
                )
            )
        return out
