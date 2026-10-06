import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ExplainPanel } from "@/components/explain/ExplainPanel";
import { ApiError } from "@/lib/api";
import type { Explanation } from "@/lib/types";

const mocks = vi.hoisted(() => ({ createExplanation: vi.fn(), getExplanation: vi.fn(), reviewExplanation: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: mocks }));

const done = (over: Partial<Explanation> = {}): Explanation => ({
  id: 7, analysis_id: 5, kind: "plan", status: "completed", source: "template",
  text: "The query took 742 ms to execute. [F2] The plan contains 1 sequential scan(s). [F5]",
  sentences: [
    { text: "The query took 742 ms to execute.", fact_ids: ["F2"] },
    { text: "The plan contains 1 sequential scan(s).", fact_ids: ["F5"] },
  ],
  facts: [
    { id: "F2", label: "Execution time", value: 742, unit: "ms", text: "The query took 742 ms to execute.", derivation: null },
    { id: "F5", label: "Sequential scans in the plan", value: 1, unit: null, text: "The plan has 1 sequential scan(s).", derivation: "count" },
  ],
  dropped_sentences: [], fallback_reason: null, reviewed: null, error: null, ...over,
});

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
  mocks.createExplanation.mockResolvedValue({ id: 7, status: "queued" });
});

describe("ExplainPanel", () => {
  it("offers the five kinds of explanation and nothing is generated until one is chosen", () => {
    render(<ExplainPanel analysisId={5} />);
    for (const name of [/explain the plan/i, /summarise the findings/i, /trade-offs/i, /next experiment/i, /pull-request comment/i]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
    expect(mocks.createExplanation).not.toHaveBeenCalled();
  });

  it("asks for the chosen kind and shows a template explanation with its source badge and the facts it used", async () => {
    const user = userEvent.setup();
    mocks.getExplanation.mockResolvedValue(done());
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /explain the plan/i }));
    expect(mocks.createExplanation).toHaveBeenCalledWith(5, "plan");
    expect(await screen.findByText(/742 ms to execute\. \[F2\]/)).toBeInTheDocument();
    expect(screen.getByText("Template")).toBeInTheDocument();
    const facts = screen.getByText(/facts used/i).closest("details")!;
    expect(within(facts).getByText(/F2/)).toBeInTheDocument();
    expect(within(facts).getByText(/Execution time/)).toBeInTheDocument();
  });

  it("labels a validated language-model explanation with the model", async () => {
    const user = userEvent.setup();
    mocks.getExplanation.mockResolvedValue(done({ source: "ollama:qwen3" }));
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /explain the plan/i }));
    expect(await screen.findByText("Ollama · qwen3, validated")).toBeInTheDocument();
  });

  it("polls until the explanation is ready", async () => {
    const user = userEvent.setup();
    mocks.getExplanation.mockResolvedValueOnce(done({ status: "queued", text: null })).mockResolvedValueOnce(done({ status: "queued", text: null })).mockResolvedValue(done());
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /explain the plan/i }));
    expect(screen.getByRole("status")).toHaveTextContent(/writing/i);
    expect(await screen.findByText(/742 ms to execute\. \[F2\]/)).toBeInTheDocument();
    expect(mocks.getExplanation.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("says what was left out and why when a draft was filtered or fell back", async () => {
    const user = userEvent.setup();
    mocks.getExplanation.mockResolvedValue(
      done({ dropped_sentences: ["It is 99% faster. [F1]"], fallback_reason: "The language model timed out after 60 seconds. The template explanation is shown instead." }),
    );
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /explain the plan/i }));
    expect(await screen.findByText(/timed out after 60 seconds/)).toBeInTheDocument();
    expect(screen.getByText(/1 sentence was removed/i)).toBeInTheDocument();
    expect(screen.getByText(/It is 99% faster/)).toBeInTheDocument();
  });

  it("copies the pull-request comment", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    mocks.getExplanation.mockResolvedValue(done({ kind: "pr_comment", text: "**QueryDoctor** Suggested index. [F1]" }));
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /pull-request comment/i }));
    await user.click(await screen.findByRole("button", { name: /copy comment/i }));
    expect(writeText).toHaveBeenCalledWith("**QueryDoctor** Suggested index. [F1]");
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });

  it("records a person's accept or reject decision", async () => {
    const user = userEvent.setup();
    mocks.getExplanation.mockResolvedValue(done());
    mocks.reviewExplanation.mockResolvedValue({ id: 7, reviewed: true });
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /explain the plan/i }));
    await user.click(await screen.findByRole("button", { name: "Accept" }));
    expect(mocks.reviewExplanation).toHaveBeenCalledWith(7, true);
    expect(await screen.findByText(/you accepted this explanation/i)).toBeInTheDocument();
  });

  it("shows a clear error when the request fails or the explanation failed", async () => {
    const user = userEvent.setup();
    mocks.createExplanation.mockRejectedValueOnce(new ApiError("the analysis has not finished yet", 409));
    render(<ExplainPanel analysisId={5} pollMs={5} />);
    await user.click(screen.getByRole("button", { name: /explain the plan/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the analysis has not finished yet");
    mocks.createExplanation.mockResolvedValue({ id: 8, status: "queued" });
    mocks.getExplanation.mockResolvedValue(done({ status: "failed", text: null, error: "The explanation could not be produced. Try again." }));
    await user.click(screen.getByRole("button", { name: /summarise the findings/i }));
    expect(await screen.findByText(/could not be produced/i)).toBeInTheDocument();
  });

  it("says that an explanation states only what the evidence supports and is never run", () => {
    render(<ExplainPanel analysisId={5} />);
    expect(screen.getByText(/only what the measurements and plan show/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing it says is ever run/i)).toBeInTheDocument();
  });
});
