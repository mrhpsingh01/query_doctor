import type {
  Analysis,
  AnalysisSummary,
  DbConnection,
  Engine,
  PrivacyInfo,
  Explanation,
  ExplanationKind,
  ModelMonitor,
  ModelResponse,
  FingerprintHistory,
  Project,
  SampleQuery,
  WorkloadDashboard,
  WorkloadQueryDetail,
  WorkloadSnapshot,
} from "./types";

/** Error carrying the API's machine-readable code (e.g. "not_select") and a message fit to show a person. */
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let code: string | undefined;
    try {
      const detail = (await response.json())?.detail;
      if (typeof detail === "string") message = detail;
      else if (detail?.message) {
        message = detail.message;
        code = detail.code;
      }
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(message, response.status, code);
  }
  return response.json() as Promise<T>;
}

export interface CreateAnalysisInput {
  sql: string;
  params: unknown[];
  execute: boolean;
  project_id: number;
  connection_id?: number | null;
  engine?: Engine;
}

export interface CreateConnectionInput {
  name: string;
  engine: Engine;
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
  sslmode: "prefer" | "require";
  tls: boolean;
  auth_database: string | null;
}

export const api = {
  listProjects: () => request<Project[]>("/projects"),
  listSamples: (engine?: Engine) => request<SampleQuery[]>(engine && engine !== "postgres" ? `/sample-queries?engine=${engine}` : "/sample-queries"),
  createAnalysis: (input: CreateAnalysisInput) =>
    request<{ id: number; status: string }>("/analyses", { method: "POST", body: JSON.stringify(input) }),
  getAnalysis: (id: number) => request<Analysis>(`/analyses/${id}`),
  cancelAnalysis: (id: number) => request<{ id: number; status: string }>(`/analyses/${id}/cancel`, { method: "POST" }),
  listProjectAnalyses: (projectId: number) => request<AnalysisSummary[]>(`/projects/${projectId}/analyses`),
  approveBenchmark: (analysisId: number, candidateId: number, runs = 7) =>
    request<{ id: number; status: string }>(`/analyses/${analysisId}/candidates/${candidateId}/benchmark`, {
      method: "POST",
      body: JSON.stringify({ runs }),
    }),
  cancelBenchmark: (runId: number) => request<{ id: number; status: string }>(`/benchmarks/${runId}/cancel`, { method: "POST" }),
  importWorkload: (projectId: number, engine: Engine = "postgres", connectionId?: number | null) => {
    const query = new URLSearchParams();
    if (engine !== "postgres") query.set("engine", engine);
    if (connectionId != null) query.set("connection_id", String(connectionId));
    return request<WorkloadSnapshot>(`/projects/${projectId}/workload/import${query.size ? `?${query}` : ""}`, { method: "POST" });
  },
  importProfiler: (projectId: number, input: Record<string, unknown>) =>
    request<WorkloadSnapshot>(`/projects/${projectId}/workload/import-profiler`, { method: "POST", body: JSON.stringify(input) }),
  getWorkload: (projectId: number, snapshotId?: number, compareId?: number, engine: Engine = "postgres", connectionId?: number | null) => {
    const query = new URLSearchParams();
    if (snapshotId !== undefined) query.set("snapshot", String(snapshotId));
    if (compareId !== undefined) query.set("compare", String(compareId));
    if (engine !== "postgres") query.set("engine", engine);
    if (connectionId != null) query.set("connection_id", String(connectionId));
    const suffix = query.size ? `?${query}` : "";
    return request<WorkloadDashboard>(`/projects/${projectId}/workload${suffix}`);
  },
  getWorkloadQuery: (projectId: number, fingerprint: string, connectionId?: number | null) =>
    request<WorkloadQueryDetail>(`/projects/${projectId}/workload/queries/${encodeURIComponent(fingerprint)}${connectionId != null ? `?connection_id=${connectionId}` : ""}`),
  runWorkloadBatch: (projectId: number, snapshotId: number) =>
    request<{ analysis_ids: number[]; skipped: { fingerprint: string; reason: string }[] }>(`/projects/${projectId}/workload/batch`, {
      method: "POST",
      body: JSON.stringify({ snapshot_id: snapshotId, top_n: 5, approve_benchmarks: true }),
    }),
  runDemoWorkload: (projectId: number, mode: "baseline" | "regress", engine: Engine = "postgres") =>
    request<{ status: string; mode: string }>(`/projects/${projectId}/workload/demo`, {
      method: "POST",
      body: JSON.stringify(engine === "postgres" ? { mode } : { mode, engine }),
    }),
  listFingerprints: (projectId: number) => request<FingerprintHistory[]>(`/projects/${projectId}/fingerprints`),
  listConnections: (projectId: number) => request<DbConnection[]>(`/projects/${projectId}/connections`),
  createConnection: (projectId: number, input: CreateConnectionInput) =>
    request<DbConnection>(`/projects/${projectId}/connections`, { method: "POST", body: JSON.stringify(input) }),
  deleteConnection: (id: number) => request<{ deleted: number }>(`/connections/${id}`, { method: "DELETE" }),
  deleteAnalysis: (id: number) => request<{ deleted: number }>(`/analyses/${id}`, { method: "DELETE" }),
  getPrivacy: () => request<PrivacyInfo>("/privacy"),
  createExplanation: (analysisId: number, kind: ExplanationKind) =>
    request<{ id: number; status: string }>(`/analyses/${analysisId}/explanations`, { method: "POST", body: JSON.stringify({ kind }) }),
  getExplanation: (id: number) => request<Explanation>(`/explanations/${id}`),
  reviewExplanation: (id: number, accepted: boolean) =>
    request<{ id: number; reviewed: boolean }>(`/explanations/${id}/review`, { method: "POST", body: JSON.stringify({ accepted }) }),
  getModel: () => request<ModelResponse>("/model"),
  getModelMonitor: () => request<ModelMonitor>("/model/monitor"),
  sendFeedback: (analysisId: number, candidateId: number, helpful: boolean, session: string) =>
    request<{ id: number; helpful: boolean }>(`/analyses/${analysisId}/candidates/${candidateId}/feedback`, {
      method: "POST",
      headers: { "X-QD-Session": session },
      body: JSON.stringify({ helpful }),
    }),
  /** Where a finished analysis's report can be downloaded (a plain link, not a fetch). */
  reportUrl: (analysisId: number, format: "md" | "html") => `/api/analyses/${analysisId}/report?format=${format}`,
};
