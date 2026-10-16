import Link from "next/link";
import { LOCAL_SETUP_URL } from "@/lib/deployment";
import captured from "@/lib/workloadCaptured.json";
import type { WorkloadDashboard as Dashboard } from "@/lib/types";
import { FixQueueSummary } from "./FixQueueSummary";
import { QueryTable } from "./QueryTable";

const data = captured as unknown as Dashboard;

/** The workload view of the public showcase: a real capture from the local stack, so no database is needed to see the ROI. */
export function RecordedWorkload() {
  const snapshot = data.snapshot!;
  const seconds = snapshot.capture_start && snapshot.capture_end ? Math.round((Date.parse(snapshot.capture_end) - Date.parse(snapshot.capture_start)) / 1000) : null;
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Workload</h1>
        <p className="mt-1 max-w-[70ch] text-sm text-ink-2">
          Which queries cost the most database time, and which proven index fixes recover it. Fix the top of the list first.
        </p>
        <p className="mt-2 max-w-[75ch] rounded-md border border-line bg-surface p-3 text-sm">
          Recorded from a real local run: the demo traffic generator against the sample PostgreSQL database
          {seconds !== null ? ` (${seconds} s of traffic, ${snapshot.query_count} query shapes)` : ""}, then the product&apos;s own batch flow benchmarked the
          top queries in isolated clones. The per-day figure assumes that traffic rate continues around the clock; real workloads differ.{" "}
          <Link className="text-accent underline" href={LOCAL_SETUP_URL}>Run locally</Link> to import your own pg_stat_statements.
        </p>
      </div>
      <section aria-label="Ranked Fix Queue" className="rounded-md border border-line bg-surface p-4">
        <h2 className="text-lg font-semibold">Ranked Fix Queue</h2>
        <FixQueueSummary queue={data.fix_queue} evidenceLinks={false} />
      </section>
      <section aria-labelledby="ranked-h" className="grid gap-3">
        <h2 id="ranked-h" className="text-lg font-semibold">Ranked by total time</h2>
        <QueryTable queries={data.queries} staticMode />
      </section>
    </div>
  );
}
