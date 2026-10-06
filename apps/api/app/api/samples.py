from fastapi import APIRouter, Request
from querydoctor.lab.catalog import build_catalog

router = APIRouter(prefix="/api/sample-queries", tags=["samples"])


@router.get("")
def list_sample_queries(request: Request):
    return [q.model_dump() for q in build_catalog(request.app.state.settings.scale)]
