"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AnalysisList } from "@/components/AnalysisList";
import { api } from "@/lib/api";
import type { AnalysisSummary, SampleQuery } from "@/lib/types";
import { useDefaultProject } from "@/lib/useDefaultProject";

export default function Dashboard() {
  const { project, error } = useDefaultProject();
  const [recent, setRecent] = useState<AnalysisSummary[]>([]);
  const [samples, setSamples] = useState<SampleQuery[]>([]);

  useEffect(() => {
    api.listSamples().then(setSamples).catch(() => {});
  }, []);
  useEffect(() => {
    if (project) api.listProjectAnalyses(project.id).then((a) => setRecent(a.slice(0, 6))).catch(() => {});
  }, [project]);

  return (
    <div className="grid gap-10">
      <section className="max-w-2xl">
        <h1 className="text-3xl font-semibold tracking-tight">Find out why a query is slow</h1>
        <p className="mt-2 text-ink-2">
          Paste a SELECT. QueryDoctor reads its execution plan, points at the operator that costs the most, and only
          recommends changes that it has tested.
        </p>
        <Link
          href="/analyses/new"
          className="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-ink"
        >
          Start an analysis
        </Link>
        {error && (
          <p role="alert" className="mt-4 rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
            Can&apos;t reach the QueryDoctor API ({error}). Start the stack with{" "}
            <code className="font-mono">docker compose -f infrastructure/compose.yaml up -d</code>.
          </p>
        )}
      </section>

      <div className="grid gap-8 lg:grid-cols-2">
        <section aria-labelledby="samples-h">
          <h2 id="samples-h" className="mb-3 text-lg font-semibold">
            Slow queries to try
          </h2>
          <ul className="grid gap-1.5">
            {samples.map((s) => (
              <li key={s.id}>
                <Link
                  href={`/analyses/new?sample=${s.id}`}
                  className="block rounded-md border border-line bg-surface px-3 py-2 hover:border-accent"
                >
                  <span className="block text-sm font-medium">{s.title}</span>
                  <span className="block text-xs text-ink-2">{s.description}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="recent-h">
          <h2 id="recent-h" className="mb-3 text-lg font-semibold">
            Recent analyses
          </h2>
          <AnalysisList analyses={recent} empty="No analyses yet. Pick a slow query to the left to start one." />
        </section>
      </div>
    </div>
  );
}
