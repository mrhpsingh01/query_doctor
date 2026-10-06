import { formatMs } from "@/lib/format";
import type { BenchmarkResult } from "@/lib/types";

export function RunTable({ result }: { result: BenchmarkResult }) {
  const rows = Math.max(result.before_samples.length, result.after_samples.length);
  return (
    <details className="rounded-md border border-line p-3">
      <summary className="cursor-pointer text-sm font-medium">All runs ({rows} measured on each side)</summary>
      <table aria-label="Individual runs" className="mt-3 w-full text-left font-mono text-xs">
        <thead>
          <tr className="text-ink-2">
            <th className="py-1 pr-4 font-medium">Run</th>
            <th className="py-1 pr-4 font-medium">Original</th>
            <th className="py-1 font-medium">With index</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, i) => (
            <tr key={i} className="border-t border-line">
              <td className="py-1 pr-4">{i + 1}</td>
              <td className="py-1 pr-4">{formatMs(result.before_samples[i]?.execution_ms)}</td>
              <td className="py-1">{formatMs(result.after_samples[i]?.execution_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
