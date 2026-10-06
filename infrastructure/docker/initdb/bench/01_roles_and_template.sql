-- bench-postgres: runs once, as the bootstrap superuser, against the default `postgres` database.
-- qd_bench owns the template database (CREATE DATABASE ... TEMPLATE requires ownership) and may create/drop
-- sandbox clones. It exists only on this instance and has no other privileges.
-- hypopg goes into template1 so every database created later (sample template, ranker data variants) has it.
\connect template1
CREATE EXTENSION IF NOT EXISTS hypopg;
\connect postgres

CREATE ROLE qd_bench LOGIN PASSWORD 'qd_bench' NOSUPERUSER CREATEDB NOCREATEROLE;
CREATE DATABASE querydoctor_sample OWNER qd_bench;

\connect querydoctor_sample
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
