from typing import Literal

from fastapi import APIRouter, Request
from querydoctor.lab.catalog import build_catalog
from querydoctor.lab.mongo_catalog import build_mongo_catalog

router = APIRouter(prefix="/api/sample-queries", tags=["samples"])


@router.get("")
def list_sample_queries(request: Request, engine: Literal["postgres", "mongodb"] = "postgres"):
    """The curated slow queries of one engine (PostgreSQL by default)."""
    scale = request.app.state.settings.scale
    catalog = build_mongo_catalog(scale) if engine == "mongodb" else build_catalog(scale)
    return [q.model_dump() for q in catalog]
