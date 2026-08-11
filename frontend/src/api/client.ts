import type { components } from "./types.generated";

export type AnalysisRequest = components["schemas"]["AnalysisRequest"];
export type RunCreateRequest = components["schemas"]["RunCreateRequest"];
export type RunView = components["schemas"]["RunView"];
export type RunSummaryView = components["schemas"]["RunSummaryView"];
export type RunPage = components["schemas"]["RunPage"];
export type RunBatchResult = components["schemas"]["RunBatchResult"];
export type RunDetail = components["schemas"]["RunDetail"];
export type AnalysisResult = components["schemas"]["AnalysisResult"];
export type RunEvent = components["schemas"]["RunEvent"];
export type ResearchArtifact = components["schemas"]["ResearchArtifact"];
export type ArtifactGenerationObservation =
  components["schemas"]["ArtifactGenerationObservation"];
export type DecisionBrief = components["schemas"]["DecisionBrief"];
export type AnalystReport = components["schemas"]["AnalystReport"];
export type ResearchCase = components["schemas"]["ResearchCase"];
export type DebateAgenda = components["schemas"]["DebateAgenda"];
export type RebuttalReview = components["schemas"]["RebuttalReview"];
export type JudgeDraft = components["schemas"]["JudgeDraft"];
export type RiskReview = components["schemas"]["RiskReview"];
export type ResearchDecision = components["schemas"]["ResearchDecision"];
export type DecisionNumericAuditAppendix =
  components["schemas"]["DecisionNumericAuditAppendix"];
export type NumericAuditSnapshot = components["schemas"]["NumericAuditSnapshot"];
export type NumericAuditOmission = components["schemas"]["NumericAuditOmission"];
export type NumericRequirementCheck =
  components["schemas"]["NumericRequirementCheck"];
export type EvidenceBundle = components["schemas"]["EvidenceBundle"];
export type EvidenceItem = components["schemas"]["EvidenceItem"];
export type EvidenceTable = components["schemas"]["EvidenceTable"];
export type EvidenceTableColumn =
  components["schemas"]["EvidenceTableColumn"];
export type EvidenceTableCell = components["schemas"]["EvidenceTableCell"];
export type EvidenceTableRow = components["schemas"]["EvidenceTableRow"];
export type CalculationRecord = components["schemas"]["CalculationRecord"];
export type Capabilities = components["schemas"]["CapabilitiesResponse"];
export type ProviderModelCatalog =
  components["schemas"]["ProviderModelCatalog"];
export type DiscoveredModel = components["schemas"]["DiscoveredModelView"];
export type Health = components["schemas"]["HealthResponse"];
export type ResearchReview = components["schemas"]["ResearchReview"];
export type ResearchReviewAuditDetail =
  components["schemas"]["ResearchReviewAuditDetail"];
export type ReflectionRegenerationAccepted =
  components["schemas"]["ReflectionRegenerationAccepted"];
export type RunMetrics = components["schemas"]["RunMetrics"];
export type RunAttemptView = components["schemas"]["RunAttemptView"];
export type StructuredRecoveryNotice =
  components["schemas"]["StructuredRecoveryNotice"];
export type RecentInstrument = components["schemas"]["RecentInstrument"];
export type ResearchChain = components["schemas"]["ResearchChain"];
export type ResearchRevision = components["schemas"]["ResearchRevision"];
export type ResearchChainUpdateRequest =
  components["schemas"]["ResearchChainUpdateRequest"];

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
  providerModels: (provider: string, refresh = false) =>
    request<ProviderModelCatalog>(
      `/api/v1/providers/${encodeURIComponent(provider)}/models${
        refresh ? "?refresh=true" : ""
      }`,
    ),
  runs: (query = "") => request<RunPage>(`/api/v1/runs${query}`),
  trashRuns: (runIds: string[]) =>
    request<RunBatchResult>("/api/v1/runs/trash", {
      method: "POST",
      body: JSON.stringify({ run_ids: runIds }),
    }),
  restoreRuns: (runIds: string[]) =>
    request<RunBatchResult>("/api/v1/runs/restore", {
      method: "POST",
      body: JSON.stringify({ run_ids: runIds }),
    }),
  recentInstruments: (limit = 50) =>
    request<RecentInstrument[]>(
      `/api/v1/instruments/recent?limit=${encodeURIComponent(limit)}`,
    ),
  run: (id: string) => request<RunDetail>(`/api/v1/runs/${id}`),
  evidence: (id: string) =>
    request<EvidenceBundle>(`/api/v1/runs/${id}/evidence`),
  artifacts: (id: string, attempt?: number) =>
    request<ResearchArtifact[]>(
      `/api/v1/runs/${id}/artifacts${
        attempt === undefined ? "" : `?attempt=${attempt}`
      }`,
    ),
  createRun: (payload: RunCreateRequest, idempotencyKey: string) =>
    request<RunView>("/api/v1/runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),
  createResearchChain: (
    payload: RunCreateRequest,
    idempotencyKey: string,
  ) =>
    request<RunView>("/api/v1/research-chains", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),
  researchChains: (instrument = "") =>
    request<ResearchChain[]>(
      `/api/v1/research-chains${
        instrument ? `?instrument=${encodeURIComponent(instrument)}` : ""
      }`,
    ),
  researchChain: (id: string) =>
    request<ResearchChain>(`/api/v1/research-chains/${id}`),
  updateResearchChain: (
    id: string,
    payload: ResearchChainUpdateRequest,
    idempotencyKey: string,
  ) =>
    request<RunView>(`/api/v1/research-chains/${id}/updates`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    }),
  action: (id: string, action: "cancel" | "retry") =>
    request<RunView>(`/api/v1/runs/${id}/${action}`, { method: "POST" }),
  reviews: (query = "") =>
    request<ResearchReview[]>(`/api/v1/reviews${query}`),
  reviewAuditDetail: (outcomeId: number) =>
    request<ResearchReviewAuditDetail>(
      `/api/v1/reviews/${encodeURIComponent(outcomeId)}`,
    ),
  regenerateOutcomeReflection: (outcomeId: number, idempotencyKey: string) =>
    request<ReflectionRegenerationAccepted>(
      `/api/v1/outcome-observations/${encodeURIComponent(outcomeId)}/reflection/regenerations`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: "{}",
      },
    ),
  retireOutcomeFeedback: (feedbackId: number, reason: string) =>
    request<{ status: string }>(
      `/api/v1/outcome-feedback/${encodeURIComponent(feedbackId)}/retire`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  login: (token: string) =>
    request<{ authenticated: boolean }>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
};
