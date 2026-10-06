"""Pick the database adapter for an analysis, wired to the pipeline's (replaceable) database functions."""

from __future__ import annotations

from querydoctor.adapters import DatabaseAdapter, get_adapter


def adapter_for(deps, engine: str = "postgres") -> DatabaseAdapter:
    """The adapter for ``engine``. Each adapter delegates to the functions on ``deps`` when they are set, so tests and
    deployments can still substitute them."""
    if engine == "postgres":
        return get_adapter("postgres", explain=deps.explain, collect_schema=deps.collect_schema, scorer=deps.scorer)
    if engine == "mongodb":
        extras = {k: v for k, v in (("explain", deps.mongo_explain), ("collect_schema", deps.mongo_collect_schema)) if v is not None}
        return get_adapter("mongodb", **extras)
    return get_adapter(engine)
