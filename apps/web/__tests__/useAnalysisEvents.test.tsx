import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAnalysisEvents } from "@/lib/useAnalysisEvents";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ||= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    this.listeners.progress?.forEach((fn) => fn({ data: JSON.stringify({ type, ...(data as object) }) } as MessageEvent));
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe("useAnalysisEvents", () => {
  it("emits stages in order and stops after a terminal event", () => {
    const { result } = renderHook(() => useAnalysisEvents(7));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/analyses/7/events");

    act(() => source.emit("validating_query", {}));
    act(() => source.emit("generating_plan", {}));
    expect(result.current.events.map((e) => e.type)).toEqual(["validating_query", "generating_plan"]);
    expect(result.current.current).toBe("generating_plan");
    expect(result.current.done).toBe(false);
    expect(source.closed).toBe(false);

    act(() => source.emit("completed", {}));
    expect(result.current.done).toBe(true);
    expect(result.current.current).toBe("completed");
    expect(source.closed).toBe(true);
  });

  it("treats failed, cancelled and a finished benchmark as terminal too", () => {
    for (const terminal of ["failed", "cancelled", "benchmark_completed"]) {
      const { result } = renderHook(() => useAnalysisEvents(1));
      const source = FakeEventSource.instances.at(-1)!;
      act(() => source.emit(terminal, { message: "boom" }));
      expect(result.current.done).toBe(true);
      expect(result.current.events[0].message).toBe("boom");
    }
  });

  it("does nothing without an analysis id and closes on unmount", () => {
    renderHook(() => useAnalysisEvents(null));
    expect(FakeEventSource.instances).toHaveLength(0);
    const { unmount } = renderHook(() => useAnalysisEvents(3));
    const source = FakeEventSource.instances[0];
    unmount();
    expect(source.closed).toBe(true);
  });

  it("does nothing, rather than crashing, where EventSource is unavailable", () => {
    vi.stubGlobal("EventSource", undefined);
    const { result } = renderHook(() => useAnalysisEvents(5));
    expect(result.current).toEqual({ events: [], current: null, done: false });
  });

  it("ignores malformed events", () => {
    const { result } = renderHook(() => useAnalysisEvents(2));
    const source = FakeEventSource.instances[0];
    act(() => source.listeners.progress.forEach((fn) => fn({ data: "not json" } as MessageEvent)));
    expect(result.current.events).toEqual([]);
  });
});
