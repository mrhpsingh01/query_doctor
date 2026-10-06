import type { Engine } from "@/lib/types";

export function ExecuteWarning({ engine = "postgres" }: { engine?: Engine }) {
  return (
    <p role="note" className="rounded-md border border-heat-2 bg-heat-2-soft px-3 py-2 text-sm text-ink">
      <strong className="font-semibold">This runs your query.</strong>{" "}
      {engine === "mongodb"
        ? "It runs as a read-only user with a time limit, so nothing is changed, but it takes as long as the query normally does and reads real data."
        : "It runs in a read-only session and is rolled back, so nothing is changed, but it takes as long as the query normally does and uses real database resources."}{" "}
      Explain only does not run it.
    </p>
  );
}
