from querydoctor.adapters.base import DatabaseAdapter, UnknownEngine, get_adapter, register_adapter, unregister_adapter
from querydoctor.adapters.postgres import PostgresAdapter

register_adapter("postgres", lambda **context: PostgresAdapter(**context))

__all__ = ["DatabaseAdapter", "PostgresAdapter", "UnknownEngine", "get_adapter", "register_adapter", "unregister_adapter"]
