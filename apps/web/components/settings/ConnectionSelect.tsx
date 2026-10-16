import type { DbConnection, Engine } from "@/lib/types";

interface Props {
  connections: DbConnection[];
  value: number | null;
  onChange: (id: number | null) => void;
  engine?: Engine;
}

/** Which database an analysis runs against. Hidden until a custom connection exists. */
export function ConnectionSelect({ connections, value, onChange, engine = "postgres" }: Props) {
  const matching = connections.filter((c) => (c.engine ?? "postgres") === engine);
  if (matching.length === 0) return null;
  return (
    <div className="grid gap-1.5">
      <label htmlFor="connection-select" className="text-sm font-semibold">
        Database
      </label>
      <select
        id="connection-select"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        className="block w-full max-w-sm rounded-md border border-line bg-surface px-3 py-2 text-sm"
      >
        <option value="">Sample database</option>
        {matching.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name} ({c.host})
          </option>
        ))}
      </select>
      {value !== null && (
        <p className="text-xs text-ink-2">
          {engine === "postgres"
            ? "Benchmarks run only on the sample database, and index ideas are planner-tested only when HypoPG is installed on the selected source."
            : "Customer MongoDB index ideas remain suggestions until they are measured on an isolated benchmark target."}
        </p>
      )}
    </div>
  );
}
