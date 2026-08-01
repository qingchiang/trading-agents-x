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
      numeric_audit?: components["schemas"]["DecisionNumericAuditAppendix"] | null;
      recoveries?: components["schemas"]["StructuredRecoveryNotice"][];
      reports: Record<string, components["schemas"]["AnalystReport"] | string>;
      run_id: string;
      status: components["schemas"]["RunStatus"];
      warnings?: components["schemas"]["ResearchWarning"][];
    };
    AnalystClaimType: "observation" | "inference" | "forecast";
    AnalystReport: {
      analyst: "market" | "social" | "news" | "fundamentals";
      audit_status: components["schemas"]["ReportAuditStatus"];
      confidence?: number | null;
      key_claims?: components["schemas"]["KeyClaim"][];
      markdown: string;
      report_sections: components["schemas"]["ReportSection"][];
      source_refs?: string[];
      warnings?: components["schemas"]["ResearchWarning"][];
    };
    ArtifactGenerationMethod: "tool_call" | "tool_call_recovered" | "json_mode" | "raw_json_recovered" | "json_mode_recovered" | "sectioned_recovery" | "markdown_audited" | "markdown_audit_incomplete";
    AssetType: "stock" | "crypto";
    AuditedRangeEndpoint: {
      as_of_date: string;
      basis: components["schemas"]["MarketReferenceBasis"];
      calculation_id?: string | null;
      date_evidence_refs: string[];
      evidence_refs: string[];
      source_locator?: components["schemas"]["EvidenceValueLocator"] | null;
      temporal_basis?: components["schemas"]["NumericTemporalBasis"];
      value: number;
    };
    CalculationRecord: {
      as_of_date: string;
      formula: string;
      id: string;
      input_evidence_refs: string[];
      inputs: Record<string, number>;
      limitations: string[];
      result: number;
      temporal_basis?: components["schemas"]["NumericTemporalBasis"];
      unit: string;
    };
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
    ClaimImportance: "primary" | "supporting";
    DebateAgenda: {
      issues: components["schemas"]["DebateIssue"][];
      summary: string;
    };
    DebateImportance: "critical" | "material" | "secondary";
    DebateIssue: {
      id: string;
      importance: components["schemas"]["DebateImportance"];
      question: string;
    };
    DecisionNumericAuditAppendix: {
      omitted_components?: components["schemas"]["NumericAuditOmission"][];
      snapshots: components["schemas"]["NumericAuditSnapshot"][];
      status: components["schemas"]["NumericAuditAppendixStatus"];
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
      tables?: components["schemas"]["EvidenceTable"][];
      version?: string;
    };
    EvidenceItem: {
      available_at?: string | null;
      content?: string | null;
      effective_date?: string | null;
      evidence_type: string;
      fallback?: boolean;
      measurement_kind?: components["schemas"]["MeasurementKind"];
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
    EvidenceSealView: {
      digest?: string | null;
      item_count?: number;
      sealed_at?: string | null;
      sealed_attempt?: number | null;
      status: "pending" | "sealed";
      table_count?: number;
    };
    EvidenceTable: {
      columns: components["schemas"]["EvidenceTableColumn"][];
      evidence_refs: string[];
      id: string;
      purpose: string;
      rows: components["schemas"]["EvidenceTableRow"][];
      source_format: "structured" | "markdown" | "csv";
      title: string;
    };
    EvidenceTableCell: {
      measurement_kind?: components["schemas"]["MeasurementKind"] | null;
      raw_value?: string | number | boolean | null;
      source_refs?: string[];
      unit?: string | null;
    };
    EvidenceTableColumn: {
      data_type?: components["schemas"]["TableDataType"];
      key: string;
      label: string;
      measurement_kind?: components["schemas"]["MeasurementKind"];
      unit?: string | null;
    };
    EvidenceTableRow: {
      cells: Record<string, components["schemas"]["EvidenceTableCell"]>;
      id: string;
      source_refs?: string[];
    };
    EvidenceTemporalScope: "point_in_time" | "live_only" | "unknown";
    EvidenceValueLocator: {
      column?: string | null;
      evidence_ref: string;
      row_id?: string | null;
      table_id?: string | null;
    };
    HTTPValidationError: {
      detail?: components["schemas"]["ValidationError"][];
    };
    HealthResponse: {
      database: "ok" | "error";
      queue: components["schemas"]["QueueHealth"];
      status: "ok" | "degraded";
      version: string;
    };
    IssueDisposition: {
      issue_id: string;
      status: "upheld" | "rejected" | "unresolved";
    };
    JudgeDraft: {
      confidence?: number | null;
      issue_dispositions: components["schemas"]["IssueDisposition"][];
      markdown: string;
      preliminary_rating?: components["schemas"]["ResearchRating"] | null;
    };
    KeyClaim: {
      confidence: number;
      evidence_refs?: string[];
      id: string;
      implication: string;
      importance: components["schemas"]["ClaimImportance"];
      kind: components["schemas"]["AnalystClaimType"];
      section_id: string;
      statement: string;
    };
    LoginRequest: {
      token: string;
    };
    MarketReferenceBasis: "observed" | "interpreted" | "derived";
    MarketReferenceLevel: {
      as_of_date: string;
      basis?: components["schemas"]["MarketReferenceBasis"];
      calculation_ids?: string[];
      date_evidence_refs: string[];
      evidence_refs: string[];
      interpretation: string;
      label: string;
      measurement_kind?: components["schemas"]["MeasurementKind"];
      source_locator?: components["schemas"]["EvidenceValueLocator"] | null;
      temporal_basis?: components["schemas"]["NumericTemporalBasis"];
      unit?: string | null;
      value: number;
    };
    MeasurementKind: "currency" | "percent" | "ratio" | "index" | "quantity" | "count" | "basis_points" | "unitless" | "unknown";
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
      cache_hit_input_tokens?: number;
      cache_miss_input_tokens?: number;
      detailed_usage_calls?: number;
      input_tokens?: number;
      llm_calls?: number;
      output_tokens?: number;
      reasoning_output_tokens?: number;
      tool_calls?: number;
      wall_time_seconds?: number;
    };
    NumericAuditAppendixStatus: "recovered" | "partial" | "incomplete";
    NumericAuditComponentType: "appendix" | "calculation" | "scenario_range" | "valuation" | "market_reference";
    NumericAuditOmission: {
      component_path: string;
      component_type: components["schemas"]["NumericAuditComponentType"];
      issue_codes: string[];
      reference_label?: string | null;
      scenario_kind?: components["schemas"]["ResearchScenarioKind"] | null;
    };
    NumericAuditPhase: "initial" | "repair";
    NumericAuditSnapshot: {
      candidate?: Record<string, unknown> | null;
      candidate_digest?: string | null;
      candidate_omitted?: string | null;
      method: components["schemas"]["ArtifactGenerationMethod"];
      phase: components["schemas"]["NumericAuditPhase"];
      reason_code: string;
      schema_valid: boolean;
      validation_issues?: string[];
    };
    NumericAuditStatus: "complete" | "partial" | "incomplete" | "not_applicable";
    NumericTemporalBasis: "point_in_time" | "live_snapshot";
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
    RebuttalReview: {
      addressed_issue_ids: string[];
      markdown: string;
      open_issue_ids?: string[];
      role: "bull" | "bear";
      round: number;
    };
    RecentInstrument: {
      instrument_name?: string | null;
      last_used_at: string;
      ticker: string;
    };
    ReportAuditStatus: "complete" | "incomplete";
    ReportLanguage: "en" | "zh-CN" | "ja";
    ReportSection: {
      anchor: string;
      id: string;
      source_refs?: string[];
      title: string;
    };
    ResearchArtifact: {
      attempt: number;
      content: components["schemas"]["AnalystReport"] | components["schemas"]["ResearchCase"] | components["schemas"]["DebateAgenda"] | components["schemas"]["RebuttalReview"] | components["schemas"]["JudgeDraft"] | components["schemas"]["RiskReview"] | components["schemas"]["ResearchDecision"];
      created_at: string;
      generation_method: components["schemas"]["ArtifactGenerationMethod"];
      id: string;
      prompt_version?: string;
      role: string;
      round?: number;
      run_id: string;
      schema_version?: string;
      stage: string;
    };
    ResearchCase: {
      markdown: string;
      role: "bull" | "bear";
    };
    ResearchDecision: {
      calculation_records?: components["schemas"]["CalculationRecord"][];
      catalysts?: string[];
      confidence: number;
      evidence_refs?: string[];
      executive_summary: string;
      invalidation_conditions: string[];
      market_reference_levels?: components["schemas"]["MarketReferenceLevel"][];
      memory_refs?: string[];
      numeric_audit_status?: components["schemas"]["NumericAuditStatus"] | null;
      rating: components["schemas"]["ResearchRating"];
      risk_review_adjustments?: components["schemas"]["RiskReviewAdjustment"][];
      risks: string[];
      scenarios: components["schemas"]["ResearchScenario"][];
      thesis: string;
      time_horizon: string;
      unresolved_questions?: string[];
      valuation_assessment?: components["schemas"]["ValuationAssessment"] | null;
    };
    ResearchRating: "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell";
    ResearchScenario: {
      core_assumptions: string[];
      evidence_refs?: string[];
      kind: components["schemas"]["ResearchScenarioKind"];
      outcome: string;
      reference_ranges?: components["schemas"]["ScenarioReferenceRange"][];
    };
    ResearchScenarioKind: "base" | "bull" | "bear";
    ResearchWarning: {
      code?: string;
      evidence_ref?: string | null;
      message: string;
      source?: string | null;
    };
    RiskReview: {
      challenged_issue_ids?: string[];
      markdown: string;
      role: "integrated" | "aggressive" | "neutral" | "conservative";
      unresolved_issue_ids?: string[];
    };
    RiskReviewAdjustment: {
      disposition: components["schemas"]["RiskReviewDisposition"];
      evidence_refs?: string[];
      explanation: string;
      source_role: "integrated" | "aggressive" | "neutral" | "conservative";
      subject: string;
    };
    RiskReviewDisposition: "retained" | "modified" | "rejected";
    RunAttemptView: {
      attempt: number;
      error_code?: string | null;
      finished_at?: string | null;
      metrics?: components["schemas"]["RunMetrics"];
      resume_count?: number;
      started_at?: string | null;
      status: components["schemas"]["RunStatus"];
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
      attempts?: components["schemas"]["RunAttemptView"][];
      evidence_status: components["schemas"]["EvidenceSealView"];
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
      cache_hit_input_tokens?: number;
      cache_miss_input_tokens?: number;
      detailed_usage_calls?: number;
      input_tokens?: number;
      llm_calls?: number;
      node_metrics?: Record<string, components["schemas"]["NodeMetrics"]>;
      output_tokens?: number;
      reasoning_output_tokens?: number;
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
    ScenarioReferenceCategory: "technical" | "historical" | "analyst_consensus" | "fundamental" | "other";
    ScenarioReferenceRange: {
      category: components["schemas"]["ScenarioReferenceCategory"];
      high: components["schemas"]["AuditedRangeEndpoint"];
      interpretation: string;
      label: string;
      limitations: string[];
      low: components["schemas"]["AuditedRangeEndpoint"];
      measurement_kind?: components["schemas"]["MeasurementKind"];
      unit?: string | null;
    };
    StructuredRecoveryNotice: {
      attempt: number;
      initial_reason_code: string;
      node: string;
      recovered_at: string;
      recovery_method: components["schemas"]["ArtifactGenerationMethod"];
      retry_count: number;
      validation_issue_codes?: string[];
    };
    TableDataType: "text" | "integer" | "number" | "percent" | "currency" | "date" | "datetime" | "boolean";
    ValidationError: {
      ctx?: {
      };
      input?: unknown;
      loc: (string | number)[];
      msg: string;
      type: string;
    };
    ValuationAssessment: {
      currency: string;
      high: components["schemas"]["AuditedRangeEndpoint"];
      limitations: string[];
      low: components["schemas"]["AuditedRangeEndpoint"];
      method: string;
    };
  };
}
