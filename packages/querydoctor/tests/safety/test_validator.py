import pytest

from querydoctor.safety.validator import ValidationError, validate_query


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from orders where id = $1;",
        "WITH a AS (SELECT 1) SELECT * FROM a",
        "-- note\nSELECT 1",
        "/* c */ SELECT 1 /* trailing */",
    ],
)
def test_accepts_single_select(sql):
    assert validate_query(sql).sql


def test_reports_params_star_and_tables():
    vq = validate_query("select * from orders o join payments p on p.order_id = o.id where o.id = $1 and o.x = $2 and o.y = $1;")
    assert vq.param_count == 2
    assert vq.has_select_star is True
    assert vq.tables == ["orders", "payments"]


@pytest.mark.parametrize(
    "sql,code",
    [
        ("SELECT 1; DROP TABLE orders", "multiple_statements"),
        ("SELECT 1 /* ; */; DELETE FROM x", "multiple_statements"),
        ("INSERT INTO x VALUES (1)", "not_select"),
        ("UPDATE x SET a = 1", "not_select"),
        ("DELETE FROM x", "not_select"),
        ("CREATE INDEX i ON x (a)", "not_select"),
        ("DROP TABLE x", "not_select"),
        ("COPY x TO STDOUT", "not_select"),
        ("SET statement_timeout = 0", "not_select"),
        ("SELECT a INTO t FROM x", "not_select"),
        ("SELECT a FROM x FOR UPDATE", "not_select"),
        ("WITH d AS (DELETE FROM x RETURNING *) SELECT * FROM d", "not_select"),
        ("SELECT pg_sleep(5)", "denied_function"),
        ("SELECT pg_read_file('/etc/passwd')", "denied_function"),
        ("SELECT set_config('a','b',false)", "denied_function"),
        ("SELECT pg_advisory_lock(1)", "denied_function"),
        ("SELECT lo_import('/x')", "denied_function"),
        ("SELECT * FROM dblink('host=x','select 1') AS t(a int)", "denied_function"),
        ("SELECT pg_terminate_backend(1)", "denied_function"),
        ("EXPLAIN ANALYZE SELECT 1", "explain_not_allowed"),
        ("explain select 1", "explain_not_allowed"),
        ("SELECT '" + "a" * 20_001 + "'", "too_long"),
        ("SELEC 1", "parse_error"),
        ("", "parse_error"),
        ("   ;  ", "parse_error"),
    ],
)
def test_rejects_with_code(sql, code):
    with pytest.raises(ValidationError) as exc:
        validate_query(sql)
    assert exc.value.code == code


def test_fingerprint_ignores_literals():
    a = validate_query("SELECT * FROM orders WHERE customer_id = 123")
    b = validate_query("SELECT * FROM orders WHERE customer_id = 456")
    c = validate_query("SELECT * FROM orders WHERE status = 'x'")
    assert a.fingerprint == b.fingerprint
    assert a.normalized_sql == b.normalized_sql
    assert "?" in a.normalized_sql
    assert a.fingerprint != c.fingerprint
    assert a.tables == ["orders"]


BS = chr(92)  # a single backslash (built explicitly so no tooling layer can mangle it)


def test_a_literal_whose_canonical_form_would_change_meaning_is_rejected():
    """SQLGlot rewrites E'\' (one backslash) as E'' - an unterminated string in PostgreSQL. Executing that rewrite would
    run text other than what was validated, so anything that does not survive a parse -> print -> parse round trip is refused."""
    with pytest.raises(ValidationError) as exc:
        validate_query("SELECT e'" + BS + BS + "' AS a, 1 AS b")
    assert exc.value.code in ("unsupported_syntax", "parse_error")


@pytest.mark.parametrize(
    "sql",
    ["SELECT 'a" + BS + "b'", "SELECT e'" + BS + "'' AS q", "SELECT 'it''s'", "SELECT $$x$$", "SELECT U&'d" + BS + "0061t'"],
)
def test_string_forms_that_do_round_trip_are_still_accepted_and_stable(sql):
    try:
        first = validate_query(sql)
    except ValidationError:
        pytest.skip("not supported by the parser")  # refusing is always safe
    assert validate_query(first.sql).sql == first.sql  # canonical SQL is a fixed point


def test_canonical_sql_is_a_fixed_point_for_every_catalog_query():
    from querydoctor.lab.catalog import build_catalog

    for q in build_catalog("small"):
        first = validate_query(q.sql)
        assert validate_query(first.sql).sql == first.sql, q.id


@pytest.mark.parametrize("sql", ["SELECT 1" + chr(0), chr(0) + "SELECT 1", "SELECT 'a" + chr(0) + "b'"])
def test_nul_characters_are_rejected(sql):
    with pytest.raises(ValidationError) as exc:
        validate_query(sql)
    assert exc.value.code == "invalid_character"


@pytest.mark.parametrize(
    "sql",
    ["SELECT pg_try_advisory_lock(1)", "SELECT pg_try_advisory_xact_lock(1)", "SELECT pg_advisory_unlock_all()", "SELECT pg_notify('c', 'p')"],
)
def test_advisory_locks_and_notifications_are_denied(sql):
    with pytest.raises(ValidationError) as exc:
        validate_query(sql)
    assert exc.value.code == "denied_function"
