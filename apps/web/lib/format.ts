const NA = "n/a";

function trim(n: number, digits: number): string {
  return n.toFixed(digits).replace(/\.?0+$/, "");
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return NA;
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${trim(ms / 1000, 1)} s`;
}

export function formatRows(rows: number | null | undefined): string {
  if (rows === null || rows === undefined) return NA;
  if (rows < 1000) return String(Math.round(rows));
  if (rows < 1_000_000) return `${trim(rows / 1000, 1)}k`;
  return `${trim(rows / 1_000_000, 2)}M`;
}

export function formatPercent(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined) return NA;
  return `${trim(ratio * 100, 1)}%`;
}

export function formatNumber(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return NA;
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 ** 2) return `${trim(bytes / 1024, 1)} KB`;
  if (bytes < 1024 ** 3) return `${trim(bytes / 1024 ** 2, 1)} MB`;
  return `${trim(bytes / 1024 ** 3, 1)} GB`;
}
