"""Capture MongoDB explain output for every catalog query into fixtures/mongo_plans (run against the lab stack).

    python scripts/capture_mongo_plans.py
"""

import json
import pathlib

from pymongo import MongoClient

from querydoctor.lab.mongo_catalog import MONGO_CATALOG
from querydoctor.mongo import explain_query, validate_mongo

URI = "mongodb://qd_readonly:qd_readonly@127.0.0.1:27018/qd_sample?authSource=admin"
OUT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "mongo_plans"
VOLATILE = ("serverInfo", "serverParameters", "ok", "$clusterTime", "operationTime", "command")


def clean(doc):
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items() if k not in VOLATILE}
    if isinstance(doc, list):
        return [clean(v) for v in doc]
    return doc


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    with MongoClient(URI) as client:
        db = client["qd_sample"]
        for q in MONGO_CATALOG:
            vq = validate_mongo(q.sql, list(q.params))
            for execute, suffix in ((True, "stats"), (False, "planner")):
                raw = clean(explain_query(db, vq, q.params, execute=execute))
                (OUT / f"{q.id}.{suffix}.json").write_text(json.dumps(raw, indent=1, default=str), encoding="utf-8")
    print("captured", len(list(OUT.glob("*.json"))), "plans")
