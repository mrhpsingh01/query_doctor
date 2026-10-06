import { Evidence } from "@/components/Evidence";
import { formatMs, formatPercent, formatRows } from "@/lib/format";
import { operatorHelp } from "@/lib/operators";
import type { Finding, PlanNode } from "@/lib/types";

interface Props {
  node: PlanNode;
  findings: Finding[];
  onClose: () => void;
  /** "mongodb" lists documents and keys examined instead of planner estimates. Default: PostgreSQL. */
  engine?: string;
}

function Section({ title, id, children }: { title: string; id: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="grid gap-1.5">
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function NodeDetailPanel({ node, findings, onClose, engine }: Props) {
  const mine = findings.filter((f) => f.node_ids.includes(node.node_id));
  const actions = [...new Set(mine.flatMap((f) => f.next_actions))];
  const title = node.relation_name ? `${node.node_type} on ${node.relation_name}` : node.node_type;
  const totalMs = node.total_time_ms ?? (node.actual_time_ms === null || node.loops === null ? null : node.actual_time_ms * node.loops);

  const mongo = engine === "mongodb";
  const work: [string, string][] = mongo
    ? [
        ...(node.docs_examined != null ? ([["Documents examined", formatRows(node.docs_examined)]] as [string, string][]) : []),
        ...(node.keys_examined ? ([["Index keys examined", formatRows(node.keys_examined)]] as [string, string][]) : []),
        ...(node.actual_rows !== null ? ([["Documents returned", formatRows(node.actual_rows)]] as [string, string][]) : []),
      ]
    : [
        ["Estimated rows", formatRows(node.estimated_rows)],
        ...(node.actual_rows !== null ? ([["Actual rows", formatRows(node.actual_rows)]] as [string, string][]) : []),
      ];
  const facts: [string, string][] = [
    ...work,
    ...(totalMs !== null ? ([["Time (all loops)", formatMs(totalMs)]] as [string, string][]) : []),
    ...(node.exclusive_time_ms !== null ? ([["Time in this step", formatMs(node.exclusive_time_ms)]] as [string, string][]) : []),
    ...(node.loops !== null && !mongo ? ([["Loops", String(node.loops)]] as [string, string][]) : []),
    ...(node.filter_waste !== null ? ([["Rows discarded by filter", formatPercent(node.filter_waste)]] as [string, string][]) : []),
    ...(node.filter_condition ? ([["Filter", node.filter_condition]] as [string, string][]) : []),
    ...(node.index_condition ? ([["Index condition", node.index_condition]] as [string, string][]) : []),
    ...(node.join_condition ? ([["Join condition", node.join_condition]] as [string, string][]) : []),
  ];

  return (
    <aside aria-label="Operator details" className="grid content-start gap-4 rounded-md border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-base font-semibold leading-snug">{title}</h2>
        <button type="button" aria-label="Close details" onClick={onClose} className="rounded px-1.5 text-ink-2 hover:text-ink">
          ✕
        </button>
      </div>

      <p className="text-sm text-ink-2">{operatorHelp(node.node_type)}</p>

      <Section title="In this query" id="facts-h">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
          {facts.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink-2">{k}</dt>
              <dd className="break-words font-mono text-xs leading-6">{v}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Why it may be slow" id="why-h">
        {mine.length === 0 ? (
          <p className="text-sm text-ink-2">No problems were found at this step.</p>
        ) : (
          mine.map((f) => (
            <div key={f.id} className="grid gap-1 rounded border border-line p-2.5">
              <div className="text-sm font-medium">{f.title}</div>
              <p className="text-sm">{f.summary}</p>
              <Evidence evidence={f.evidence} />
            </div>
          ))
        )}
      </Section>

      {actions.length > 0 && (
        <Section title="What might help" id="help-h">
          <div className="grid gap-1 text-sm">
            {actions.map((a) => (
              <p key={a}>{a}</p>
            ))}
          </div>
        </Section>
      )}

      <Section title="Raw plan JSON" id="raw-h">
        <pre className="max-h-64 overflow-auto rounded bg-surface-2 p-2 font-mono text-[11px] leading-snug">
          {JSON.stringify(node.raw, null, 2)}
        </pre>
      </Section>
    </aside>
  );
}
