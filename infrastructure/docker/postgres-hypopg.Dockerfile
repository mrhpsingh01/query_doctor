# PostgreSQL 16 + HypoPG, with pg_stat_statements available for preloading (see compose.yaml `command`).
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-hypopg \
    && rm -rf /var/lib/apt/lists/*
