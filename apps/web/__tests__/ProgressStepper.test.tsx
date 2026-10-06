import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProgressStepper } from "@/components/ProgressStepper";

const state = (label: string) => screen.getByText(label).closest("li")?.getAttribute("data-state");

describe("ProgressStepper", () => {
  it("marks earlier stages done, the latest in progress and later ones waiting", () => {
    render(<ProgressStepper current="generating_plan" />);
    expect(state("Checking the query")).toBe("done");
    expect(state("Reading the schema")).toBe("done");
    expect(state("Getting the execution plan")).toBe("current");
    expect(state("Looking for problems")).toBe("waiting");
    expect(state("Testing index ideas")).toBe("waiting");
  });

  it("shows everything done once completed", () => {
    render(<ProgressStepper current="completed" />);
    for (const label of ["Checking the query", "Looking for problems", "Testing index ideas"]) {
      expect(state(label)).toBe("done");
    }
  });

  it("marks the stage that was running as failed and explains why", () => {
    render(<ProgressStepper current="failed" lastStage="generating_plan" message='relation "x" does not exist' />);
    expect(state("Checking the query")).toBe("done");
    expect(state("Getting the execution plan")).toBe("failed");
    expect(state("Looking for problems")).toBe("waiting");
    expect(screen.getByRole("alert")).toHaveTextContent('relation "x" does not exist');
  });

  it("says when an analysis was cancelled", () => {
    render(<ProgressStepper current="cancelled" lastStage="collecting_schema" />);
    expect(screen.getByText(/cancelled/i)).toBeInTheDocument();
    expect(state("Reading the schema")).toBe("failed");
  });

  it("renders all stages waiting before anything arrives", () => {
    render(<ProgressStepper current={null} />);
    expect(state("Checking the query")).toBe("waiting");
  });
});
