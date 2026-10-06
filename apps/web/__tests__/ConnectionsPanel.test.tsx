import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionSelect } from "@/components/settings/ConnectionSelect";
import { ConnectionsPanel } from "@/components/settings/ConnectionsPanel";
import { DeleteAnalysisButton } from "@/components/DeleteAnalysisButton";
import { ApiError } from "@/lib/api";
import type { DbConnection } from "@/lib/types";

const mocks = vi.hoisted(() => ({ listConnections: vi.fn(), createConnection: vi.fn(), deleteConnection: vi.fn(), deleteAnalysis: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: mocks }));

const conn = (over: Partial<DbConnection> = {}): DbConnection => ({
  id: 3, project_id: 1, name: "Prod replica", kind: "custom", host: "db.example.com", port: 5432, database: "shop", username: "reader", sslmode: "prefer", ...over,
});

beforeEach(() => {
  Object.values(mocks).forEach((m) => m.mockReset());
});

async function fill(user: ReturnType<typeof userEvent.setup>, password = "Sup3r-S3cret-pw") {
  await user.type(screen.getByLabelText(/^name$/i), "Prod replica");
  await user.type(screen.getByLabelText(/^host$/i), "db.example.com");
  await user.type(screen.getByLabelText(/^database$/i), "shop");
  await user.type(screen.getByLabelText(/^user$/i), "reader");
  await user.type(screen.getByLabelText(/^password$/i), password);
}

describe("ConnectionsPanel", () => {
  it("explains that custom connections are off when the server says so", async () => {
    mocks.listConnections.mockRejectedValue(new ApiError("Custom connections are turned off. Set QD_ALLOW_CUSTOM_CONNECTIONS=true to enable them.", 403));
    render(<ConnectionsPanel projectId={1} />);
    expect((await screen.findAllByText(/turned off/i)).length).toBeGreaterThan(0);
    expect(screen.getByText(/QD_ALLOW_CUSTOM_CONNECTIONS/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add connection/i })).not.toBeInTheDocument();
  });

  it("lists connections without any password and offers the form", async () => {
    mocks.listConnections.mockResolvedValue([conn()]);
    render(<ConnectionsPanel projectId={1} />);
    expect(await screen.findByText("Prod replica")).toBeInTheDocument();
    expect(screen.getByText(/db\.example\.com:5432/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toHaveAttribute("type", "password");
  });

  it("posts the form, clears the password field, and never shows it again", async () => {
    const user = userEvent.setup();
    mocks.listConnections.mockResolvedValue([]);
    mocks.createConnection.mockResolvedValue(conn());
    render(<ConnectionsPanel projectId={1} />);
    await user.click(await screen.findByRole("button", { name: /add connection/i }));
    expect(mocks.createConnection).not.toHaveBeenCalled(); // an empty form is not submitted
    await fill(user);
    await user.click(screen.getByRole("button", { name: /add connection/i }));
    expect(mocks.createConnection).toHaveBeenCalledWith(1, {
      name: "Prod replica", host: "db.example.com", port: 5432, database: "shop", username: "reader", password: "Sup3r-S3cret-pw", sslmode: "prefer",
    });
    expect(await screen.findByText("Prod replica")).toBeInTheDocument();
    expect(screen.getByLabelText(/^password$/i)).toHaveValue("");
    expect(document.body.textContent).not.toContain("Sup3r-S3cret-pw");
  });

  it("shows the server's reason when a host is refused and keeps the form usable", async () => {
    const user = userEvent.setup();
    mocks.listConnections.mockResolvedValue([]);
    mocks.createConnection.mockRejectedValue(new ApiError("That host or port is not allowed: loopback addresses are not allowed.", 400));
    render(<ConnectionsPanel projectId={1} />);
    await screen.findByRole("button", { name: /add connection/i });
    await fill(user);
    await user.click(screen.getByRole("button", { name: /add connection/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/loopback addresses are not allowed/);
    expect(screen.getByRole("button", { name: /add connection/i })).toBeEnabled();
    expect(document.body.textContent).not.toContain("Sup3r-S3cret-pw");
  });

  it("asks for confirmation before deleting and then removes the connection", async () => {
    const user = userEvent.setup();
    mocks.listConnections.mockResolvedValue([conn()]);
    mocks.deleteConnection.mockResolvedValue({ deleted: 3 });
    render(<ConnectionsPanel projectId={1} />);
    await user.click(await screen.findByRole("button", { name: /delete prod replica/i }));
    expect(mocks.deleteConnection).not.toHaveBeenCalled();
    expect(screen.getByText(/saved password/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/saved password/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /delete prod replica/i }));
    await user.click(screen.getByRole("button", { name: /yes, delete/i }));
    expect(mocks.deleteConnection).toHaveBeenCalledWith(3);
    expect(screen.queryByText("Prod replica")).not.toBeInTheDocument();
  });
});

describe("ConnectionSelect", () => {
  it("renders nothing when there are no custom connections", () => {
    const { container } = render(<ConnectionSelect connections={[]} value={null} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("offers the sample database and each connection, and reports the choice", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ConnectionSelect connections={[conn()]} value={null} onChange={onChange} />);
    const select = screen.getByRole("combobox", { name: /database/i });
    expect(within(select).getByRole("option", { name: /sample database/i })).toBeInTheDocument();
    await user.selectOptions(select, "3");
    expect(onChange).toHaveBeenCalledWith(3);
    await user.selectOptions(select, "");
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("warns that benchmarks and index testing are limited on a custom database", () => {
    render(<ConnectionSelect connections={[conn()]} value={3} onChange={vi.fn()} />);
    expect(screen.getByText(/benchmarks run only on the sample database/i)).toBeInTheDocument();
  });
});

describe("DeleteAnalysisButton", () => {
  it("confirms, deletes through the API and then calls back so the page can navigate away", async () => {
    const user = userEvent.setup();
    const onDeleted = vi.fn();
    mocks.deleteAnalysis.mockResolvedValue({ deleted: 5 });
    render(<DeleteAnalysisButton analysisId={5} onDeleted={onDeleted} />);
    await user.click(screen.getByRole("button", { name: /delete this analysis/i }));
    expect(mocks.deleteAnalysis).not.toHaveBeenCalled();
    expect(screen.getByText(/permanently removes/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /yes, delete/i }));
    expect(mocks.deleteAnalysis).toHaveBeenCalledWith(5);
    expect(onDeleted).toHaveBeenCalledTimes(1);
  });

  it("can be cancelled and shows the server's message when deleting fails", async () => {
    const user = userEvent.setup();
    const onDeleted = vi.fn();
    render(<DeleteAnalysisButton analysisId={5} onDeleted={onDeleted} />);
    await user.click(screen.getByRole("button", { name: /delete this analysis/i }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText(/permanently removes/i)).not.toBeInTheDocument();
    mocks.deleteAnalysis.mockRejectedValue(new ApiError("the analysis is still running: cancel it first", 409));
    await user.click(screen.getByRole("button", { name: /delete this analysis/i }));
    await user.click(screen.getByRole("button", { name: /yes, delete/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/cancel it first/);
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
