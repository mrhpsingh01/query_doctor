class DbError(Exception):
    """Base class for target-database failures that callers should report to the user."""


class ExplainFailed(DbError):
    def __init__(self, message: str, sqlstate: str | None = None):
        super().__init__(message)
        self.sqlstate = sqlstate


class ExplainTimeout(DbError):
    """The statement exceeded the session's statement_timeout."""


class ParamMismatch(DbError):
    """The supplied parameters do not fit the query (count or unsupported type)."""


class UnsafeRole(DbError):
    """Refusing to use a connection whose role could do more than read."""
