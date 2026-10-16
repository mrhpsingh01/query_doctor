"""Cryptographic output checks for MongoDB 7 without exporting business documents.

A fixed internal $function hashes MongoDB's BSON-aware tojson representation inside the
server. Only sorted 64-character SHA-256 row digests leave it; the worker folds those
into SHA-256 and records the count. Duplicate rows remain distinct. No query-supplied
JavaScript is used, and no result document is logged or persisted.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.resources import files
from typing import Any

from querydoctor.mongo.query import ValidatedMongoQuery


@lru_cache(maxsize=1)
def checksum_function() -> str:
    source = files('querydoctor.mongo').joinpath('vendor/sha256.js').read_text(encoding='utf-8')
    return ('function(doc) { var window = {JS_SHA256_NO_NODE_JS: true, JS_SHA256_NO_COMMON_JS: true}; '
            'var module; var define; var self;\n' + source + '\nreturn window.sha256(tojson(doc)); }')


def checksum_pipeline(vq: ValidatedMongoQuery) -> tuple[str, list[dict[str, Any]]]:
    q = vq.query
    if q.kind == 'find':
        stages: list[dict[str, Any]] = [{'$match': q.filter}]
        if q.sort:
            stages.append({'$sort': q.sort})
        if q.skip:
            stages.append({'$skip': q.skip})
        if q.limit:
            stages.append({'$limit': q.limit})
        if q.projection:
            stages.append({'$project': q.projection})
    else:
        stages = list(q.pipeline or [])
    stages.extend([
        {'$project': {'_id': 0, 'qd_digest': {'$function': {
            'body': checksum_function(), 'args': ['$$ROOT'], 'lang': 'js',
        }}}},
        {'$sort': {'qd_digest': 1}},
    ])
    return q.collection, stages


def result_checksum(db, vq: ValidatedMongoQuery, check=lambda: None) -> str:
    collection, pipeline = checksum_pipeline(vq)
    digest, count = hashlib.sha256(), 0
    # The sort can spill in the disposable database. Client memory remains bounded to a cursor batch.
    with db[collection].aggregate(pipeline, maxTimeMS=60_000, allowDiskUse=True, batchSize=500) as rows:
        for row in rows:
            check()
            value = row['qd_digest']
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('MongoDB returned an invalid SHA-256 digest')
            digest.update(value.encode('ascii'))
            count += 1
    return f'{count}:{digest.hexdigest()}'
