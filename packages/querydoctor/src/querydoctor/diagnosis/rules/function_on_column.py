from __future__ import annotations

from querydoctor.diagnosis.models import AnalysisContext, Finding
from querydoctor.diagnosis.rules._sql import functions_on_columns
from querydoctor.plan_parser import PlanNode


class FunctionOnColumn:
    """A function or cast wrapped around an indexed column stops PostgreSQL from using that index."""

    rule_id = "PG_FUNCTION_ON_COLUMN"
    supported_node_types = {"Seq Scan"}

    def evaluate(self, node: PlanNode, context: AnalysisContext) -> Finding | None:
        indexes = context.schema_.indexes.get(node.relation_name or "", [])
        if not indexes or not node.filter_condition:
            return None
        blocked: list[dict] = []
        for function, column in functions_on_columns(node.filter_condition):
            leading = next((i for i in indexes if i.columns and i.columns[0] == column), None)
            if leading is not None:
                blocked.append({"function": function, "column": column, "blocked_index": leading.name})
        if not blocked:
            return None
        first = blocked[0]
        fn, col, table = first["function"], first["column"], node.relation_name
        fix = (
            f"Create an expression index on {table} ({fn}({col})) so the query can use it"
            if fn != "cast"
            else f"Compare {col} to a value of its own type, or create an expression index on {table} (({col})::<type>)"
        )
        return Finding(
            rule_id=self.rule_id,
            severity="warning",
            title="A function on the column prevents index use",
            summary=f"The filter applies {fn}() to {col}, so {first['blocked_index']} cannot be used and the table "
            "is scanned instead.",
            evidence={
                "table": table,
                "function": fn,
                "column": col,
                "blocked_index": first["blocked_index"],
                "filter": node.filter_condition,
                "all": blocked,
            },
            node_ids=[node.node_id],
            confidence=0.9,
            next_actions=[fix, f"Or rewrite the predicate so {col} is compared directly"],
        )
