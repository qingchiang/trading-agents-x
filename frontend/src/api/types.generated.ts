/*
 * This file is generated from frontend/openapi.json.
 * Run `npm run openapi:generate` from frontend/ to update it.
 */

export interface components {
  schemas: {
    AnalysisRequest: {
      analysis_date: string;
      analysts?: ("market" | "social" | "news" | "fundamentals")[];
      asset_type?: components["schemas"]["AssetType"] | null;
      deep_model?: string | null;
      deep_reasoning_effort?: string | null;
      llm_provider?: string | null;
      output_language?: components["schemas"]["ReportLanguage"] | string | null;
      profile?: components["schemas"]["RunProfile"];
      quick_model?: string | null;
      quick_reasoning_effort?: string | null;
      ticker: string;
    };
    AnalysisResult: {
      decision: components["schemas"]["ResearchDecision"] | null;
      evidence?: components["schemas"]["EvidenceBundle"] | null;
      instrument: string;
      instrument_name?: string | null;
      metrics?: components["schemas"]["RunMetrics"];
      reports: Record<string, components["schemas"]["AnalystReport"] | string>;
      run_id: string;
      status: components["schemas"]["RunStatus"];
      warnings?: components["schemas"]["ResearchWarning"][];
    };
    AnalystClaim: {
      evidence_refs?: string[];
      text: string;
    };
    AnalystReport: {
      analyst: "market" | "social" | "news" | "fundamentals";
      claims?: components["schemas"]["AnalystClaim"][];
      confidence: number;
      evidence_refs?: string[];
      narrative: string;
      summary: string;
      warnings?: components["schemas"]["ResearchWarning"][];
    };
    ArtifactGenerationMethod: "tool_call" | "json_mode" | "narrative_adapted" | "raw_json_recovered" | "json_mode_recovered";
    AssetType: "stock" | "crypto";
    CapabilitiesResponse: {
      analysts: string[];
      defaults: components["schemas"]["CapabilityDefaults"];
      output_languages: string[];
      profiles: string[];
      providers: Record<string, components["schemas"]["ProviderCapabilities"]>;
    };
    CapabilityDefaults: {
      deep_model: string;
      deep_reasoning_effort: string | null;
      lan_enabled: boolean;
      llm_provider: string;
      output_language: string;
      profile: string;
      quick_model: string;
      quick_reasoning_effort: string | null;
      trash_retention_days: number;
    };
    DiscoveredModelView: {
      compatibility: "supported" | "unknown";
      default_roles: ("quick" | "deep")[];
      id: string;
      label: string;
      reasoning_efforts: string[];
    };
    EvidenceBundle: {
      analysis_date: string;
      digest?: string | null;
      instrument: string;
      items: components["schemas"]["EvidenceItem"][];
      sealed_at?: string;
      version?: "1" | "2";
    };
    EvidenceItem: {
      available_at?: string | null;
      content?: string | null;
      effective_date?: string | null;
      evidence_type: string;
      fallback?: boolean;
      origins?: components["schemas"]["EvidenceOrigin"][];
      provenance?: Record<string, unknown>;
      quality?: components["schemas"]["EvidenceQuality"];
      ref: string;
      requested_date: string;
      source: string;
      unit?: string | null;
      value?: number | string | null;
    };
    EvidenceOrigin: {
      effective?: string;
      effective_date?: string | null;
      evidence_type: string;
      fallback?: boolean;
      quality?: components["schemas"]["EvidenceQuality"];
      requested?: string;
      retrieved_at?: string | null;
      source: string;
      temporal_scope?: components["schemas"]["EvidenceTemporalScope"];
      timing?: string;
    };
    EvidenceQuality: "high" | "medium" | "low" | "unavailable";
    EvidenceTemporalScope: "point_in_time" | "live_only" | "unknown";
    HTTPValidationError: {
      detail?: components["schemas"]["ValidationError"][];
    };
    HealthResponse: {
      database: "ok" | "error";
      queue: components["schemas"]["QueueHealth"];
      status: "ok" | "degraded";
      version: string;
    };
    LoginRequest: {
      token: string;
    };
    MemoryEntry: {
      analysis_date: string;
      asset_type: string;
      decision: components["schemas"]["ResearchDecision"];
      instrument_name?: string | null;
      market: string | null;
      outcome: components["schemas"]["MemoryOutcome"];
      reflection: string | null;
      run_id: string;
      ticker: string;
    };
    MemoryOutcome: {
      alpha_return: number | null;
      benchmark: string;
      holding_intervals: number;
      observation_end: string | null;
      observation_start: string | null;
      raw_return: number | null;
      status: "pending" | "resolved";
    };
    ModelDiscoveryWarningView: {
      code: string;
      message: string;
    };
    NodeMetrics: {
      input_tokens?: number;
      llm_calls?: number;
      output_tokens?: number;
      tool_calls?: number;
      wall_time_seconds?: number;
    };
    PerspectiveReview: {
      claim_rebuttals?: string[];
      evidence_refs?: string[];
      new_evidence_refs?: string[];
      risks?: string[];
      role: string;
      thesis: string;
    };
    ProviderCapabilities: {
      api_key_configured: boolean | null;
      api_key_required: boolean;
      configured: boolean;
      label: string;
      model_discovery_supported: boolean;
      selectable: boolean;
      unavailable_reason?: string | null;
    };
    ProviderModelCatalog: {
      fetched_at: string;
      models: components["schemas"]["DiscoveredModelView"][];
      provider: string;
      source: "live" | "cache" | "fallback";
      stale: boolean;
      warning?: components["schemas"]["ModelDiscoveryWarningView"] | null;
    };
    QueueHealth: {
      pending_outcomes: number;
      queued: number;
      running: number;
    };
    RecentInstrument: {
      instrument_name?: string | null;
      last_used_at: string;
      ticker: string;
    };
    ReportLanguage: "en" | "zh-CN" | "ja";
    ResearchArtifact: {
      attempt: number;
      content: components["schemas"]["AnalystReport"] | components["schemas"]["PerspectiveReview"] | components["schemas"]["ResearchDecision"];
      created_at: string;
      generation_method: components["schemas"]["ArtifactGenerationMethod"];
      id: string;
      role: string;
      round?: number;
      run_id: string;
      schema_version?: string;
      stage: string;
    };
    ResearchDecision: {
      catalysts?: string[];
      confidence: number;
      evidence_refs?: string[];
      invalidation_conditions?: string[];
      memory_refs?: string[];
      rating: components["schemas"]["ResearchRating"];
      risks?: string[];
      thesis: string;
      time_horizon: string;
    };
    ResearchRating: "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell";
    ResearchWarning: {
      code?: string;
      evidence_ref?: string | null;
      message: string;
      source?: string | null;
    };
    RunBatchRequest: {
      run_ids: string[];
    };
    RunBatchResult: {
      changed: number;
      runs: components["schemas"]["RunView"][];
    };
    RunCreateRequest: {
      analysis_date: string;
      analysts?: ("market" | "social" | "news" | "fundamentals")[];
      asset_type?: components["schemas"]["AssetType"] | null;
      deep_model?: string | null;
      deep_reasoning_effort?: string | null;
      llm_provider?: string | null;
      output_language?: components["schemas"]["ReportLanguage"] | string | null;
      profile?: components["schemas"]["RunProfile"];
      quick_model?: string | null;
      quick_reasoning_effort?: string | null;
      source_run_id?: string | null;
      ticker: string;
    };
    RunDetail: {
      result?: components["schemas"]["AnalysisResult"] | null;
      run: components["schemas"]["RunView"];
    };
    RunEvent: {
      attempt: number;
      created_at: string;
      event_type: string;
      node?: string | null;
      payload?: Record<string, unknown>;
      run_id: string;
      sequence: number;
    };
    RunMetrics: {
      input_tokens?: number;
      llm_calls?: number;
      node_metrics?: Record<string, components["schemas"]["NodeMetrics"]>;
      output_tokens?: number;
      tool_calls?: number;
      wall_time_seconds?: number;
    };
    RunPage: {
      items: components["schemas"]["RunView"][];
      limit: number;
      offset: number;
      total: number;
    };
    RunProfile: "fast" | "standard" | "deep";
    RunStatus: "queued" | "running" | "succeeded" | "failed" | "cancelled";
    RunTrashState: "active" | "trashed" | "all";
    RunView: {
      attempt: number;
      cancel_requested: boolean;
      config_snapshot: Record<string, unknown>;
      created_at: string;
      error_code?: string | null;
      error_message?: string | null;
      finished_at?: string | null;
      id: string;
      instrument_name?: string | null;
      metrics?: components["schemas"]["RunMetrics"];
      request: components["schemas"]["AnalysisRequest"];
      source_run_id?: string | null;
      started_at?: string | null;
      status: components["schemas"]["RunStatus"];
      trashed_at?: string | null;
      updated_at: string;
    };
    ValidationError: {
      ctx?: {
      };
      input?: unknown;
      loc: (string | number)[];
      msg: string;
      type: string;
    };
  };
}
