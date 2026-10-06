"""Rules about the indexes themselves: ones that match too much, and ones that duplicate another."""

from __future__ import annotations

from querydoctor.diagnosis.models import Finding
from querydoctor.mongo.rules.base import IndexSpec, MongoContext, finding


class UnselectiveIndex:
    rule_id = "MONGO_UNSELECTIVE_INDEX"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        t, out = ctx.thresholds, []
        if not ctx.executed:
            return out
        count = ctx.count_of(ctx.collection)
        if not count:
            return out
        for node in ctx.nodes("IXSCAN"):
            keys, returned = node.keys_examined, node.actual_rows
            if keys is None or returned is None or keys < t.unselective_min_keys:
                continue
            share = returned / count
            if share < t.unselective_share:
                continue
            out.append(
                finding(
                    self.rule_id, "warning", "An index that matches most of the collection",
                    f"{node.index_name or 'The index'} matched {returned:,.0f} of {count:,} documents ({share:.0%}), so it saves little over reading everything.",
                    {"index": node.index_name, "matched": returned, "collection_docs": count, "share": share},
                    [node.node_id], 0.85,
                    ["Filter on a more selective field as well, or combine fields in a compound index", "Consider whether the query should read this many documents at all"],
                )
            )
        return out


def _covers(longer: IndexSpec, shorter: IndexSpec) -> bool:
    return len(longer.keys) > len(shorter.keys) and longer.keys[: len(shorter.keys)] == shorter.keys


class RedundantIndex:
    rule_id = "MONGO_REDUNDANT_INDEX"

    def evaluate(self, ctx: MongoContext) -> list[Finding]:
        out: list[Finding] = []
        names = ctx.query.collections if ctx.query else list(ctx.collections)
        for name in names:
            info = ctx.collections.get(name)
            if not info:
                continue
            candidates = [i for i in info.indexes if i.name != "_id_"]
            for index in candidates:
                if index.unique or index.partial or index.ttl:
                    continue  # these enforce or restrict something a prefix of another index cannot
                other = next((o for o in candidates if o is not index and _covers(o, index) and not o.partial), None)
                duplicate = next((o for o in candidates if o is not index and o.keys == index.keys and o.name < index.name and not o.partial), None)
                by = other or duplicate
                if by is None:
                    continue
                out.append(
                    finding(
                        self.rule_id, "info", "Redundant index",
                        f"{name}.{index.name} is a prefix of {by.name}, so queries it serves can use {by.name}; it only adds write and storage cost.",
                        {"collection": name, "index": index.name, "keys": [list(k) for k in index.keys], "covered_by": by.name}, [], 0.9,
                        [f"Drop {name}.{index.name} after confirming no query hints it"],
                    )
                )
        return out
