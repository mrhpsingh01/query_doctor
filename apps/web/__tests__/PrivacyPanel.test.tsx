import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PrivacyPanel } from "@/components/settings/PrivacyPanel";
import { ApiError } from "@/lib/api";

const mocks = vi.hoisted(() => ({ getPrivacy: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: mocks }));

beforeEach(() => {
  mocks.getPrivacy.mockReset();
});

const info = {
  stores: ["The canonical query text and the parameters you submit.", "Findings, index ideas and their evidence levels."],
  never_stores: ["Rows returned by your queries: only plans, counts and hashes are kept."],
  deletion: ["DELETE /api/analyses/{id} removes an analysis and everything under it."],
  custom_connections_enabled: false,
};

describe("PrivacyPanel", () => {
  it("lists what is stored, what never is, and how to delete, straight from the API", async () => {
    mocks.getPrivacy.mockResolvedValue(info);
    render(<PrivacyPanel />);
    const stored = (await screen.findByRole("heading", { name: /what is stored/i })).closest("section")!;
    expect(within(stored).getAllByRole("listitem")).toHaveLength(2);
    expect(stored).toHaveTextContent("canonical query text");
    expect(screen.getByRole("heading", { name: /never stored/i }).closest("section")).toHaveTextContent(/rows returned by your queries/i);
    expect(screen.getByRole("heading", { name: /deleting your data/i }).closest("section")).toHaveTextContent("DELETE /api/analyses/{id}");
  });

  it("says whether custom connections are switched on", async () => {
    mocks.getPrivacy.mockResolvedValue(info);
    const { rerender } = render(<PrivacyPanel />);
    expect(await screen.findByText(/custom connections are off/i)).toBeInTheDocument();
    mocks.getPrivacy.mockResolvedValue({ ...info, custom_connections_enabled: true });
    rerender(<PrivacyPanel key="again" />);
    expect(await screen.findByText(/custom connections are on/i)).toBeInTheDocument();
  });

  it("shows an error when the information cannot be loaded", async () => {
    mocks.getPrivacy.mockRejectedValue(new ApiError("Request failed (502)", 502));
    render(<PrivacyPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Request failed (502)");
  });
});
