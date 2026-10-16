import Link from "next/link";
import { LOCAL_SETUP_URL, SHOWCASE_ONLY } from "@/lib/deployment";
import { OfflineAnalyzer } from "@/components/OfflineAnalyzer";
export default function Dashboard() {
  return <div className="grid gap-8"><section className="max-w-3xl"><h1 className="text-3xl font-semibold tracking-tight">Find out why a database query is slow—and measure whether a fix actually works.</h1><p className="mt-3 text-ink-2">Inspect execution plans, test hypothetical indexes, and compare isolated benchmarks with output verification.</p><Link className="mt-4 inline-block text-accent underline" href={SHOWCASE_ONLY ? LOCAL_SETUP_URL : "/analyses/new?mode=live"}>{SHOWCASE_ONLY ? "Run locally for live database validation" : "Open live query runner"}</Link></section><OfflineAnalyzer /></div>;
}
