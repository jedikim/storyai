import type {
  AppStatus,
  GraphPayload,
  NodeDetail,
  NodeUpdateResult,
  PromiseItem,
  ProjectList,
  Proposal,
  SearchResult,
  TimelinePayload,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const value = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(value?.detail ?? `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export const api = {
  projects: () => request<ProjectList>("/api/projects"),
  selectProject: (name: string) =>
    request<Record<string, unknown>>("/api/projects/select", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  health: () => request<AppStatus>("/api/health"),
  graph: (asOf: number | null) =>
    request<GraphPayload>(`/api/graph${asOf === null ? "" : `?as_of=${asOf}`}`),
  node: (id: string, asOf: number | null) =>
    request<NodeDetail>(
      `/api/nodes/${encodeURI(id)}${asOf === null ? "" : `?as_of=${asOf}`}`,
    ),
  updateSummary: (id: string, rev: number, summary: string) =>
    request<NodeUpdateResult>(`/api/nodes/${encodeURI(id)}/summary`, {
      method: "POST",
      body: JSON.stringify({ rev, summary }),
    }),
  search: (query: string, asOf: number | null) =>
    request<SearchResult[]>(
      `/api/search?q=${encodeURIComponent(query)}${asOf === null ? "" : `&as_of=${asOf}`}`,
    ),
  promises: (asOf: number | null) =>
    request<PromiseItem[]>(`/api/promises${asOf === null ? "" : `?as_of=${asOf}`}`),
  timeline: () => request<TimelinePayload>("/api/timeline"),
  proposals: () => request<Proposal[]>("/api/proposals"),
  commit: (proposalId: string, mode: "apply" | "dry_run" = "apply") =>
    request<Record<string, unknown>>("/api/proposals/commit", {
      method: "POST",
      body: JSON.stringify({ proposal_id: proposalId, mode }),
    }),
  impact: (proposalId: string) =>
    request<{ proposal_id: string; previews: Array<Record<string, unknown>> }>(
      "/api/proposals/impact",
      { method: "POST", body: JSON.stringify({ proposal_id: proposalId }) },
    ),
};
