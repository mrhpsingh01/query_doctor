"""Turn a query's column usage into index candidates. Candidates are *suggestions*: nothing here is created anywhere."""

from __future__ import annotations

import re

from querydoctor.candidates.extraction import TableUsage, extract_usage
from querydoctor.candidates.models import IndexCandidate, IndexColumn
from querydoctor.diagnosis import Finding
from querydoctor.schema import SchemaSnapshot

MAX_KEY_COLUMNS = 4
MAX_INCLUDE_COLUMNS = 5
_PLAIN = re.compile(r"[a-z_][a-z0-9_$]*")
_RESERVED = {"user", "order", "group", "table", "select", "where", "from", "limit", "offset", "index", "primary", "check"}


def ident(name: str) -> str:
    return name if _PLAIN.fullmatch(name) and name not in _RESERVED else '"' + name.replace('"', '""') + '"'


def build_ddl(table: str, columns: list[IndexColumn], include: list[str], predicate: str | None, expression: str | None) -> str:
    keys = [expression] if expression else [ident(c.name) + (" DESC" if c.desc else "") for c in columns]
    ddl = f"CREATE INDEX ON {ident(table)} ({', '.join(keys)})"
    if include:
        ddl += f" INCLUDE ({', '.join(ident(c) for c in include)})"
    if predicate:
        ddl += f" WHERE {predicate}"
    return ddl


def _key_columns(u: TableUsage) -> list[IndexColumn]:
    """Equality, then join keys, then range, then ordering columns (the standard B-tree column-order heuristic)."""
    ordering = dict(u.order_by)
    key: list[IndexColumn] = []

    def push(name: str) -> None:
        if all(c.name != name for c in key):
            key.append(IndexColumn(name=name, desc=ordering.get(name, False)))

    for name in [*u.equality, *u.join, *u.range, *(c for c, _ in u.order_by)]:
        push(name)
    return key[:MAX_KEY_COLUMNS]


def _candidate(table, columns, include, predicate, expression, rationale, rule_ids) -> IndexCandidate:
    ddl = build_ddl(table, columns, include, predicate, expression)
    return IndexCandidate(
        id=IndexCandidate.make_id(ddl), table=table, columns=columns, include=include, predicate=predicate,
        expression=expression, ddl=ddl, rationale=rationale, source_rule_ids=rule_ids, validation="suggested",
    )


def generate_candidates(sql: str, schema: SchemaSnapshot, findings: list[Finding]) -> list[IndexCandidate]:
    candidates: dict[str, IndexCandidate] = {}
    for table, usage in extract_usage(sql, schema).items():
        if table not in schema.tables:  # CTEs, derived tables and unknown relations have nothing to index
            continue
        rule_ids = sorted({f.rule_id for f in findings if f.evidence.get("table") == table})
        key = _key_columns(usage)
        names = [c.name for c in key]

        reasons = [f"equality on {c}" for c in usage.equality if c in names]
        reasons += [f"{c} is a join key" for c in usage.join if c in names]
        reasons += [f"range on {c}" for c in usage.range if c in names]
        reasons += [f"ORDER BY {c}{' DESC' if d else ''} can follow the index order" for c, d in usage.order_by if c in names]

        def add(candidate: IndexCandidate) -> None:
            candidates.setdefault(candidate.id, candidate)

        if key:
            add(_candidate(table, key, [], None, None, reasons, rule_ids))
            include = [c for c in usage.output if c not in names][:MAX_INCLUDE_COLUMNS]
            if include and not usage.select_star:
                add(_candidate(
                    table, key, include, None, None,
                    [*reasons, "INCLUDE adds the returned columns so the query can be answered from the index alone"],
                    rule_ids,
                ))
            for column, literal in usage.constants.items():
                rest = [c for c in key if c.name != column]
                if column in names and rest:
                    add(_candidate(
                        table, rest, [], f"{column} = {literal}", None,
                        [*reasons, f"partial index: only rows where {column} = {literal} are indexed, keeping it small"],
                        rule_ids,
                    ))
        for expression in usage.expressions:
            add(_candidate(
                table, [], [], None, expression,
                [f"the query filters on {expression}; a plain index on the column cannot serve it"], rule_ids,
            ))
    return list(candidates.values())
