"""Pick the database adapter for an analysis, wired to the pipeline's (replaceable) database functions."""

from __future__ import annotations

from querydoctor.adapters import DatabaseAdapter, get_adapter


def adapter_for(deps, engine: str = "postgres") -> DatabaseAdapter:
    """The adapter for ``engine``. The PostgreSQL adapter delegates to ``deps.explain`` / ``collect_schema`` / ``scorer`` so
    tests and deployments can still substitute them."""
    if engine == "postgres":
        return get_adapter("postgres", explain=deps.explain, collect_schema=deps.collect_schema, scorer=deps.scorer)
    return get_adapter(engine)
