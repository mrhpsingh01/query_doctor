import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CandidateCard } from "@/components/candidates/CandidateCard";
import { FeedbackButtons } from "@/components/candidates/FeedbackButtons";
import { PredictionBlock } from "@/components/candidates/PredictionBlock";
import { ApiError } from "@/lib/api";
import { candidate, prediction } from "./fixtures";

const mocks = vi.hoisted(() => ({ sendFeedback: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: mocks }));

beforeEach(() => {
  mocks.sendFeedback.mockReset();
});

describe("PredictionBlock", () => {
  it("shows the expected speedup, the probability, the index size and the regression risk", () => {
    render(<PredictionBlock prediction={prediction()} />);
    expect(screen.getByText("~8.2× faster")).toBeInTheDocument();
    expect(screen.getByText(/93% likely to improve/)).toBeInTheDocument();
    expect(screen.getByText(/4\.5 MB/)).toBeInTheDocument();
    expect(screen.getByText(/2% chance of getting slower/)).toBeInTheDocument();
  });

  it("has its own heading and says plainly that it is not a measurement", () => {
    render(<PredictionBlock prediction={prediction()} />);
    expect(screen.getByRole("heading", { name: "Prediction" })).toBeInTheDocument();
    expect(screen.getByText(/not a measurement/i)).toBeInTheDocument();
    expect(screen.getByText(/does not change the evidence level/i)).toBeInTheDocument();
  });

  it("lists the top three contributions in plain phrases, strongest first", () => {
    render(<PredictionBlock prediction={prediction()} />);
    const items = within(screen.getByRole("list", { name: /why/i })).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent(/removes a sequential scan/i);
    expect(items[2]).toHaveTextContent(/lowers the expected speedup/i);
    expect(screen.queryByText(/the ai thinks/i)).not.toBeInTheDocument();
  });

  it("says which of the two orderings the list uses", () => {
    const { rerender } = render(<PredictionBlock prediction={prediction({ used_for_ranking: false })} />);
    expect(screen.getByText(/ordered by the planner score/i)).toBeInTheDocument();
    rerender(<PredictionBlock prediction={prediction({ used_for_ranking: true })} />);
    expect(screen.getByText(/ordered by this model/i)).toBeInTheDocument();
  });

  it("renders nothing without a prediction", () => {
    const { container } = render(<PredictionBlock prediction={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("formats a speedup below one as a slowdown instead of 0×", () => {
    render(<PredictionBlock prediction={prediction({ expected_speedup: 0.5 })} />);
    expect(screen.getByText("~2× slower")).toBeInTheDocument();
  });
});

describe("CandidateCard with a prediction", () => {
  it("keeps the evidence badge and the prediction visibly separate", () => {
    render(<CandidateCard candidate={candidate({ prediction: prediction() })} />);
    const badge = screen.getByText("Planner-validated");
    const heading = screen.getByRole("heading", { name: "Prediction" });
    expect(badge.closest("[data-prediction]")).toBeNull();
    expect(heading.closest("[data-prediction]")).not.toBeNull();
  });

  it("offers feedback only when it knows the analysis", () => {
    const { rerender } = render(<CandidateCard candidate={candidate({ prediction: prediction() })} />);
    expect(screen.queryByRole("button", { name: /helpful/i })).not.toBeInTheDocument();
    rerender(<CandidateCard candidate={candidate({ prediction: prediction() })} analysisId={5} />);
    expect(screen.getByRole("button", { name: "Helpful" })).toBeInTheDocument();
  });
});

describe("FeedbackButtons", () => {
  it("posts the vote with a session id and then thanks the person and disables both buttons", async () => {
    const user = userEvent.setup();
    mocks.sendFeedback.mockResolvedValue({ id: 1, helpful: true });
    render(<FeedbackButtons analysisId={5} candidateId={9} />);
    await user.click(screen.getByRole("button", { name: "Helpful" }));
    expect(mocks.sendFeedback).toHaveBeenCalledWith(5, 9, true, expect.stringMatching(/^[a-z0-9-]{8,64}$/));
    expect(await screen.findByText(/thanks/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Helpful" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Not helpful" })).toBeDisabled();
  });

  it("sends a negative vote too and reuses the same session id", async () => {
    const user = userEvent.setup();
    mocks.sendFeedback.mockResolvedValue({ id: 1, helpful: false });
    const { unmount } = render(<FeedbackButtons analysisId={5} candidateId={9} />);
    await user.click(screen.getByRole("button", { name: "Not helpful" }));
    const first = mocks.sendFeedback.mock.calls[0];
    expect(first.slice(0, 3)).toEqual([5, 9, false]);
    unmount();
    render(<FeedbackButtons analysisId={5} candidateId={10} />);
    await user.click(screen.getByRole("button", { name: "Helpful" }));
    expect(mocks.sendFeedback.mock.calls[1][3]).toBe(first[3]);
  });

  it("shows the server's message and stays usable when posting fails", async () => {
    const user = userEvent.setup();
    mocks.sendFeedback.mockImplementation(async () => {
      throw new ApiError("index idea not found", 404);
    });
    render(<FeedbackButtons analysisId={5} candidateId={9} />);
    await user.click(screen.getByRole("button", { name: "Helpful" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("index idea not found");
    expect(screen.getByRole("button", { name: "Helpful" })).toBeEnabled();
  });
});
