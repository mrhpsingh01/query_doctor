import hashlib
import uuid
from datetime import UTC, datetime
from bson import Binary, Decimal128, Int64, ObjectId
import pytest
from pymongo import MongoClient
from labdsn import MONGO_BENCH_URI
from querydoctor.mongo import validate_mongo
from querydoctor.mongo.checksum import checksum_function, result_checksum

pytestmark = pytest.mark.integration


def test_server_sha256_matches_known_answers_and_bson_types_remain_distinct():
    with MongoClient(MONGO_BENCH_URI, serverSelectionTimeoutMS=3000) as client:
        name = 'bench_' + uuid.uuid4().hex[:12]
        db = client[name]
        try:
            db.probe.insert_one({'_id':1})
            # Exercise the exact bundled implementation inside MongoDB, including UTF-8 and block boundaries.
            body = checksum_function().replace('window.sha256(tojson(doc))', 'window.sha256(doc)')
            for value in ['', 'abc', '🌍 database Καλημέρα', 'x' * 1024]:
                row = next(db.probe.aggregate([{'$project': {'value': {'$function': {'body': body, 'args': [value], 'lang': 'js'}}}}]))
                assert row['value'] == hashlib.sha256(value.encode()).hexdigest()
            q = validate_mongo('{"find":"probe","filter":{},"projection":{"_id":0}}', [])
            values = [42, '42', Int64(42), Decimal128('42'), Binary(b'42'), ObjectId(), datetime.now(UTC), {'a':42}, [42]]
            checks = []
            for value in values:
                db.probe.delete_many({})
                db.probe.insert_one({'_id':1, 'value':value})
                checks.append(result_checksum(db,q))
            assert len(set(checks)) == len(values)
            original = checks[-1]
            db.probe.insert_one({'_id':2,'value':[42]})
            assert result_checksum(db,q) != original  # duplicate multiplicity matters
            db.probe.delete_many({})
            assert result_checksum(db,q) == '0:' + hashlib.sha256(b'').hexdigest()
        finally:
            client.drop_database(name)
