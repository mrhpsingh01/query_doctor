import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CandidateCard } from "@/components/candidates/CandidateCard";
import { CandidatesList } from "@/components/candidates/CandidatesList";
import { ValidationBadge } from "@/components/candidates/ValidationBadge";
import { candidate } from "./fixtures";

describe("ValidationBadge", () => {
  it.each([
    ["suggested", "Suggested", /not tested/i],
    ["planner_validated", "Planner-validated", /not (yet )?measured/i],
    ["benchmark_validated", "Benchmark-validated", /measured faster/i],
  ] as const)("labels %s and explains what it does and does not prove", (state, label, explanation) => {
    render(<ValidationBadge validation={state} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(explanation)).toBeInTheDocument();
  });

  it("marks each state with its own data attribute so they can be told apart", () => {
    const { container } = render(
      <>
        <ValidationBadge validation="suggested" />
        <ValidationBadge validation="planner_validated" />
        <ValidationBadge validation="benchmark_validated" />
      </>,
    );
    const states = [...container.querySelectorAll("[data-validation]")].map((e) => e.getAttribute("data-validation"));
    expect(states).toEqual(["suggested", "planner_validated", "benchmark_validated"]);
  });
});

describe("CandidateCard", () => {
  it("shows the DDL, evidence level, planner score and what changes in the plan", () => {
    render(<CandidateCard candidate={candidate()} />);
    expect(screen.getByText(/CREATE INDEX ON orders \(customer_id, created_at DESC\)/)).toBeInTheDocument();
    expect(screen.getByText("Planner-validated")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: /planner score/i })).toHaveAttribute("aria-valuenow", "95");
    expect(screen.getByText(/Seq Scan/)).toBeInTheDocument();
    expect(screen.getByText(/Index Scan/)).toBeInTheDocument();
    expect(screen.getByText(/61\.5k/)).toBeInTheDocument();
    expect(screen.getByText(/2\.2k/)).toBeInTheDocument();
    expect(screen.getByText(/equality on customer_id/)).toBeInTheDocument();
  });

  it("says it is untested for suggested candidates and shows no score", () => {
    render(<CandidateCard candidate={candidate({ validation: "suggested", planner_score: 0, comparison: null })} />);
    expect(screen.getByText("Suggested")).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("offers no benchmark button unless a handler is supplied", () => {
    render(<CandidateCard candidate={candidate()} />);
    expect(screen.queryByRole("button", { name: /benchmark/i })).not.toBeInTheDocument();
  });

  it("requires explicit confirmation before a benchmark can be approved", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(<CandidateCard candidate={candidate()} onApprove={onApprove} />);
    const button = screen.getByRole("button", { name: /run benchmark/i });
    expect(button).toBeDisabled();
    await user.click(button);
    expect(onApprove).not.toHaveBeenCalled();

    await user.click(screen.getByRole("checkbox", { name: /real index in an isolated copy/i }));
    expect(button).toBeEnabled();
    await user.click(button);
    expect(onApprove).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });

  it("describes a candidate the planner ignored as untested, with its note", () => {
    render(
      <CandidateCard
        candidate={candidate({
          validation: "suggested",
          rationale: ["equality on customer_id", "The planner did not use this index for this query as tested."],
        })}
      />,
    );
    expect(screen.getByText(/did not use this index/i)).toBeInTheDocument();
  });
});

describe("CandidatesList", () => {
  it("lists recommended ideas first and puts ruled-out ones in a collapsed section with reasons", () => {
    render(
      <CandidatesList
        candidates={[
          candidate({ id: 1, rank: 1 }),
          candidate({
            id: 2, rank: 0, dropped: true, validation: "suggested",
            ddl: "CREATE INDEX ON orders (id)", drop_reason: "duplicate of existing index orders_pkey",
          }),
        ]}
      />,
    );
    expect(screen.getAllByRole("article")).toHaveLength(1);
    const ruledOut = screen.getByText(/ruled out/i).closest("details")!;
    expect(ruledOut).not.toHaveAttribute("open");
    expect(within(ruledOut).getByText(/duplicate of existing index orders_pkey/)).toBeInTheDocument();
    expect(within(ruledOut).getByText(/CREATE INDEX ON orders \(id\)/)).toBeInTheDocument();
  });

  it("explains an empty result instead of leaving a blank", () => {
    render(<CandidatesList candidates={[]} />);
    expect(screen.getByText(/no index ideas/i)).toBeInTheDocument();
  });
});
