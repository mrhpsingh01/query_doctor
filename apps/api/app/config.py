from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from QD_* environment variables (and a local .env for development)."""

    model_config = SettingsConfigDict(env_prefix="QD_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://querydoctor:querydoctor@localhost:5435/querydoctor_meta"
    target_dsn: str = "postgresql://qd_readonly:qd_readonly@localhost:5433/querydoctor_sample"
    bench_dsn: str = "postgresql://qd_bench:qd_bench@localhost:5434/postgres"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = ""  # Fernet key; empty falls back to a fixed development key (never use in production)
    scale: str = "small"  # data scale of the sample database (used for catalog parameters)
    auto_create_tables: bool = False  # tests / quick local runs; deployments use `alembic upgrade head`
    max_plan_bytes: int = 5_000_000
    max_sql_chars: int = 20_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
