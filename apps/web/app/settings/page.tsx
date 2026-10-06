export default function Settings() {
  return (
    <div className="grid max-w-2xl gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <section className="grid gap-1">
        <h2 className="text-lg font-semibold">Database</h2>
        <p className="text-ink-2">
          This release analyzes QueryDoctor&apos;s own sample shop database. Connecting your own database is not available
          yet.
        </p>
      </section>

      <section className="grid gap-1">
        <h2 className="text-lg font-semibold">What QueryDoctor does to the database</h2>
        <ul className="list-disc pl-5 text-ink-2">
          <li>Every query runs in a read-only session with a 10 second limit and is rolled back.</li>
          <li>Only a single SELECT is accepted. Anything that writes or changes the schema is rejected.</li>
          <li>Index ideas are tested as hypothetical indexes first. Real indexes are only created in a throwaway copy, and only after you approve a benchmark.</li>
        </ul>
      </section>

      <section className="grid gap-1">
        <h2 className="text-lg font-semibold">What is stored</h2>
        <p className="text-ink-2">
          The normalized query and its parameters, the execution plan, schema details, findings, and benchmark results.
          Rows returned by your queries are never stored.
        </p>
      </section>
    </div>
  );
}
