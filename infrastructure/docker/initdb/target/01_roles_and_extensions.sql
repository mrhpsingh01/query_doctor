-- target-postgres: runs once, as the bootstrap superuser, against POSTGRES_DB (querydoctor_sample).
-- Dev-only credentials; the explore role can never write.
CREATE ROLE qd_readonly LOGIN PASSWORD 'qd_readonly' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE qd_readonly SET default_transaction_read_only = on;
ALTER ROLE qd_readonly SET statement_timeout = '10s';
ALTER ROLE qd_readonly SET lock_timeout = '1s';
ALTER ROLE qd_readonly SET idle_in_transaction_session_timeout = '15s';

GRANT CONNECT ON DATABASE querydoctor_sample TO qd_readonly;
GRANT pg_read_all_stats TO qd_readonly;  -- lets the workload importer read pg_stat_statements for all roles
GRANT USAGE ON SCHEMA public TO qd_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO qd_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO qd_readonly;

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
