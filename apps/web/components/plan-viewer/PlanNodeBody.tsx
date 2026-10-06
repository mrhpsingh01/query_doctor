import { formatMs, formatRows } from "@/lib/format";
import type { NodeVisual } from "@/lib/planGraph";
import { HEAT_STYLE } from "@/lib/severity";
import type { PlanNode } from "@/lib/types";

/**
 * The face of one operator in the plan graph. The thin bar at the bottom is the memorable part: how much of the
 * whole query's time this operator spent itself, so the bottleneck is visible before anything is read.
 */
export function PlanNodeBody({ node, data }: { node: PlanNode; data: NodeVisual }) {
  const heat = HEAT_STYLE[data.severity];
  const target = node.relation_name ?? node.index_name;
  // Elapsed time across loops, corrected for parallel workers by the API (falls back to per-loop x loops).
  const totalMs = node.total_time_ms ?? (node.actual_time_ms === null || node.loops === null ? null : node.actual_time_ms * node.loops);
  const sharePct = data.share === null ? null : Math.round(data.share * 100);

  return (
    <div className={`overflow-hidden rounded border border-line border-l-4 ${heat.edge} bg-surface text-left`}>
      <div className="flex items-start justify-between gap-2 px-2.5 pt-2">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold leading-tight">{node.node_type}</div>
          {target && <div className="truncate font-mono text-xs text-ink-2">{target}</div>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {data.isSlowest && (
            <span className="rounded bg-heat-3-soft px-1.5 py-0.5 text-[11px] font-medium text-heat-3">slowest</span>
          )}
          {data.findingCount > 0 && (
            <span
              aria-label={`${data.findingCount} ${data.findingCount === 1 ? "finding" : "findings"}`}
              className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${heat.soft} ${heat.text}`}
            >
              ⚠ {data.findingCount}
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-0.5 px-2.5 pb-2 pt-1.5 font-mono text-[11px] text-ink-2">
        <div className="flex flex-wrap gap-x-2">
          <span>est {formatRows(node.estimated_rows)}</span>
          {node.actual_rows !== null && <span>actual {formatRows(node.actual_rows)}</span>}
        </div>
        <div className="flex flex-wrap gap-x-2">
          {totalMs !== null && <span className="text-ink">{formatMs(totalMs)}</span>}
          {node.loops !== null && node.loops !== 1 && <span>×{node.loops}</span>}
          {node.loops === 1 && <span>×1</span>}
          {node.shared_read_blocks ? <span>{formatRows(node.shared_read_blocks)} read</span> : null}
          {node.shared_hit_blocks ? <span>{formatRows(node.shared_hit_blocks)} hit</span> : null}
        </div>
      </div>

      {sharePct !== null && (
        <div
          role="meter"
          aria-label="Share of query time"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={sharePct}
          className="h-1.5 w-full bg-surface-2"
        >
          <div className={`h-full ${heat.bar}`} style={{ width: `${Math.max(sharePct, sharePct > 0 ? 2 : 0)}%` }} />
        </div>
      )}
    </div>
  );
}
