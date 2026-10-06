import psycopg
import pytest
from labdsn import TARGET_RO_DSN

from querydoctor.db.schema_collect import collect_schema, distinct_fraction
from querydoctor.schema import ColumnStats, TableInfo


def test_distinct_fraction_handles_the_absolute_and_the_negative_fraction_forms():
    assert distinct_fraction(-0.25, 1000) == 0.25  # negative: already a fraction of the row count
    assert distinct_fraction(-1.0, 1000) == 1.0  # unique
    assert distinct_fraction(50, 1000) == 0.05  # absolute count
    assert distinct_fraction(5000, 1000) == 1.0  # a stale estimate above the row count is capped
    assert distinct_fraction(10, 0) == 1.0  # nothing known about the row count


def test_extras_default_to_none_on_a_plain_snapshot():
    info = TableInfo(name="t", row_estimate=1)
    assert info.size_bytes is None and info.write_ratio is None and info.column_stats == {}
    assert ColumnStats(n_distinct_fraction=0.5, null_frac=0.0, correlation=1.0).correlation == 1.0


@pytest.mark.integration
def test_extras_describe_the_orders_table():
    with psycopg.connect(TARGET_RO_DSN) as conn:
        info = collect_schema(conn, ["orders"], extras=True).tables["orders"]
    assert info.size_bytes > 1_000_000
    assert 0.0 <= info.write_ratio <= 1.0
    customer = info.column_stats["customer_id"]
    assert 0.0 < customer.n_distinct_fraction <= 1.0 and customer.n_distinct_fraction < 0.5  # few customers, many orders
    created = info.column_stats["created_at"]
    assert abs(created.correlation) > 0.9  # rows were inserted in created_at order
    assert info.column_stats["id"].n_distinct_fraction == 1.0
    assert all(s.null_frac == 0.0 for s in (customer, created))


@pytest.mark.integration
def test_without_extras_the_extra_fields_stay_empty():
    with psycopg.connect(TARGET_RO_DSN) as conn:
        info = collect_schema(conn, ["orders"]).tables["orders"]
    assert info.size_bytes is None and info.write_ratio is None and info.column_stats == {}


@pytest.mark.integration
def test_a_table_without_statistics_has_no_column_stats_and_unknown_tables_are_omitted():
    with psycopg.connect(TARGET_RO_DSN) as conn:
        snapshot = collect_schema(conn, ["orders", "no_such_table"], extras=True)
    assert "no_such_table" not in snapshot.tables
    # pg_stats has no row for a column that was never analysed: the stats map simply lacks it
    for name, stats in snapshot.tables["orders"].column_stats.items():
        assert name in snapshot.tables["orders"].columns
        assert stats.n_distinct_fraction > 0
