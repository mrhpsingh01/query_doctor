"""Which columns of which tables a query filters, joins, sorts, groups and returns - the raw material for index candidates."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlglot import exp
import sqlglot
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from querydoctor.schema import SchemaSnapshot

_RANGE = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)
_COMPARISONS = (exp.EQ, exp.In, exp.NEQ, exp.Like, exp.ILike, *_RANGE)


class TableUsage(BaseModel):
    equality: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)
    join: list[str] = Field(default_factory=list)
    order_by: list[tuple[str, bool]] = Field(default_factory=list)  # (column, descending)
    group_by: list[str] = Field(default_factory=list)
    output: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)  # e.g. "lower(email)" used in a predicate
    constants: dict[str, str] = Field(default_factory=dict)  # column -> literal it is compared to with =, e.g. 'pending'
    select_star: bool = False


def _add(items: list, value) -> None:
    if value not in items:
        items.append(value)


def _enclosing_select(node: exp.Expression) -> exp.Expression | None:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        parent = parent.parent
    return parent


def _locate(col: exp.Column, select: exp.Select) -> tuple[str | None, exp.Expression | None, exp.Expression]:
    """The clause of ``select`` holding ``col``, its nearest comparison, and the comparison's operand around it."""
    node: exp.Expression = col
    comparison: exp.Expression | None = None
    operand: exp.Expression = col
    while node.parent is not None and node.parent is not select:
        parent = node.parent
        if comparison is None and isinstance(parent, _COMPARISONS):
            comparison, operand = parent, node
        node = parent
    return node.arg_key, comparison, operand


def extract_usage(sql: str, schema: SchemaSnapshot | None = None) -> dict[str, TableUsage]:
    """Per-table column usage. Returns ``{}`` for SQL that cannot be analysed (the caller just gets no candidates)."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
        scopes = list(traverse_scope(tree)) if tree is not None else []
    except (SqlglotError, ValueError, KeyError, AttributeError):
        return {}

    usage: dict[str, TableUsage] = {}
    for scope in scopes:
        if isinstance(scope.expression, exp.Select):
            _scan_scope(scope, usage, schema)
    return usage


def _scan_scope(scope: Scope, usage: dict[str, TableUsage], schema: SchemaSnapshot | None) -> None:
    select: exp.Select = scope.expression
    local = {alias: src.name for alias, src in scope.sources.items() if isinstance(src, exp.Table)}
    for table in local.values():
        usage.setdefault(table, TableUsage())

    if any(
        isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star))
        for p in select.expressions
    ):
        for table in set(local.values()):
            usage[table].select_star = True

    def resolve(col: exp.Column) -> str | None:
        if col.table:
            return local.get(col.table)
        tables = set(local.values())
        if len(local) == 1:
            return next(iter(tables))
        if schema is not None:
            owners = [t for t in tables if t in schema.tables and col.name in schema.tables[t].columns]
            if len(owners) == 1:
                return owners[0]
        return None

    for col in scope.columns:
        outer_reference = bool(col.table) and col.table not in local  # belongs to an enclosing query's table
        if outer_reference or isinstance(col.this, exp.Star) or _enclosing_select(col) is not select:
            continue
        table = resolve(col)
        if table is None:
            continue
        clause, comparison, operand = _locate(col, select)
        u = usage[table]

        if clause == "expressions":
            _add(u.output, col.name)
        elif clause == "group":
            if isinstance(col.parent, exp.Group):  # plain GROUP BY column (not an expression)
                _add(u.group_by, col.name)
        elif clause == "order":
            ordered = col.parent
            if isinstance(ordered, exp.Ordered) and ordered.this is col:
                _add(u.order_by, (col.name, bool(ordered.args.get("desc"))))
        elif clause in ("where", "joins", "having") and comparison is not None:
            _classify_predicate(u, table, col, comparison, operand, local, resolve)


def _classify_predicate(
    u: TableUsage, table: str, col: exp.Column, cmp: exp.Expression, operand: exp.Expression, local, resolve
) -> None:
    if isinstance(cmp, (exp.In, exp.Between)) and cmp.this is not operand:
        return
    if operand is not col:  # a function or cast wraps the column: needs an expression index, not a plain one
        if len(list(operand.find_all(exp.Column))) == 1:
            _add(u.expressions, operand.sql(dialect="postgres").lower())
        return
    if isinstance(cmp, (exp.NEQ, exp.Like, exp.ILike)):
        return

    other = None
    if isinstance(cmp, (exp.EQ, *_RANGE)) and not isinstance(cmp, exp.Between):
        other = cmp.expression if cmp.this is operand else cmp.this
    if isinstance(other, exp.Column):
        in_scope = other.table in local or (not other.table and len(local) == 1)
        if in_scope:
            other_table = resolve(other)
            if other_table is not None and isinstance(cmp, exp.EQ):
                if other_table != table:
                    _add(u.join, col.name)
            return  # same-table or range column comparisons are not index-shaping
        # the other side comes from an outer query: this is a per-row lookup, i.e. an equality
    if isinstance(cmp, exp.EQ) and isinstance(other, exp.Literal):
        u.constants.setdefault(col.name, other.sql(dialect="postgres"))
    if isinstance(cmp, (exp.EQ, exp.In)):
        _add(u.equality, col.name)
    elif isinstance(cmp, _RANGE):
        _add(u.range, col.name)

