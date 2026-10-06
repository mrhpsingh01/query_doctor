import logging
import time

import pytest

from app.database import make_engine
from app.logging_utils import RedactionFilter, install_redaction, redact, uninstall_redaction


def test_literals_in_sql_are_replaced_and_identifiers_are_kept():
    assert redact("SELECT * FROM t WHERE email = 'a@b.c' AND id = 42") == "SELECT * FROM t WHERE email = ? AND id = ?"
    assert redact('SELECT "Order Items".id, orders_2024.t1 FROM orders_2024 WHERE x = $1') == 'SELECT "Order Items".id, orders_2024.t1 FROM orders_2024 WHERE x = $1'


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT a FROM t WHERE n = 'it''s' AND m = 'x'", "SELECT a FROM t WHERE n = ? AND m = ?"),
        ("SELECT a FROM t WHERE n = E'a\\'b' ", "SELECT a FROM t WHERE n = E? "),
        ("SELECT a FROM t WHERE n = $$secret value$$", "SELECT a FROM t WHERE n = ?"),
        ("SELECT a FROM t WHERE n = $tag$sec'ret$tag$", "SELECT a FROM t WHERE n = ?"),
        ("SELECT a FROM t WHERE v > -5 AND w < 3.14 AND z = 1e5", "SELECT a FROM t WHERE v > ? AND w < ? AND z = ?"),
        ("SELECT a FROM t WHERE id IN (1, 2, 3) LIMIT 20 OFFSET 40", "SELECT a FROM t WHERE id IN (?, ?, ?) LIMIT ? OFFSET ?"),
        ("UPDATE t SET name = 'bob' WHERE id = 7", "UPDATE t SET name = ? WHERE id = ?"),
        ("INSERT INTO t (a, b) VALUES ('x', 9)", "INSERT INTO t (a, b) VALUES (?, ?)"),
        ("SELECT a FROM t WHERE n = 'unterminated", "SELECT a FROM t WHERE n = ?"),
    ],
)
def test_every_kind_of_literal_is_redacted_in_sql(sql, expected):
    assert redact(sql) == expected


def test_numbers_in_ordinary_log_messages_survive_so_logs_stay_useful():
    assert redact("analysis 12 failed after 340 ms on attempt 2") == "analysis 12 failed after 340 ms on attempt 2"
    assert "'" not in redact("could not translate host name 'secret-host.internal'")  # quoted text is still removed


def test_driver_error_details_and_parameter_dumps_are_redacted():
    sqlalchemy = "(psycopg.errors.UniqueViolation) duplicate key\n[SQL: INSERT INTO users (email) VALUES (%s)]\n[parameters: ('alice@example.com',)]"
    assert "alice" not in redact(sqlalchemy)
    pg = 'duplicate key value violates unique constraint "u"\nDETAIL:  Key (email)=(alice@example.com) already exists.'
    assert "alice" not in redact(pg) and "Key" in redact(pg)
    row = "null value in column \"x\"\nDETAIL:  Failing row contains (1, bob, 4242)."
    assert "bob" not in redact(row) and "4242" not in redact(row)


def test_redaction_is_linear_and_safe_on_hostile_input():
    started = time.monotonic()
    redact("SELECT " + "'" * 50_000 + " FROM t WHERE x = " + "9" * 200_000)
    redact("'a" * 100_000)
    assert time.monotonic() - started < 2.0


def record(msg, *args, exc_info=None):
    return logging.LogRecord("app.test", logging.WARNING, __file__, 1, msg, args or None, exc_info)


def test_the_filter_redacts_messages_arguments_nested_values_and_exceptions():
    f = RedactionFilter()
    r = record("query failed: %s", "SELECT 1 FROM t WHERE email = 'a@b.c'")
    f.filter(r)
    assert "a@b.c" not in r.getMessage()

    r = record("bad params %s and %s", ("alice@example.com", [42, {"k": "bob"}]), {"password": "hunter2"})
    f.filter(r)
    assert not any(secret in r.getMessage() for secret in ("alice", "bob", "hunter2"))

    r = record("failed: %s", ValueError("could not run SELECT x FROM t WHERE y = 'top-secret'"))
    f.filter(r)
    assert "top-secret" not in r.getMessage()

    r = record("%d rows in %s", 5, "analysis")  # harmless values keep their formatting
    f.filter(r)
    assert r.getMessage() == "5 rows in analysis"


def test_a_logged_exception_traceback_contains_no_literal(caplog):
    install_redaction()
    try:
        logger = logging.getLogger("app.exc")
        with caplog.at_level(logging.ERROR, logger="app.exc"):
            try:
                raise RuntimeError("syntax error in SELECT * FROM t WHERE token = 'abc123secret'")
            except RuntimeError:
                logger.exception("job crashed")
        text = caplog.text
        assert "job crashed" in text and "abc123secret" not in text
    finally:
        uninstall_redaction()


def test_installing_covers_every_logger_and_is_idempotent_and_reversible(caplog):
    install_redaction()
    install_redaction()
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("some.other.module").info("running SELECT * FROM t WHERE id = %s AND n = %s", 123, "x")
            logging.getLogger("sqlalchemy.engine").info("[parameters: ('alice@example.com', 7)]")
        assert "alice" not in caplog.text and "123" not in caplog.text and "?" in caplog.text
    finally:
        uninstall_redaction()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        logging.getLogger("x").info("analysis %s", 123)
    assert "123" in caplog.text


def test_engines_hide_statement_parameters_from_errors_and_logs():
    assert make_engine("sqlite://").hide_parameters is True
    assert make_engine("postgresql+psycopg://u:p@127.0.0.1:1/db").hide_parameters is True
