import pytest
from labdsn import MONGO_ROOT_URI
from pymongo import MongoClient

from querydoctor.lab.generator import COUNTS, customer_order_count, generate, top_customers
from querydoctor.lab.mongo_seed import INDEXES, seed_mongo

pytestmark = pytest.mark.integration
DB = "qd_seedtest"


@pytest.fixture(scope="module")
def counts():
    result = seed_mongo(MONGO_ROOT_URI, "tiny", db=DB)
    yield result
    with MongoClient(MONGO_ROOT_URI) as client:
        client.drop_database(DB)


@pytest.fixture
def db():
    with MongoClient(MONGO_ROOT_URI) as client:
        yield client[DB]


def test_document_counts_match_the_sql_lab_at_the_same_scale(counts):
    expected = COUNTS["tiny"]
    assert counts == {"customers": expected["customers"], "orders": expected["orders"], "events": expected["events"]}


def test_orders_embed_their_items_and_payment_like_the_sql_rows_they_came_from(counts, db):
    order = db["orders"].find_one({"payment": {"$ne": None}, "items.0": {"$exists": True}})
    assert set(order) == {"_id", "customer_id", "status", "total_amount", "currency", "created_at", "shipped_at", "items", "payment"}
    assert all(set(i) == {"product_id", "quantity", "unit_price"} for i in order["items"])
    assert set(order["payment"]) == {"amount", "method", "paid_at"}
    # parity with the SQL generator: the same number of items and of paid orders
    n_items = sum(len(batch) for table, batches in generate("tiny") if table == "order_items" for batch in batches)
    n_payments = sum(len(batch) for table, batches in generate("tiny") if table == "payments" for batch in batches)
    assert sum(len(d["items"]) for d in db["orders"].find({}, {"items": 1})) == n_items
    assert db["orders"].count_documents({"payment": {"$ne": None}}) == n_payments
    assert db["orders"].count_documents({"items": []}) >= 0


def test_the_skew_is_the_same_as_the_sql_labs(counts, db):
    heavy = top_customers("tiny", 1)[0]
    assert db["orders"].count_documents({"customer_id": heavy}) == customer_order_count("tiny", heavy)
    assert db["orders"].count_documents({"customer_id": heavy}) > 10 * db["orders"].count_documents({}) / COUNTS["tiny"]["customers"]


def test_ids_and_values_are_plain_json_scalars_with_text_timestamps(counts, db):
    customer = db["customers"].find_one({"_id": 1})
    assert customer["email"] == "User1@Example.com" and isinstance(customer["created_at"], str) and "T" in customer["created_at"]
    event = db["events"].find_one()
    assert set(event) == {"_id", "customer_id", "event_type", "payload", "created_at"}


def test_exactly_the_intended_indexes_exist(counts, db):
    for collection in ("customers", "orders", "events"):
        names = {i["name"] for i in db[collection].list_indexes()} - {"_id_"}
        assert names == {name for _keys, name in INDEXES[collection]}, collection
    assert "customer_id_1" not in {i["name"] for i in db["orders"].list_indexes()}  # missing on purpose
    events = {i["name"]: list(i["key"].keys()) for i in db["events"].list_indexes()}
    assert events["event_type_1"] == ["event_type"] and events["event_type_1_created_at_1"] == ["event_type", "created_at"]  # a redundant pair


def test_the_profiler_records_every_operation(counts, db):
    assert db.command("profile", -1)["was"] == 2
    db["customers"].find_one({"_id": 5})
    assert db["system.profile"].count_documents({"ns": f"{DB}.customers"}) >= 1


def test_seeding_is_deterministic_and_if_empty_skips_a_finished_seed(counts, db):
    first = db["orders"].find_one({"_id": 1})
    assert seed_mongo(MONGO_ROOT_URI, "tiny", db=DB, if_empty=True) == {}  # nothing is touched
    assert db["orders"].find_one({"_id": 1}) == first
    assert seed_mongo(MONGO_ROOT_URI, "tiny", db=DB) == counts
    assert db["orders"].find_one({"_id": 1}) == first


def test_an_unknown_scale_is_refused():
    with pytest.raises(ValueError):
        seed_mongo(MONGO_ROOT_URI, "huge", db=DB)
