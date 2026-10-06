"""What the diagnosis and candidate code needs to know about the collections a query touches."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.database import Database

from querydoctor.mongo.rules.base import CollectionInfo, IndexSpec


class MongoSchema(BaseModel):
    collections: dict[str, CollectionInfo] = Field(default_factory=dict)


@contextmanager
def mongo_connect(uri: str, *, timeout_ms: int = 5000):
    """A handle on the database named in ``uri`` (read-only for the analysis user); closed when the block ends."""
    client: MongoClient = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
    try:
        yield client.get_default_database()
    finally:
        client.close()


def _spec(index: dict[str, Any]) -> IndexSpec:
    keys = [(field, int(direction) if isinstance(direction, (int, float)) else 1) for field, direction in index["key"].items()]
    return IndexSpec(
        name=index["name"], keys=keys, unique=bool(index.get("unique")), partial="partialFilterExpression" in index,
        ttl="expireAfterSeconds" in index,
    )


def collect_mongo_schema(db: Database, collections: Sequence[str]) -> MongoSchema:
    """Document counts and index lists for the named collections (unknown ones are omitted)."""
    existing = set(db.list_collection_names())
    out: dict[str, CollectionInfo] = {}
    for name in dict.fromkeys(collections):
        if name in existing:
            out[name] = CollectionInfo(count=db[name].estimated_document_count(), indexes=[_spec(i) for i in db[name].list_indexes()])
    return MongoSchema(collections=out)
