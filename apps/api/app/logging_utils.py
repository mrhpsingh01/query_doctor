"""Keep query literals and parameter values out of every log line.

Queries and their parameters can be personal data. Redaction runs when a log record is *created* (a record factory), so it
covers every logger, handler and library (SQLAlchemy, psycopg, Celery, uvicorn) without configuring each one.

* In text that looks like SQL, every string literal, dollar-quoted string and number becomes ``?``.
* Everywhere, quoted strings, SQLAlchemy ``[parameters: ...]`` dumps and PostgreSQL ``DETAIL`` row/key values are removed.
* Plain numbers in ordinary messages ("analysis 12 failed after 340 ms") are kept: they are what makes logs useful.
"""

from __future__ import annotations

import logging
import re
from typing import Any

PLACEHOLDER = "?"
_SQL_HINT = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|EXPLAIN|WITH|VALUES|FROM|WHERE|LIMIT)\b", re.IGNORECASE)
# Quoted text. An unterminated quote swallows the rest of the line, which keeps the scan linear on hostile input.
_STRING = re.compile(r"'(?:[^']|'')*(?:'|$)", re.DOTALL)
_ESCAPE_STRING = re.compile(r"(?<![\w$])[Ee]'(?:\\.|[^'\\]|'')*(?:'|$)", re.DOTALL)
_DOLLAR = re.compile(r"(?<![\w$])\$(?P<tag>[A-Za-z_][A-Za-z_0-9]*)?\$.*?\$(?P=tag)?\$", re.DOTALL)
_NUMBER = re.compile(r"(?<![\w$.\"])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w\"])")
_PARAMETERS = re.compile(r"\[parameters:.*?\](?=\s*(?:\(Background|$))", re.DOTALL | re.IGNORECASE)
_PARAMETERS_LINE = re.compile(r"\[parameters:[^\n]*", re.IGNORECASE)
_DETAIL_KEY = re.compile(r"(Key \([^)]*\))=\([^)]*\)")
_DETAIL_ROW = re.compile(r"(Failing row contains) \(.*?\)(?=\.|\s*$)", re.DOTALL)


def _redact_strings(text: str) -> str:
    text = _DOLLAR.sub(PLACEHOLDER, text)
    text = _ESCAPE_STRING.sub("E" + PLACEHOLDER, text)  # E'...' honours backslash escapes
    return _STRING.sub(PLACEHOLDER, text)


def redact(text: str) -> str:
    """The text with query literals and parameter values removed."""
    text = _PARAMETERS_LINE.sub("[parameters: ?]", text)
    text = _DETAIL_KEY.sub(r"\1=(?)", text)
    text = _DETAIL_ROW.sub(r"\1 (?)", text)
    redacted = _redact_strings(text)
    if _SQL_HINT.search(text):
        redacted = _NUMBER.sub(PLACEHOLDER, redacted)
    return redacted


def _scrub(value: Any, nested: bool) -> Any:
    """A log argument without its values. Containers are what parameter collections look like, so everything inside one
    is replaced; a plain string argument keeps its text with literals redacted."""
    if isinstance(value, str):
        return PLACEHOLDER if nested else redact(value)
    if isinstance(value, (list, tuple)):
        scrubbed = [_scrub(v, True) for v in value]
        return scrubbed if isinstance(value, list) else tuple(scrubbed)
    if isinstance(value, dict):
        return {k: _scrub(v, True) for k, v in value.items()}
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return PLACEHOLDER if nested else value
    return redact(str(value))  # exceptions, objects: their text may quote a literal


def scrub_record(record: logging.LogRecord) -> None:
    sql_like = isinstance(record.msg, str) and bool(_SQL_HINT.search(record.msg))
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    else:
        record.msg = redact(str(record.msg))
    if record.args:
        if isinstance(record.args, dict):
            record.args = _scrub(record.args, False)
        else:
            record.args = tuple(_scrub(a, False) for a in record.args)
            if sql_like:  # numbers substituted into a SQL message are parameter values
                record.args = tuple(PLACEHOLDER if isinstance(a, (int, float)) and not isinstance(a, bool) else a for a in record.args)
    if record.exc_info and record.exc_info[0] is not None:
        record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        record.exc_info = None  # handlers print the redacted text instead of formatting the raw traceback
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


class RedactionFilter(logging.Filter):
    """The same redaction as a filter, for code that attaches filters to handlers or loggers explicitly."""

    def filter(self, record: logging.LogRecord) -> bool:
        scrub_record(record)
        return True


_original_factory: Any = None
_installed = False


def install_redaction() -> None:
    """Redact every log record from now on, whichever logger made it. Safe to call repeatedly."""
    global _original_factory, _installed
    if _installed:
        return
    _original_factory = logging.getLogRecordFactory()
    original = _original_factory

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original(*args, **kwargs)
        scrub_record(record)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


def uninstall_redaction() -> None:
    global _installed
    if _installed:
        logging.setLogRecordFactory(_original_factory)
        _installed = False
