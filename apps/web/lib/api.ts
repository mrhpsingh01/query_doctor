import type { Analysis, AnalysisSummary, Project, SampleQuery } from "./types";

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
}

export const api = {
  listProjects: () => request<Project[]>("/projects"),
  listSamples: () => request<SampleQuery[]>("/sample-queries"),
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
  /** Where a finished analysis's report can be downloaded (a plain link, not a fetch). */
  reportUrl: (analysisId: number, format: "md" | "html") => `/api/analyses/${analysisId}/report?format=${format}`,
};
