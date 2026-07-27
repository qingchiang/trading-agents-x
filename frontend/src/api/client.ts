import type { components } from "./types.generated";

export type AnalysisRequest = components["schemas"]["AnalysisRequest"];
export type RunView = components["schemas"]["RunView"];
export type RunDetail = components["schemas"]["RunDetail"];
export type AnalysisResult = components["schemas"]["AnalysisResult"];
export type RunEvent = components["schemas"]["RunEvent"];
export type Capabilities = components["schemas"]["CapabilitiesResponse"];
export type Health = components["schemas"]["HealthResponse"];
export type MemoryEntry = components["schemas"]["MemoryEntry"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.error?.message || payload.detail || message;
    } catch {
      // Preserve the HTTP status text.
    }
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("tradingagents:auth-required"));
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/v1/health"),
  capabilities: () =>
    request<Capabilities>("/api/v1/capabilities"),
  runs: (query = "") => request<RunView[]>(`/api/v1/runs${query}`),
  run: (id: string) => request<RunDetail>(`/api/v1/runs/${id}`),
  createRun: (payload: AnalysisRequest, idempotencyKey: string) =>
    request<RunView>("/api/v1/runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),
  action: (id: string, action: "cancel" | "retry" | "rerun") =>
    request<RunView>(`/api/v1/runs/${id}/${action}`, { method: "POST" }),
  memory: (query = "") =>
    request<MemoryEntry[]>(`/api/v1/memory${query}`),
  login: (token: string) =>
    request<{ authenticated: boolean }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
};
