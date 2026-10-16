"use client";
import Link from "next/link";
import { LOCAL_SETUP_URL, SHOWCASE_ONLY } from "@/lib/deployment";
import { useState } from "react";
import { PlanGraph } from "./plan-viewer/PlanGraph";
import { NodeDetailPanel } from "./plan-viewer/NodeDetailPanel";
import { parseExplain, UNVERIFIED, type PasteResult } from "@/lib/pasteExplain";
import { showcaseFixtures } from "@/lib/showcaseFixtures";
import type { PlanNode } from "@/lib/types";
function findNode(node: PlanNode, id: string | null): PlanNode | null {
  if (node.node_id === id) return node;
  for (const child of node.children) { const found = findNode(child, id); if (found) return found; }
  return null;
}
export function OfflineAnalyzer({ pasteOnly = false }: { pasteOnly?: boolean }) {
  const [example, setExample] = useState<(typeof showcaseFixtures)[number] | null>(null);
  const [result, setResult] = useState<PasteResult | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const selectedNode = result ? findNode(result.plan.root, selected) : null;
  return <section className="grid gap-5" aria-label="Offline analyzer">
    {!pasteOnly && <><div><h2 className="text-xl font-semibold">One click. No database setup.</h2><p className="text-sm text-ink-2">Explore recorded pipeline results or inspect your own plan below.</p></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{showcaseFixtures.map(item => <button key={item.id} type="button" aria-pressed={example?.id === item.id} className="rounded-md border border-line bg-surface p-4 text-left hover:border-accent" onClick={() => { setExample(item); setResult(item.analysis); setSelected(null); setError(null); }}><span className="block font-semibold">{item.title}</span><span className="mt-2 block text-sm text-ink-2">{item.description}</span></button>)}</div></>}
    <form className="order-last grid gap-3" onSubmit={event => { event.preventDefault(); setExample(null); setSelected(null); try { setResult(parseExplain(input)); setError(null); } catch (e) { setResult(null); setError(e instanceof Error ? e.message : "Could not parse plan."); } }}>
      <label htmlFor="explain-paste" className="font-semibold">Paste EXPLAIN Output</label>
      <p id="paste-help" className="text-sm text-ink-2">PostgreSQL EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON), PostgreSQL text, or MongoDB executionStats JSON. Processed locally; nothing is uploaded.</p>
      <textarea id="explain-paste" aria-describedby="paste-help" value={input} onChange={e => setInput(e.target.value)} maxLength={1_000_001} rows={6} spellCheck={false} className="w-full rounded-md border border-line bg-surface p-3 font-mono text-sm" placeholder="Paste plan output, not SQL…" />
      <button className="justify-self-start rounded-md bg-accent px-4 py-2 font-semibold text-accent-ink">Inspect plan</button>
      {error && <p role="alert" className="text-red-600">{error}</p>}
    </form>
    {result && <div className="grid gap-4" aria-live="polite">
      {example && <section className="grid gap-2 rounded-md border border-line bg-surface p-4"><h3 className="font-semibold">{example.title}</h3><p className="text-sm text-ink-2">{example.provenance}</p><p className="font-semibold">{example.verdict}</p>
        <ol aria-label="Verification ladder" className="grid gap-1.5 sm:grid-cols-3">{example.ladder.map(r => <li key={r.label} data-status={r.status} className={`rounded-md border p-3 text-sm ${r.status === "pass" ? "border-emerald-600/40" : r.status === "fail" ? "border-red-600/50" : "border-line"}`}><span className="font-semibold">{r.status === "pass" ? "✅" : r.status === "fail" ? "❌ FAILED:" : "➖ n/a:"} {r.label}</span><span className="mt-1 block text-ink-2">{r.detail}</span></li>)}</ol><p>{example.note}</p>
        <p className="text-sm">Output checksum: {example.benchmark.checksum_match ? "✅ Matched" : "❌ Mismatch"}. {example.capture.checksum_method}.</p>
        <p className="text-sm">Variability (CV): {(example.benchmark.before.cv * 100).toFixed(1)}% → {(example.benchmark.after.cv * 100).toFixed(1)}%. p95: {example.benchmark.before.p95_ms.toFixed(3)} → {example.benchmark.after.p95_ms.toFixed(3)} ms.</p>
        <details><summary className="cursor-pointer text-sm">Full recorded evidence</summary><pre className="max-h-80 overflow-auto text-xs">{JSON.stringify(example.capture, null, 2)}</pre></details>
        <a className="text-sm text-accent underline" download={`querydoctor-${example.id}.json`} href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(example.capture, null, 2))}`}>Download recorded evidence</a><pre className="overflow-auto text-sm">{example.ddl}</pre><p className="text-sm">Recorded HypoPG cost: {example.cost?.join(" → ") ?? "Not applicable (MongoDB)"}</p><p className="text-sm">Recorded baseline runs (ms): {example.before.join(", ")}</p><p className="text-sm">Recorded candidate runs (ms): {example.after.join(", ")}</p></section>}
      {!example && <p className="font-semibold">{UNVERIFIED}</p>}{result.warnings.map(w => <p key={w} className="text-sm text-ink-2">{w}</p>)}
      <PlanGraph plan={result.plan} findings={result.findings} selectedId={selected} onSelect={setSelected} />
      {selectedNode && <NodeDetailPanel node={selectedNode} findings={result.findings} onClose={() => setSelected(null)} engine={result.plan.engine} />}
      <ul className="list-disc space-y-2 pl-5">{result.suggestions.map(s => <li key={s}>{s}</li>)}</ul>
      {!example && !result.suggestions.length && <p>No deterministic index suggestion can be inferred from this plan alone.</p>}
      <Link className="text-accent underline" href={SHOWCASE_ONLY ? LOCAL_SETUP_URL : "/analyses/new?mode=live"}>{SHOWCASE_ONLY ? "Run locally for HypoPG & benchmark validation" : "Connect a database to validate a candidate"}</Link>
    </div>}
  </section>;
}
