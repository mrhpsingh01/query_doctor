"""Dev lab connection strings (see infrastructure/compose.yaml)."""

TARGET_ADMIN_DSN = "postgresql://querydoctor:querydoctor@127.0.0.1:5433/querydoctor_sample"
TARGET_RO_DSN = "postgresql://qd_readonly:qd_readonly@127.0.0.1:5433/querydoctor_sample"
BENCH_ADMIN_DSN = "postgresql://qd_bench:qd_bench@127.0.0.1:5434/postgres"
BENCH_SAMPLE_DSN = "postgresql://qd_bench:qd_bench@127.0.0.1:5434/querydoctor_sample"
BENCH_SUPER_DSN = "postgresql://querydoctor:querydoctor@127.0.0.1:5434/postgres"  # superuser: installs extensions
MONGO_ROOT_URI = "mongodb://querydoctor:querydoctor@127.0.0.1:27018/?authSource=admin"
MONGO_RO_URI = "mongodb://qd_readonly:qd_readonly@127.0.0.1:27018/qd_sample?authSource=admin"
MONGO_BENCH_URI = "mongodb://qd_bench:qd_bench@127.0.0.1:27018/?authSource=admin"
