import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FindingsList } from "@/components/findings/FindingsList";
import { finding } from "./fixtures";

describe("FindingsList", () => {
  it("lists findings most severe first with their summary, evidence and next actions", () => {
    render(
      <FindingsList
        findings={[
          finding({ id: 2, severity: "info", title: "Wide result", summary: "SELECT * returns 300 bytes per row." }),
          finding({ id: 1, severity: "critical" }),
        ]}
        onSelectNode={() => {}}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Large scan discards most rows");
    expect(items[0]).toHaveTextContent("critical");
    expect(items[0]).toHaveTextContent("99.4% of visited rows were discarded.");
    expect(items[0]).toHaveTextContent(/rows removed/i);
    expect(items[0]).toHaveTextContent("Evaluate an index covering the filter columns on orders");
    expect(items[1]).toHaveTextContent("Wide result");
  });

  it("selects the related plan node when a finding is chosen", async () => {
    const onSelectNode = vi.fn();
    render(<FindingsList findings={[finding({ node_ids: ["0.0", "0.1"] })]} onSelectNode={onSelectNode} />);
    await userEvent.click(screen.getByRole("button", { name: /show in plan/i }));
    expect(onSelectNode).toHaveBeenCalledWith("0.0");
  });

  it("says plainly when nothing was found", () => {
    render(<FindingsList findings={[]} onSelectNode={() => {}} />);
    expect(screen.getByText(/no problems found/i)).toBeInTheDocument();
  });
});
