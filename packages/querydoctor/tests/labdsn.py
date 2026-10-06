"""Dev lab connection strings (see infrastructure/compose.yaml)."""

TARGET_ADMIN_DSN = "postgresql://querydoctor:querydoctor@localhost:5433/querydoctor_sample"
TARGET_RO_DSN = "postgresql://qd_readonly:qd_readonly@localhost:5433/querydoctor_sample"
BENCH_ADMIN_DSN = "postgresql://qd_bench:qd_bench@localhost:5434/postgres"
BENCH_SAMPLE_DSN = "postgresql://qd_bench:qd_bench@localhost:5434/querydoctor_sample"
