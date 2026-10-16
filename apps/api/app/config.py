from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from querydoctor.diagnosis import Thresholds


class Settings(BaseSettings):
    """Runtime configuration, read from QD_* environment variables (and a local .env for development)."""

    model_config = SettingsConfigDict(env_prefix="QD_", env_file=".env", extra="ignore")

    database_url: str = Field(default="postgresql+psycopg://querydoctor:querydoctor@127.0.0.1:5435/querydoctor_meta", repr=False)
    target_dsn: str = Field(default="postgresql://qd_readonly:qd_readonly@127.0.0.1:5433/querydoctor_sample", repr=False)
    bench_dsn: str = Field(default="postgresql://qd_bench:qd_bench@127.0.0.1:5434/postgres", repr=False)
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", repr=False)
    secret_key: str = Field(default="", repr=False)  # Fernet key; empty falls back to a fixed development key (never use in production)
    scale: str = "small"  # data scale of the sample database (used for catalog parameters)
    auto_create_tables: bool = False  # tests / quick local runs; deployments use `alembic upgrade head`
    max_plan_bytes: int = 5_000_000
    max_sql_chars: int = 20_000
    allow_custom_connections: bool = False  # off by default: analyses run only against the prepared sample database
    auth_tokens: dict[str, str] = Field(default_factory=dict, repr=False)  # subject -> long bearer token; required for shared deployments
    allow_private_networks: bool = False  # lets custom connections reach RFC 1918 hosts (never loopback or metadata)
    ollama_url: str = "http://127.0.0.1:11434"  # a local Ollama server; the model name below enables it
    ollama_model: str = ""  # empty: explanations use the deterministic template only
    ollama_timeout_s: float = 60
    mongo_target_uri: str = Field(default="mongodb://qd_readonly:qd_readonly@127.0.0.1:27018/qd_sample?authSource=admin", repr=False)
    mongo_bench_uri: str = Field(default="mongodb://qd_bench:qd_bench@127.0.0.1:27018/?authSource=admin", repr=False)
    model_dir: str = "models"  # where ranker.joblib and ranker.meta.json live; a missing model just means heuristic ordering
    seq_scan_min_rows: int | None = None  # overrides the scale-aware default below

    def diagnosis_thresholds(self) -> Thresholds:
        """Rule thresholds sized to the sample data: the small lab's largest hot table has exactly 100k rows, which
        would sit on the default 100k 'large scan' boundary, so it uses a lower floor."""
        floor = self.seq_scan_min_rows or (50_000 if self.scale == "small" else Thresholds().seq_scan_min_rows)
        return Thresholds(seq_scan_min_rows=floor)


@lru_cache
def get_settings() -> Settings:
    return Settings()
