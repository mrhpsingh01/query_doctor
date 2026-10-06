// mongo: runs once, as the root user, on an empty data volume.
//
// qd_readonly  reads the sample database only (every analysis connects as this user).
// qd_bench     builds and measures throwaway bench_* databases for benchmarks. MongoDB roles cannot be scoped by a name
//              pattern, so the role grants these actions on every database and the application only ever names
//              databases matching bench_<12 hex digits> (the same rule as the PostgreSQL sandboxes).
// qdProfileReader lets qd_readonly read the profiler's system.profile, the source of the workload dashboard (the built-in read
//              role excludes system collections). Nothing else.
// Neither user is a root, dbOwner or userAdmin.
const admin = db.getSiblingDB("admin");

admin.createRole({
  role: "qdBench",
  privileges: [
    {
      resource: { db: "", collection: "" },
      actions: [
        "find", "insert", "remove", "createCollection", "createIndex", "dropIndex", "dropCollection", "dropDatabase",
        "listCollections", "listIndexes", "collStats", "dbStats", "killCursors",
      ],
    },
  ],
  roles: [],
});

admin.createRole({
  role: "qdProfileReader",
  privileges: [{ resource: { db: "qd_sample", collection: "system.profile" }, actions: ["find"] }],
  roles: [],
});

admin.createUser({ user: "qd_readonly", pwd: "qd_readonly", roles: [{ role: "read", db: "qd_sample" }, { role: "qdProfileReader", db: "admin" }] });
admin.createUser({ user: "qd_bench", pwd: "qd_bench", roles: [{ role: "qdBench", db: "admin" }] });
