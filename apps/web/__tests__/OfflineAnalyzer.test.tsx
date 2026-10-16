import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { OfflineAnalyzer } from "@/components/OfflineAnalyzer";
vi.mock("@/components/plan-viewer/PlanGraph", () => ({ PlanGraph: () => <div>Execution plan graph</div> }));
describe("offline analyzer", () => {
  it("opens every card without network access and labels evidence", () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Offline"));
    render(<OfflineAnalyzer />);
    for (const name of [/97% Scan Waste/, /Sort Spill/, /Unindexed MongoDB/, /False Positive/]) {
      fireEvent.click(screen.getByRole("button", { name }));
      expect(screen.getByText("Execution plan graph")).toBeInTheDocument();
      expect(screen.getByText(/Output checksum:/)).toBeInTheDocument();
    }
    expect(fetch).not.toHaveBeenCalled();
    fetch.mockRestore();
  });
  it("renders a pasted plan and clears stale output after invalid input", () => {
    render(<OfflineAnalyzer pasteOnly />);
    const input = screen.getByLabelText("Paste EXPLAIN Output");
    fireEvent.change(input, { target: { value: '{"Plan":{"Node Type":"Seq Scan"}}' } });
    fireEvent.click(screen.getByRole("button", { name: "Inspect plan" }));
    expect(screen.getByText(/Suggested \(Unverified/)).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "Inspect plan" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Execution plan graph")).not.toBeInTheDocument();
  });
});
