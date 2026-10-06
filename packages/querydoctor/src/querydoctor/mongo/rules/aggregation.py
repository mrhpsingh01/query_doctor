"""Rules about aggregation pipelines: unbounded work, expensive lookups and stage order."""

from __future__ import annotations

from querydoctor.diagnosis.models import Finding
from querydoctor.mongo.rules.base import MongoContext, finding

HEAVY_STAGES = {"$group", "$sortByCount", "$bucket", "$bucketAuto", "$facet"}


class UnboundedAggregation:
    rule_id = "MONGO_UNBOUNDED_AGG"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t = ctx.thresholds
        for i, stage in enumerate(ctx.pipeline()):
            name = next(iter(stage), "")
            if name in ("$match", "$limit", "$sample"):
                return []  # the input is bounded before anything heavy runs
            if name in HEAVY_STAGES:
                cursor = next(iter(ctx.nodes("$cursor")), None)
                docs = int(cursor.actual_rows) if ctx.executed and cursor and cursor.actual_rows is not None else ctx.count_of(ctx.collection)
                if docs is None or docs < t.unbounded_min_docs:
                    return []
                return [
                    finding(
                        self.rule_id, "warning", "Aggregation over the whole collection",
                        f"{name} receives all {docs:,} documents of {ctx.collection}: nothing narrows or limits the input first.",
                        {"stage": name, "stage_index": i, "docs_in": docs, "collection": ctx.collection}, [], 0.9 if ctx.executed else 0.6,
                        ["Add a $match before the grouping stage to narrow the input", "Add an index that supports the $match, or pre-aggregate the result", "Use $limit if only the top groups matter"],
                    )
                ]
        return []


class LargeLookup:
    rule_id = "MONGO_LARGE_LOOKUP"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t, out = ctx.thresholds, []
        for node in ctx.nodes("$lookup", "EQ_LOOKUP"):
            args = node.raw.get("args") or node.raw
            target = args.get("from") or args.get("foreignCollection")
            foreign = args.get("foreignField") or args.get("joinFieldForeign")
            info = ctx.collections.get(target or "")
            indexed = info.indexed_prefix(foreign) if info and foreign else None
            scans = node.raw.get("collectionScans")
            if ctx.executed and node.docs_examined is not None:
                if node.docs_examined < t.lookup_min_docs:
                    continue
                severity, docs, confidence = ("critical" if scans else "warning"), node.docs_examined, 0.95
            elif info and indexed is False and info.count >= t.lookup_min_docs:
                severity, docs, confidence = "warning", info.count, 0.6  # planner only: each looked-up row scans the target
            else:
                continue
            out.append(
                finding(
                    self.rule_id, severity, "$lookup without a supporting index",
                    f"The join into {target} on {foreign} examined {docs:,} documents" + (f" in {scans:,} collection scans." if scans else "."),
                    {"from": target, "foreign_field": foreign, "docs_examined": docs, "collection_scans": scans, "indexed": indexed},
                    [node.node_id], confidence,
                    [f"Create an index on {target}.{foreign}", "Filter the local collection before the $lookup so fewer rows are joined"],
                )
            )
        return out


def _fields_of(match: dict) -> set[str]:
    return {k.split(".")[0] for k in match if not k.startswith("$")}


class StageOrder:
    rule_id = "MONGO_STAGE_ORDER"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        pipeline, out = ctx.pipeline(), []
        for i, stage in enumerate(pipeline):
            if "$match" not in stage or i == 0:
                continue
            fields = _fields_of(stage["$match"])
            previous = pipeline[i - 1]
            before = next(iter(previous), "")
            reason = None
            if before == "$sort" and fields:
                reason = "$match after $sort sorts documents that are then discarded"
            elif before == "$group" and fields == {"_id"} and isinstance(previous["$group"].get("_id"), str) and previous["$group"]["_id"].startswith("$"):
                key = previous["$group"]["_id"][1:]
                reason = f"every document is grouped by {key} and all groups but the matching ones are then discarded"
            elif before == "$project" and fields and all(previous["$project"].get(f) in (1, True) for f in fields):
                reason = "$match after $project could filter before reshaping every document"
            if reason is None:
                continue
            out.append(
                finding(
                    self.rule_id, "warning" if before == "$group" else "info", "A $match that could run earlier",
                    f"The $match at stage {i + 1} follows {before}: {reason}.",
                    {"stage_index": i, "after": before, "match_fields": sorted(fields)}, [], 0.9,
                    ["Move the $match before the grouping, sorting or projection so it uses an index and shrinks the input"],
                )
            )
        return out
