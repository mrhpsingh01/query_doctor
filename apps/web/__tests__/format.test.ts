import { describe, expect, it } from "vitest";
import { formatMs, formatPercent, formatRows } from "@/lib/format";

describe("format helpers", () => {
  it("formats durations at a readable precision", () => {
    expect(formatMs(0.42)).toBe("0.42 ms");
    expect(formatMs(742.1)).toBe("742 ms");
    expect(formatMs(6200)).toBe("6.2 s");
    expect(formatMs(null)).toBe("n/a");
  });

  it("formats row counts compactly", () => {
    expect(formatRows(12)).toBe("12");
    expect(formatRows(10842)).toBe("10.8k");
    expect(formatRows(1824403)).toBe("1.82M");
    expect(formatRows(null)).toBe("n/a");
  });

  it("formats ratios as percentages", () => {
    expect(formatPercent(0.9941)).toBe("99.4%");
    expect(formatPercent(1)).toBe("100%");
    expect(formatPercent(null)).toBe("n/a");
  });
});
