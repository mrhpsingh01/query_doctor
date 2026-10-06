import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NodeDetailPanel } from "@/components/plan-viewer/NodeDetailPanel";
import { PlanNodeBody } from "@/components/plan-viewer/PlanNodeBody";
import { finding, slowPlan } from "./fixtures";

const scan = () => slowPlan().root.children[0];

describe("PlanNodeBody", () => {
  it("shows operator, table, estimated vs actual rows, time, loops and buffer reads", () => {
    render(
      <PlanNodeBody
        node={scan()}
        data={{ severity: "red", share: 0.99, isSlowest: true, findingCount: 1 }}
      />,
    );
    expect(screen.getByText("Seq Scan")).toBeInTheDocument();
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText(/est 10\.9k/)).toBeInTheDocument();
    expect(screen.getByText(/actual 10\.8k/)).toBeInTheDocument();
    expect(screen.getByText("735 ms")).toBeInTheDocument();
    expect(screen.getByText(/×1/)).toBeInTheDocument();
    expect(screen.getByText(/18k read/)).toBeInTheDocument();
    expect(screen.getByLabelText("1 finding")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: /share of query time/i })).toHaveAttribute("aria-valuenow", "99");
  });

  it("shows elapsed time for parallel nodes, not summed worker time", () => {
    const parallel = { ...scan(), actual_time_ms: 90, loops: 3, total_time_ms: 90 };
    render(<PlanNodeBody node={parallel} data={{ severity: "green", share: 0.5, isSlowest: false, findingCount: 0 }} />);
    expect(screen.getByText("90 ms")).toBeInTheDocument();
    expect(screen.queryByText("270 ms")).not.toBeInTheDocument();
  });

  it("omits actuals and the time bar for planner-only nodes", () => {
    const planned = { ...scan(), actual_rows: null, actual_time_ms: null, exclusive_time_ms: null, loops: null };
    render(<PlanNodeBody node={planned} data={{ severity: "green", share: null, isSlowest: false, findingCount: 0 }} />);
    expect(screen.queryByText(/actual/)).not.toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });
});

describe("NodeDetailPanel", () => {
  it("explains the operator, shows evidence for its findings, next actions and the raw JSON", () => {
    render(<NodeDetailPanel node={scan()} findings={[finding()]} onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: /seq scan on orders/i })).toBeInTheDocument();
    expect(screen.getByText(/reads every row of the table/i)).toBeInTheDocument();
    const why = screen.getByRole("region", { name: /why it may be slow/i });
    expect(within(why).getByText("Large scan discards most rows")).toBeInTheDocument();
    expect(within(why).getByText("99.4% of visited rows were discarded.")).toBeInTheDocument();
    expect(within(why).getByText(/1,813,561/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /what might help/i })).toHaveTextContent(/index covering the filter columns/i);
    expect(screen.getByRole("region", { name: /raw plan json/i })).toHaveTextContent('"Relation Name": "orders"');
  });

  it("ignores findings that belong to other nodes and says so when there are none", () => {
    render(<NodeDetailPanel node={scan()} findings={[finding({ node_ids: ["7"] })]} onClose={() => {}} />);
    expect(screen.getByText(/no problems were found at this step/i)).toBeInTheDocument();
  });

  it("explains an unfamiliar operator generically and can be closed", async () => {
    const odd = { ...scan(), node_type: "Frobnicate" };
    const closed: boolean[] = [];
    render(<NodeDetailPanel node={odd} findings={[]} onClose={() => closed.push(true)} />);
    expect(screen.getByText(/no built-in description/i)).toBeInTheDocument();
    screen.getByRole("button", { name: /close/i }).click();
    expect(closed).toEqual([true]);
  });
});
