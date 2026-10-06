"""Throwaway MongoDB databases for benchmarks.

A sandbox is a ``bench_<12 hex>`` database on the same server, filled from the sample database with ``$out`` (documents and
the existing indexes), so a real index can be built and timed without touching the source. The application only ever names
databases that match the pattern below; MongoDB roles cannot enforce that themselves (see docs/security.md).
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from querydoctor.benchmark.runner import BenchmarkBusy

NAME_PATTERN = re.compile(r"^bench_[0-9a-f]{12}$")
META = "_qd_meta"
LOCK_DB = "qd_bench_locks"
MAX_CONCURRENT = 2
SLOT_TTL_S = 300  # a crashed holder's slot is reclaimable after this long
SYSTEM_DBS = {"admin", "local", "config"}
_INDEX_OPTIONS = ("unique", "sparse", "expireAfterSeconds", "partialFilterExpression", "collation")


class SandboxError(RuntimeError):
    pass


def is_sandbox_name(name: str) -> bool:
    return bool(NAME_PATTERN.match(name))


class SlotLease:
    """One of the MAX_CONCURRENT benchmark slots, held for the duration of a run (and reclaimable if its holder dies)."""

    def __init__(self, client: MongoClient, slot: int, token: str) -> None:
        self._client, self.slot, self.token = client, slot, token

    def release(self) -> None:
        try:
            self._client[LOCK_DB]["slots"].delete_one({"_id": self.slot, "owner": self.token})
        except PyMongoError:
            pass  # the TTL reclaims it


def acquire_slot(client: MongoClient, *, ttl_s: int = SLOT_TTL_S) -> SlotLease:
    slots = client[LOCK_DB]["slots"]
    slots.create_index("expires_at", expireAfterSeconds=0)  # the server also sweeps expired slots, a minute at a time
    token = uuid.uuid4().hex
    for slot in range(MAX_CONCURRENT):
        now = datetime.now(UTC)
        slots.delete_one({"_id": slot, "expires_at": {"$lt": now.replace(tzinfo=None)}})  # reclaim a dead holder's slot now
        try:
            slots.insert_one({"_id": slot, "owner": token, "expires_at": (now + timedelta(seconds=ttl_s)).replace(tzinfo=None)})
            return SlotLease(client, slot, token)
        except DuplicateKeyError:
            continue
    raise BenchmarkBusy("Too many benchmarks are running right now. Try again in a moment.")


class MongoSandbox:
    def __init__(self, client: MongoClient, source_db: str = "qd_sample") -> None:
        self.client = client
        self.source_db = source_db
        self.name: str | None = None

    @property
    def db(self):
        if self.name is None:
            raise SandboxError("the sandbox has not been created")
        return self.client[self.name]

    def create(self, collections: Sequence[str]) -> str:
        name = f"bench_{uuid.uuid4().hex[:12]}"
        self.name = name  # set first so a failed copy is still dropped by drop()
        try:
            self.client[name][META].insert_one({"_id": "sandbox", "created": int(time.time()), "source": self.source_db})
            source = self.client[self.source_db]
            for collection in dict.fromkeys(collections):
                if collection not in source.list_collection_names():
                    raise SandboxError(f"the collection {collection!r} does not exist in the source database")
                source[collection].aggregate([{"$out": {"db": name, "coll": collection}}])  # documents only
                for index in source[collection].list_indexes():
                    if index["name"] == "_id_":
                        continue
                    options = {k: index[k] for k in _INDEX_OPTIONS if k in index}
                    self.client[name][collection].create_index(list(index["key"].items()), name=index["name"], **options)
        except PyMongoError as exc:
            raise SandboxError("the benchmark copy could not be prepared") from exc
        return name

    def drop(self) -> None:
        name, self.name = self.name, None
        if name and is_sandbox_name(name):
            self.client.drop_database(name)

    def __enter__(self) -> MongoSandbox:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.drop()


def leftover_sandboxes(client: MongoClient) -> list[str]:
    return [n for n in client.list_database_names() if is_sandbox_name(n)]


def drop_stale(client: MongoClient, max_age_s: float) -> int:
    """Drop sandboxes left behind by crashed workers that are at least ``max_age_s`` old. Returns how many were dropped."""
    dropped = 0
    now = time.time()
    for name in leftover_sandboxes(client):
        meta = client[name][META].find_one({"_id": "sandbox"})
        created = (meta or {}).get("created", 0)  # a sandbox with no marker is treated as ancient
        if now - created >= max_age_s:
            client.drop_database(name)
            dropped += 1
    return dropped
