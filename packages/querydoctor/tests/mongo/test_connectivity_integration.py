import time
import uuid

import pytest
from labdsn import MONGO_BENCH_URI, MONGO_RO_URI, MONGO_ROOT_URI
from pymongo import MongoClient
from pymongo.errors import ExecutionTimeout, OperationFailure

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def sample_database():
    root = MongoClient(MONGO_ROOT_URI, serverSelectionTimeoutMS=3000)
    root["qd_sample"]["probe"].drop()
    root["qd_sample"]["probe"].insert_many([{"_id": i, "n": i} for i in range(50)])
    yield
    root["qd_sample"]["probe"].drop()
    root.close()


def roles_of(uri: str) -> list[dict]:
    with MongoClient(uri, serverSelectionTimeoutMS=3000) as client:
        status = client.admin.command({"connectionStatus": 1, "showPrivileges": False})
    return status["authInfo"]["authenticatedUserRoles"]


def test_the_readonly_user_can_read_the_sample_database_and_nothing_else():
    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        assert client["qd_sample"]["probe"].count_documents({}) == 50
        assert client["qd_sample"]["probe"].find_one({"_id": 7})["n"] == 7
        with pytest.raises(OperationFailure):
            client["qd_sample"]["probe"].insert_one({"_id": 999})
        with pytest.raises(OperationFailure):
            client["qd_sample"]["probe"].delete_many({})
        with pytest.raises(OperationFailure):
            client["qd_sample"]["probe"].create_index("n")
        with pytest.raises(OperationFailure):
            client["qd_sample"].drop_collection("probe")
        with pytest.raises(OperationFailure):
            client["other_db"]["x"].find_one()
        with pytest.raises(OperationFailure):
            client["bench_0123456789ab"]["x"].insert_one({"a": 1})


def test_the_bench_user_can_create_fill_and_drop_bench_databases_and_read_the_sample():
    name = f"bench_{uuid.uuid4().hex[:12]}"
    with MongoClient(MONGO_BENCH_URI, serverSelectionTimeoutMS=3000) as client:
        try:
            client[name]["c"].insert_many([{"a": i} for i in range(10)])
            client[name]["c"].create_index("a")
            assert client[name]["c"].count_documents({}) == 10
            assert name in client.list_database_names()
            # copying from the sample database with $out, the way the sandbox will
            list(client["qd_sample"]["probe"].aggregate([{"$match": {"n": {"$lt": 5}}}, {"$out": {"db": name, "coll": "probe_copy"}}]))
            assert client[name]["probe_copy"].count_documents({}) == 5
        finally:
            client.drop_database(name)
        assert name not in client.list_database_names()


def test_neither_application_user_is_a_root_or_owner():
    for uri, expected in ((MONGO_RO_URI, {("read", "qd_sample")}), (MONGO_BENCH_URI, {("qdBench", "admin")})):
        granted = {(r["role"], r["db"]) for r in roles_of(uri)}
        assert granted == expected, granted
        assert not any(role in {"root", "dbOwner", "userAdmin", "userAdminAnyDatabase", "dbAdminAnyDatabase", "readWriteAnyDatabase", "clusterAdmin"} for role, _db in granted)


def test_the_bench_user_cannot_administer_users_or_the_server():
    with MongoClient(MONGO_BENCH_URI, serverSelectionTimeoutMS=3000) as client:
        with pytest.raises(OperationFailure):
            client.admin.command("createUser", "intruder", pwd="x", roles=[])
        with pytest.raises(OperationFailure):
            client.admin.command("shutdown")
        with pytest.raises(OperationFailure):
            client.admin.command("serverStatus")


def test_max_time_ms_is_enforced_on_a_slow_operation():
    with MongoClient(MONGO_RO_URI, serverSelectionTimeoutMS=3000) as client:
        started = time.monotonic()
        with pytest.raises(ExecutionTimeout):
            list(client["qd_sample"]["probe"].find({"$where": "sleep(2000) || true"}).max_time_ms(300))
        assert time.monotonic() - started < 1.5
