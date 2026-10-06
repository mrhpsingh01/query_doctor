"""Rules about how much work a plan does to find what it returns: collection scans, scan and key amplification."""

from __future__ import annotations

from querydoctor.diagnosis.models import Finding
from querydoctor.mongo.rules.base import MongoContext, finding


class CollScan:
    rule_id = "MONGO_COLLSCAN"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t, out = ctx.thresholds, []
        for node in ctx.nodes("COLLSCAN"):
            if not node.filter_condition:
                continue  # scanning everything with no filter is what the query asked for
            name = node.relation_name or ctx.collection
            count = ctx.count_of(name)
            if ctx.executed and node.docs_examined is not None:
                docs, confidence = node.docs_examined, 0.95
            elif count is not None:
                docs, confidence = count, 0.6  # planner only: the whole collection will be read
            else:
                continue
            if docs < t.collscan_min_docs:
                continue
            waste = node.filter_waste
            out.append(
                finding(
                    self.rule_id, "critical" if waste is not None and waste >= t.waste_critical else "warning", "Collection scan",
                    f"Every document of {name or 'the collection'} ({docs:,}) is read to apply a filter"
                    + (f"; {node.actual_rows:,.0f} match." if node.actual_rows is not None else "."),
                    {"collection": name, "docs_examined": docs, "returned": node.actual_rows, "filter_waste": waste, "filter": node.filter_condition},
                    [node.node_id], confidence,
                    ["Create an index on the filtered fields (equality fields first)", "Check that the filter field is the one you meant"],
                )
            )
        return out


class ScanAmplification:
    rule_id = "MONGO_SCAN_AMPLIFICATION"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t, out = ctx.thresholds, []
        if not ctx.executed:
            return out
        for node in ctx.nodes("FETCH", "COLLSCAN"):
            docs = node.docs_examined
            if docs is None or docs < t.scan_amp_min_docs:
                continue
            returned = node.actual_rows or 0
            ratio = docs / max(returned, 1)  # never divides by zero: no result counts as one
            if ratio < t.scan_amp_warn:
                continue
            out.append(
                finding(
                    self.rule_id, "critical" if ratio >= t.scan_amp_critical else "warning", "Documents examined far exceed documents returned",
                    f"{docs:,} documents were examined to return {returned:,.0f}: {ratio:,.0f} per result.",
                    {"docs_examined": docs, "returned": returned, "ratio": ratio, "stage": node.node_type, "index": node.index_name},
                    [node.node_id], 0.9,
                    ["Add or reorder an index so the filter is applied before documents are fetched", "Put equality fields first, then sort fields, then range fields"],
                )
            )
        return out


class KeyAmplification:
    rule_id = "MONGO_KEY_AMPLIFICATION"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t, out = ctx.thresholds, []
        if not ctx.executed:
            return out
        for node in ctx.nodes("IXSCAN"):
            keys = node.keys_examined
            if keys is None or keys < t.key_amp_min_keys:
                continue
            returned = node.actual_rows or 0
            ratio = keys / max(returned, 1)
            if ratio < t.key_amp_warn:
                continue
            out.append(
                finding(
                    self.rule_id, "critical" if ratio >= t.key_amp_critical else "warning", "Index keys examined far exceed keys returned",
                    f"{keys:,} index keys were scanned to return {returned:,.0f} ({ratio:,.0f} per result) using {node.index_name or 'an index'}.",
                    {"keys_examined": keys, "returned": returned, "ratio": ratio, "index": node.index_name, "bounds": node.index_condition},
                    [node.node_id], 0.9,
                    ["Reorder the compound index: equality fields, then sort fields, then range fields", "Add an index whose leading field is the most selective equality"],
                )
            )
        return out
