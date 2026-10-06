from fastapi import APIRouter, Request

router = APIRouter(tags=["privacy"])

STORES = [
    "The canonical query text and the parameters you submit, in the QueryDoctor metadata database.",
    "The execution plan PostgreSQL returned for it, and a snapshot of schema metadata (table, column and index names, row estimates, sizes).",
    "Findings, index ideas and their evidence levels.",
    "Benchmark results: timings, plan fingerprints and checksums of the result (a hash, never the rows).",
    "Model predictions for index ideas, your feedback (thumbs up or down) on them, and explanations with the facts they used.",
    "Workload statistics imported from pg_stat_statements (normalised query text and counters).",
    "Custom database connections: host, port, database and user name, with the password stored encrypted.",
    "An audit trail of security-relevant actions (analysis started, benchmark approved, connection created or deleted).",
]
NEVER_STORES = [
    "Rows returned by your queries: only plans, counts and hashes are kept.",
    "Passwords in plain text, and connection strings in logs, responses or explanations.",
    "Anything sent to a language model other than the numbered facts of an analysis, and only to a model you run locally.",
]
DELETION = [
    "DELETE /api/analyses/{id} removes an analysis and everything under it: findings, index ideas, predictions, benchmarks, explanations and feedback.",
    "DELETE /api/connections/{id} wipes a connection's stored credentials. It works even when custom connections are switched off.",
]


@router.get("/api/privacy")
def privacy(request: Request):
    return {
        "stores": STORES, "never_stores": NEVER_STORES, "deletion": DELETION,
        "custom_connections_enabled": bool(request.app.state.settings.allow_custom_connections),
    }
