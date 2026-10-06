import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryEditor } from "@/components/query-editor/QueryEditor";
import type { SampleQuery } from "@/lib/types";

// Monaco needs a real browser; the editor surface is swapped for a textarea here.
vi.mock("@/components/query-editor/SqlEditor", () => ({
  SqlEditor: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <textarea aria-label="SQL" value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

const samples: SampleQuery[] = [
  {
    id: "recent_orders_by_customer",
    title: "Recent orders for one customer",
    sql: "SELECT id FROM orders WHERE customer_id = $1 AND created_at >= $2",
    params: [4242, "2022-01-01"],
    description: "orders has no index on customer_id",
    expected_rule_ids: [],
    expected_candidate_table: null,
    expected_candidate_columns: null,
  },
];

describe("QueryEditor", () => {
  it("shows the execute warning only in execute mode", async () => {
    const user = userEvent.setup();
    render(<QueryEditor samples={samples} onSubmit={vi.fn()} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /execute with analysis/i }));
    expect(screen.getByRole("note")).toHaveTextContent(/runs your query/i);
    await user.click(screen.getByRole("radio", { name: /explain only/i }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });

  it("submits sql, parsed params and the chosen mode", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<QueryEditor samples={samples} onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("SQL"), "SELECT 1");
    await user.click(screen.getByRole("button", { name: /analyze query/i }));
    expect(onSubmit).toHaveBeenCalledWith({ sql: "SELECT 1", params: [], execute: false });

    await user.click(screen.getByRole("radio", { name: /execute with analysis/i }));
    await user.click(screen.getByRole("button", { name: /analyze and run query/i }));
    expect(onSubmit).toHaveBeenLastCalledWith({ sql: "SELECT 1", params: [], execute: true });
  });

  it("fills the editor and parameters from a sample", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<QueryEditor samples={samples} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: /recent orders for one customer/i }));
    expect(screen.getByLabelText("SQL")).toHaveValue(samples[0].sql);
    expect(screen.getByLabelText(/parameters/i)).toHaveValue(JSON.stringify([4242, "2022-01-01"]));
    await user.click(screen.getByRole("button", { name: /analyze query/i }));
    expect(onSubmit).toHaveBeenCalledWith({ sql: samples[0].sql, params: [4242, "2022-01-01"], execute: false });
  });

  it("explains invalid parameter JSON instead of submitting", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<QueryEditor samples={samples} onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("SQL"), "SELECT 1");
    const params = screen.getByLabelText(/parameters/i);
    await user.clear(params);
    await user.type(params, "not json");
    await user.click(screen.getByRole("button", { name: /analyze query/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/json array/i);

    await user.clear(params);
    await user.type(params, '{{"a": 1}');
    await user.click(screen.getByRole("button", { name: /analyze query/i }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(within(screen.getByRole("alert")).getByText(/json array/i)).toBeInTheDocument();
  });

  it("opens with the given SQL and parameters (a link from the workload page)", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<QueryEditor samples={samples} onSubmit={onSubmit} initialSql="SELECT 1 WHERE $1 = 1" initialParams="[1]" />);
    expect(screen.getByLabelText("SQL")).toHaveValue("SELECT 1 WHERE $1 = 1");
    await user.click(screen.getByRole("button", { name: /analyze query/i }));
    expect(onSubmit).toHaveBeenCalledWith({ sql: "SELECT 1 WHERE $1 = 1", params: [1], execute: false });
  });

  it("disables submission while empty or busy and shows a server error", async () => {
    const { rerender } = render(<QueryEditor samples={samples} onSubmit={vi.fn()} />);
    expect(screen.getByRole("button", { name: /analyze query/i })).toBeDisabled();
    rerender(<QueryEditor samples={samples} onSubmit={vi.fn()} busy initialSql="SELECT 1" />);
    expect(screen.getByRole("button", { name: /analyzing/i })).toBeDisabled();
    rerender(<QueryEditor samples={samples} onSubmit={vi.fn()} error="Only SELECT statements are supported." />);
    expect(screen.getByRole("alert")).toHaveTextContent("Only SELECT statements are supported.");
  });
});
