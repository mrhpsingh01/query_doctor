from querydoctor.adapters.base import DatabaseAdapter, UnknownEngine, get_adapter, register_adapter, unregister_adapter
from querydoctor.adapters.mongo import MongoAdapter
from querydoctor.adapters.postgres import PostgresAdapter

register_adapter("postgres", lambda **context: PostgresAdapter(**context))
register_adapter("mongodb", lambda **context: MongoAdapter(**context))

__all__ = ["DatabaseAdapter", "MongoAdapter", "PostgresAdapter", "UnknownEngine", "get_adapter", "register_adapter", "unregister_adapter"]
