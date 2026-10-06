import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Evidence } from "@/components/Evidence";

describe("Evidence", () => {
  it("formats numbers, booleans and shares", () => {
    render(<Evidence evidence={{ docs_sorted: 299219, used_disk: false, share: 0.598 }} />);
    expect(screen.getByText("299,219")).toBeInTheDocument();
    expect(screen.getByText("no")).toBeInTheDocument();
    expect(screen.getByText("59.8%")).toBeInTheDocument();
  });

  it("shows a structured value (a MongoDB sort pattern) as JSON, never as [object Object]", () => {
    const { container } = render(<Evidence evidence={{ sort_pattern: { payload: 1 }, fields: ["a", "b"] }} />);
    expect(container).not.toHaveTextContent("[object Object]");
    expect(screen.getByText('{"payload":1}')).toBeInTheDocument();
    expect(screen.getByText('["a","b"]')).toBeInTheDocument();
  });
});
