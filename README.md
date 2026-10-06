# QueryDoctor

Evidence-based PostgreSQL performance investigator. Give it a slow `SELECT`; it reads the execution plan,
diagnoses bottlenecks, tests index candidates (HypoPG, then isolated benchmarks) and recommends only what the
measurements support.

See `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the build plan.
