import { Evidence } from "@/components/Evidence";
import { HEAT_STYLE, SEVERITY_RANK } from "@/lib/severity";
import type { Finding, Severity } from "@/lib/types";

const CHIP: Record<Severity, string> = {
  critical: `${HEAT_STYLE.red.soft} ${HEAT_STYLE.red.text}`,
  warning: `${HEAT_STYLE.orange.soft} ${HEAT_STYLE.orange.text}`,
  info: `${HEAT_STYLE.yellow.soft} ${HEAT_STYLE.yellow.text}`,
};

interface Props {
  findings: Finding[];
  onSelectNode: (nodeId: string) => void;
}

export function FindingsList({ findings, onSelectNode }: Props) {
  if (findings.length === 0) {
    return (
      <p className="text-sm text-ink-2">
        No problems found in this plan. The query may already be doing what it should, or the plan may be too small to
        show a pattern.
      </p>
    );
  }
  const ordered = [...findings].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || a.id - b.id);
  return (
    <ul className="grid gap-3">
      {ordered.map((f) => (
        <li key={f.id} className="grid gap-2 rounded-md border border-line bg-surface p-3.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded px-1.5 py-0.5 text-xs font-semibold ${CHIP[f.severity]}`}>{f.severity}</span>
            <h3 className="text-sm font-semibold">{f.title}</h3>
          </div>
          <p className="text-sm">{f.summary}</p>
          <Evidence evidence={f.evidence} />
          {f.next_actions.length > 0 && (
            <div className="grid gap-0.5 text-sm text-ink-2">
              <span className="font-medium text-ink">What might help</span>
              {f.next_actions.map((action) => (
                <p key={action}>{action}</p>
              ))}
            </div>
          )}
          {f.node_ids.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => onSelectNode(f.node_ids[0])}
                className="rounded border border-line px-2.5 py-1 text-xs font-medium hover:border-accent"
              >
                Show in plan
              </button>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
