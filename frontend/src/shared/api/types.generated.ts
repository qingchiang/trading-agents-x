/*
 * This file is generated from frontend/openapi.json.
 * Run `npm run openapi:generate` from frontend/ to update it.
 */

export interface components {
  schemas: {
    AnalysisCutoffContext: {
      instrument: string;
      market_date: string;
      market_timezone: string;
      max_analysis_date: string;
      observed_at: string;
      valid_until: string;
    };
    AnalysisCutoffError: {
      code: components["schemas"]["AnalysisCutoffErrorCode"];
      message: string;
    };
    AnalysisCutoffErrorCode: "future_analysis_cutoff";
    AnalysisCutoffErrorResponse: {
      context: components["schemas"]["AnalysisCutoffContext"];
      error: components["schemas"]["AnalysisCutoffError"];
      requested_analysis_date: string;
    };
    AnalysisRequest: {
      analysis_date: string;
      analysts?: ("market" | "social" | "news" | "fundamentals")[];
      full_baseline_run_id?: string | null;
      make_primary?: boolean | null;
      models?: components["schemas"]["RoleSelections"];
      output_language?: components["schemas"]["ReportLanguage"] | string | null;
      profile?: components["schemas"]["RunProfile"];
      research_kind?: "full" | "incremental";
      ticker: string;
    };
    AnalysisResult: {
      decision: components["schemas"]["ResearchDecision"] | null;
      evidence?: components["schemas"]["EvidenceBundle"] | null;
      instrument: string;
      instrument_local_name?: string | null;
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
    ArtifactGenerationObservation: {
      client_role: "quick_reasoning" | "deep_reasoning" | "quick_serializer" | "deep_serializer";
      generation_method: components["schemas"]["ArtifactGenerationMethod"];
      node: string;
      task_kind: "semantic_structured" | "schema_serialization";
    };
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
    AzureTransport: {
      api_version?: string | null;
      base_url?: string | null;
      deployment?: string | null;
      kind?: "azure";
    };
    BedrockTransport: {
      auth_mode?: "system" | "static" | "bearer";
      aws_profile?: string | null;
      kind?: "bedrock";
      region?: string;
    };
    BenchmarkContext: {
      component: components["schemas"]["PerformanceComponent"];
      name: string;
      reported_difference?: number | null;
    };
    CalculationRecord: {
      as_of_date: string;
      date_evidence_refs?: string[];
      decision_uses?: components["schemas"]["DecisionCalculationUse"][];
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
      configuration_initialized?: boolean;
      connections?: Record<string, components["schemas"]["ConnectionView"]>;
      defaults: components["schemas"]["CapabilityDefaults"];
      output_languages: string[];
      profiles: string[];
    };
    CapabilityDefaults: {
      analysts?: string[];
      lan_enabled: boolean;
      models: components["schemas"]["RoleSelections"];
      output_language: string;
      profile: string;
      trash_retention_days: number;
    };
    ClaimImportance: "primary" | "supporting";
    CollectionDiagnostic: {
      code: string;
    };
    CollectionDomainResult: {
      diagnostic?: components["schemas"]["CollectionDiagnostic"] | null;
      domain: "fundamentals" | "market" | "news" | "social";
      evidence_refs?: string[];
      observed_from?: string | null;
      observed_through?: string | null;
      omitted_by_temporal_boundary?: boolean;
      sources?: components["schemas"]["CollectionSourceProvenance"][];
      state: components["schemas"]["CollectionResultState"];
      temporal_bases?: components["schemas"]["CollectionTemporalBasis"][];
    };
    CollectionResultState: "data" | "empty" | "partial" | "unavailable";
    CollectionSourceProvenance: {
      diagnostic?: components["schemas"]["CollectionDiagnostic"] | null;
      fallback?: boolean;
      retrieved_at: string;
      source: string;
    };
    CollectionSummary: {
      domains: components["schemas"]["CollectionDomainResult"][];
      market: "united_states" | "japan" | "mainland_china";
      version: string;
    };
    CollectionTemporalBasis: "pit" | "near_live_advisory";
    ComparisonValueState: "recorded" | "null" | "empty" | "not_recorded_under_this_schema";
    ConfigurationField: {
      default?: unknown;
      description: Record<string, string>;
      env_names?: string[];
      group: string;
      key: string;
      kind: "text" | "number" | "boolean" | "list" | "choice" | "routes" | "models";
      label: Record<string, string>;
      maximum?: number | null;
      minimum?: number | null;
      nullable?: boolean;
      option_labels?: Record<string, Record<string, string>>;
      options?: string[];
    };
    ConfigurationPatch: {
      connection_changes?: components["schemas"]["ConnectionChange"][];
      credentials?: Record<string, string | null>;
      reset_fields?: string[];
      revision: number;
      values?: components["schemas"]["ConfigurationValues"];
    };
    ConfigurationSchema: {
      credential_metadata?: Record<string, components["schemas"]["CredentialMetadata"]>;
      credential_owners: Record<string, string>;
      fields: components["schemas"]["ConfigurationField"][];
      group_descriptions?: Record<string, Record<string, string>>;
      presets?: Record<string, components["schemas"]["ModelConnection"]>;
      providers: Record<string, string>;
      route_options: Record<string, string[]>;
      tool_options: Record<string, string[]>;
    };
    ConfigurationValues: {
      analysts?: ("market" | "social" | "news" | "fundamentals")[];
      cn_news_candidate_limit?: number;
      data_vendors?: Record<string, string>;
      data_vendors_by_market?: Record<string, Record<string, string>>;
      global_news_article_limit?: number;
      global_news_candidate_limit?: number;
      global_news_lookback_days?: number;
      global_news_queries?: string[];
      global_news_query_limit?: number;
      llm_max_retries?: number | null;
      models?: components["schemas"]["RoleSelections"];
      news_article_limit?: number;
      news_cache_enabled?: boolean;
      news_cache_refresh_seconds?: number;
      news_cache_retention_days?: number;
      news_cache_scope_limit?: number;
      news_cache_total_limit?: number;
      output_language?: string;
      profile?: "fast" | "standard" | "deep";
      sentiment_filing_limit?: number;
      social_lookback_days?: number;
      temperature?: number | null;
      ticker_news_lookback_days?: number;
      tool_vendors?: Record<string, string>;
      trash_retention_days?: number;
      yahoo_news_candidate_limit?: number;
    };
    ConfigurationView: {
      connections?: Record<string, components["schemas"]["ConnectionView"]>;
      credentials: Record<string, boolean>;
      deployment: Record<string, string | number | boolean>;
      initialized: boolean;
      revision: number;
      sources: Record<string, "database" | "default">;
      values: components["schemas"]["ConfigurationValues"];
    };
    ConnectionChange: {
      action: "create" | "update" | "delete" | "reset";
      compatibility?: string | null;
      credentials?: Record<string, string | null>;
      discovery?: "openai_compatible" | "anthropic" | "google" | "ollama" | "bedrock" | "custom" | null;
      enabled?: boolean | null;
      id: string;
      key_required?: boolean | null;
      name?: string | null;
      preset?: string | null;
      reasoning_effort?: string | null;
      transport?: components["schemas"]["EndpointTransport"] | components["schemas"]["AzureTransport"] | components["schemas"]["BedrockTransport"] | null;
    };
    ConnectionModelCatalog: {
      connection_id: string;
      fetched_at: string;
      models: components["schemas"]["DiscoveredModelView"][];
      source: "live" | "cache" | "fallback";
      stale: boolean;
      warning?: components["schemas"]["ModelDiscoveryWarningView"] | null;
    };
    ConnectionView: {
      connection: components["schemas"]["ModelConnection"];
      credentials: Record<string, boolean>;
      missing_fields: string[];
      reasoning_efforts?: string[];
      selectable: boolean;
      unavailable_reason?: string | null;
    };
    CredentialMetadata: {
      description: Record<string, string>;
      label: string;
    };
    CredentialRequest: {
      connection_id?: string | null;
      name: string;
    };
    CredentialView: {
      value: string | null;
    };
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
    DecisionBrief: {
      evidence_refs?: string[];
      markdown: string;
      warnings?: components["schemas"]["ResearchWarning"][];
    };
    DecisionCalculationUse: {
      component_path: string;
      label: string;
    };
    DecisionNumericAuditAppendix: {
      omitted_components?: components["schemas"]["NumericAuditOmission"][];
      requirement_checks?: components["schemas"]["NumericRequirementCheck"][];
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
    EndpointTransport: {
      base_url?: string | null;
      kind?: "chat_completions" | "responses" | "anthropic" | "google";
    };
    EvidenceBundle: {
      analysis_date: string;
      digest?: string | null;
      instrument: string;
      items: components["schemas"]["EvidenceItem"][];
      sealed_at?: string;
      tables?: components["schemas"]["EvidenceTable"][];
      version?: "8";
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
    FullBaselineCandidate: {
      analysis_date: string;
      confidence?: components["schemas"]["ResearchConfidenceLevel"] | null;
      cycle_warning?: boolean;
      id: string;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      is_primary?: boolean;
      rating?: components["schemas"]["ResearchRating"] | null;
      thesis?: string | null;
    };
    FullBaselineCandidates: {
      before: string;
      instrument: string;
      items?: components["schemas"]["FullBaselineCandidate"][];
    };
    FullResearchRequiredReason: {
      code: "thesis.material_reversal" | "identity.uncertain" | "attribution.unreliable" | "evidence.material_conflict";
      evidence_refs?: string[];
      message: string;
      origin: "deterministic" | "semantic";
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
    ImportIssue: {
      message: string;
      name: string;
    };
    ImportPreview: {
      conflicts: string[];
      connection_targets?: Record<string, string>;
      credentials: Record<string, boolean>;
      fingerprint: string;
      issues: components["schemas"]["ImportIssue"][];
      revision: number;
      values: Record<string, unknown>;
    };
    ImportRequest: {
      enterprise?: string | null;
      exclude?: string[];
      fingerprint?: string | null;
      primary?: string | null;
      revision?: number;
      use_defaults?: boolean;
    };
    IncrementalAnalysisBrief: {
      evidence_refs?: string[];
      generation_method?: "markdown_audited";
      markdown: string;
      prompt_version?: "incremental-analysis-brief-v1";
      report_sections: components["schemas"]["ReportSection"][];
      warnings?: components["schemas"]["ResearchWarning"][];
    };
    IncrementalBaselineContext: {
      analysis_date: string;
      decision: components["schemas"]["ResearchDecision"];
      run_id: string;
    };
    IncrementalDecisionOutcome: "unchanged" | "updated";
    IncrementalRunContext: {
      analysis_brief?: components["schemas"]["IncrementalAnalysisBrief"] | null;
      full_baseline: components["schemas"]["IncrementalBaselineContext"];
    };
    InformationAdvancement: {
      advanced: boolean;
      observation_ids?: string[];
      reasons?: ("admissible_observation" | "completed_stock_session")[];
    };
    InstrumentAdmissionError: {
      code: components["schemas"]["InstrumentAdmissionErrorCode"];
      message: string;
    };
    InstrumentAdmissionErrorCode: "unsupported_instrument" | "instrument_eligibility_unavailable";
    InstrumentAdmissionErrorResponse: {
      error: components["schemas"]["InstrumentAdmissionError"];
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
    ModelConnection: {
      compatibility?: string;
      deleted?: boolean;
      discovery?: "openai_compatible" | "anthropic" | "google" | "ollama" | "bedrock" | "custom";
      enabled?: boolean;
      id: string;
      key_required?: boolean;
      name: string;
      preset?: string | null;
      reasoning_effort?: string | null;
      revision?: number;
      template?: Record<string, unknown>;
      template_origin?: "creation" | "upgrade";
      transport: components["schemas"]["EndpointTransport"] | components["schemas"]["AzureTransport"] | components["schemas"]["BedrockTransport"];
    };
    ModelDiscoveryWarningView: {
      code: string;
      message: string;
    };
    ModelSelection: {
      connection_id?: string | null;
      model?: string | null;
      reasoning_effort?: string | null;
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
    NumericAuditAppendixStatus: "complete" | "recovered" | "partial" | "incomplete";
    NumericAuditComponentType: "appendix" | "calculation" | "scenario_range" | "valuation" | "market_reference" | "decision_claim";
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
      candidate_omitted?: "oversize" | null;
      method: components["schemas"]["ArtifactGenerationMethod"];
      phase: components["schemas"]["NumericAuditPhase"];
      reason_code: string;
      schema_valid: boolean;
      validation_issues?: string[];
    };
    NumericAuditStatus: "complete" | "partial" | "incomplete" | "not_applicable";
    NumericCalculationStatus: "verified" | "invalid" | "missing";
    NumericDisplayScale: "base" | "thousand" | "ten_thousand" | "million" | "hundred_million" | "billion" | "trillion";
    NumericDisplayStatus: "matched" | "approximately_matched" | "mismatched" | "not_checked";
    NumericRequirementCheck: {
      calculation_id?: string | null;
      calculation_status: components["schemas"]["NumericCalculationStatus"];
      canonical_result?: number | null;
      comparison_difference?: number | null;
      comparison_result?: number | null;
      component_path: string;
      date_evidence_refs?: string[];
      display_scale?: components["schemas"]["NumericDisplayScale"];
      display_status: components["schemas"]["NumericDisplayStatus"];
      formula: string;
      fraction_digits: number;
      input_evidence_refs: string[];
      inputs: Record<string, number>;
      issue_codes?: string[];
      label: string;
      requirement_id: string;
      rounded_canonical_result?: number | null;
      rounded_stated_value?: number | null;
      stated_value: number;
      unit: string;
    };
    NumericTemporalBasis: "point_in_time" | "live_snapshot";
    PerformanceCalculationRecord: {
      adjustment_basis: string;
      baseline_information_cutoff_at: string;
      end_session: string;
      end_value: number;
      fallback?: boolean;
      formula?: "(end_value / start_value) - 1";
      provider: string;
      retrieved_at: string;
      start_session: string;
      start_value: number;
      target_information_cutoff_at: string;
      unrounded_return: number;
    };
    PerformanceComponent: {
      calculation?: components["schemas"]["PerformanceCalculationRecord"] | null;
      reason?: string | null;
      status: components["schemas"]["PerformanceComponentStatus"];
    };
    PerformanceComponentStatus: "calculated" | "not_yet_observable" | "unavailable";
    PerformanceObservation: {
      benchmarks?: components["schemas"]["BenchmarkContext"][];
      stock: components["schemas"]["PerformanceComponent"];
    };
    PrimaryCycleCandidate: {
      analysis_date: string;
      confidence?: components["schemas"]["ResearchConfidenceLevel"] | null;
      id: string;
      is_primary?: boolean;
      rating?: components["schemas"]["ResearchRating"] | null;
    };
    PrimaryCycleSelectionRequest: {
      full_run_id: string;
    };
    QueueHealth: {
      queued: number;
      running: number;
    };
    ReassessmentDisposition: "reaffirmed" | "strengthened" | "weakened" | "overturned" | "unresolved";
    RebuttalReview: {
      addressed_issue_ids: string[];
      markdown: string;
      open_issue_ids?: string[];
      role: "bull" | "bear";
      round: number;
    };
    RecentInstrument: {
      instrument_local_name?: string | null;
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
    RequestValidationDetail: {
      location: string[];
      message: string;
      type: string;
    };
    RequestValidationError: {
      code: components["schemas"]["RequestValidationErrorCode"];
      message: string;
    };
    RequestValidationErrorCode: "validation_error";
    RequestValidationErrorResponse: {
      details: components["schemas"]["RequestValidationDetail"][];
      error: components["schemas"]["RequestValidationError"];
    };
    ResearchArtifact: {
      attempt: number;
      content: components["schemas"]["AnalystReport"] | components["schemas"]["DecisionBrief"] | components["schemas"]["ResearchCase"] | components["schemas"]["DebateAgenda"] | components["schemas"]["RebuttalReview"] | components["schemas"]["JudgeDraft"] | components["schemas"]["RiskReview"] | components["schemas"]["ResearchDecision"];
      created_at: string;
      generation_method: components["schemas"]["ArtifactGenerationMethod"];
      generation_observations?: components["schemas"]["ArtifactGenerationObservation"][];
      id: string;
      prompt_version?: string;
      role: string;
      round?: number;
      run_id: string;
      schema_version?: "2";
      stage: string;
    };
    ResearchAvailability: {
      domains: components["schemas"]["ResearchAvailabilityDomain"][];
      version: string;
    };
    ResearchAvailabilityDomain: {
      domain: "fundamentals" | "market" | "news" | "social";
      status: components["schemas"]["ResearchAvailabilityStatus"];
    };
    ResearchAvailabilityStatus: "available" | "limited" | "missing";
    ResearchCase: {
      markdown: string;
      role: "bull" | "bear";
    };
    ResearchConfidenceLevel: "low" | "medium" | "high";
    ResearchCycleView: {
      baseline: components["schemas"]["ResearchNodeView"];
      cycle_warning?: boolean;
      head_run_id: string;
      id: string;
      increments?: components["schemas"]["ResearchNodeView"][];
      is_primary?: boolean;
    };
    ResearchDecision: {
      calculation_records?: components["schemas"]["CalculationRecord"][];
      catalysts?: string[];
      confidence: components["schemas"]["ResearchConfidenceLevel"];
      evidence_refs?: string[];
      executive_summary: string;
      invalidation_conditions: string[];
      market_reference_levels?: components["schemas"]["MarketReferenceLevel"][];
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
    ResearchNodeComparison: {
      cross_cycle: boolean;
      decision_sections: components["schemas"]["ResearchNodeDecisionSection"][];
      instrument: string;
      method_changed: boolean;
      sides: components["schemas"]["ResearchNodeComparisonSide"][];
      warnings?: components["schemas"]["ResearchNodeComparisonWarning"][];
    };
    ResearchNodeComparisonRequest: {
      nodes: components["schemas"]["ResearchNodeComparisonSelection"][];
    };
    ResearchNodeComparisonSelection: {
      lifecycle_state?: components["schemas"]["ResearchNodeLifecycleState"];
      node_id: string;
    };
    ResearchNodeComparisonSide: {
      analysis_date: string;
      collection_summary?: components["schemas"]["CollectionSummary"] | null;
      cycle_id: string;
      decision: Record<string, unknown>;
      decision_outcome?: components["schemas"]["IncrementalDecisionOutcome"] | null;
      decision_outcome_reason?: string | null;
      full_research_required_reasons?: components["schemas"]["FullResearchRequiredReason"][];
      information_advancement?: components["schemas"]["InformationAdvancement"] | null;
      lifecycle_state: components["schemas"]["ResearchNodeLifecycleState"];
      method_snapshot: Record<string, unknown>;
      node_id: string;
      performance?: components["schemas"]["PerformanceObservation"] | null;
      reassessment?: components["schemas"]["ResearchReassessment"] | null;
      research_availability?: components["schemas"]["ResearchAvailability"] | null;
      research_kind: "full" | "incremental";
      research_schema_version: string;
    };
    ResearchNodeComparisonValue: {
      state: components["schemas"]["ComparisonValueState"];
      value?: unknown;
    };
    ResearchNodeComparisonWarning: {
      code: "method_changed";
      message: string;
    };
    ResearchNodeDecisionSection: {
      key: string;
      values: components["schemas"]["ResearchNodeComparisonValue"][];
    };
    ResearchNodeLifecycleState: "active" | "trashed";
    ResearchNodeView: {
      analysis_date: string;
      collection_summary?: components["schemas"]["CollectionSummary"] | null;
      cycle_id: string;
      cycle_warning?: boolean;
      decision?: components["schemas"]["ResearchDecision"] | null;
      decision_outcome?: components["schemas"]["IncrementalDecisionOutcome"] | null;
      decision_outcome_reason?: string | null;
      full_baseline_run_id?: string | null;
      full_research_required_reasons?: components["schemas"]["FullResearchRequiredReason"][];
      id: string;
      information_advancement?: components["schemas"]["InformationAdvancement"] | null;
      information_cutoff_at: string;
      instrument: string;
      is_active: boolean;
      is_baseline_compatible: boolean;
      is_cycle_head: boolean;
      is_primary: boolean;
      method_snapshot: Record<string, unknown>;
      performance?: components["schemas"]["PerformanceObservation"] | null;
      reassessment?: components["schemas"]["ResearchReassessment"] | null;
      research_availability?: components["schemas"]["ResearchAvailability"] | null;
      research_kind: "full" | "incremental";
      research_schema_version: string;
      trash_cascade_full_run_id?: string | null;
      trashed_at?: string | null;
    };
    ResearchRating: "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell";
    ResearchReassessment: {
      entries: components["schemas"]["ResearchReassessmentEntry"][];
    };
    ResearchReassessmentEntry: {
      component_id: string;
      disposition: components["schemas"]["ReassessmentDisposition"];
      evidence_refs?: string[];
      reason: string;
    };
    ResearchScenario: {
      core_assumptions: string[];
      evidence_refs?: string[];
      kind: components["schemas"]["ResearchScenarioKind"];
      outcome: string;
      reference_ranges?: components["schemas"]["ScenarioReferenceRange"][];
    };
    ResearchScenarioKind: "base" | "bull" | "bear";
    ResearchTimeline: {
      active_full_cycles?: components["schemas"]["PrimaryCycleCandidate"][];
      cycle_limit?: number;
      cycle_offset?: number;
      cycle_total?: number;
      cycles?: components["schemas"]["ResearchCycleView"][];
      instrument: string;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      primary_cycle_id?: string | null;
      timeline_warning?: boolean;
    };
    ResearchTimelinePage: {
      items?: components["schemas"]["ResearchTimelineSummary"][];
      limit: number;
      offset: number;
      total: number;
    };
    ResearchTimelineSummary: {
      full_cycle_count: number;
      incremental_node_count?: number;
      instrument: string;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      latest_analysis_date: string;
      latest_completed_analysis_date?: string | null;
      latest_completed_cycle_id?: string | null;
      latest_completed_run_id?: string | null;
      latest_research_completed_at?: string | null;
      primary_analysis_date?: string | null;
      primary_baseline_date?: string | null;
      primary_confidence?: components["schemas"]["ResearchConfidenceLevel"] | null;
      primary_cycle_id?: string | null;
      primary_head_run_id?: string | null;
      primary_rating?: components["schemas"]["ResearchRating"] | null;
      primary_thesis?: string | null;
      timeline_warning?: boolean;
    };
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
    RoleSelections: {
      deep?: components["schemas"]["ModelSelection"] | null;
      quick?: components["schemas"]["ModelSelection"] | null;
    };
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
      expected_affected_run_ids?: string[] | null;
      primary_replacements?: Record<string, string>;
      run_ids: string[];
    };
    RunBatchResult: {
      changed: number;
      impacts?: components["schemas"]["RunLifecycleImpact"][];
      runs: components["schemas"]["RunView"][];
    };
    RunCreateRequest: {
      analysis_date: string;
      analysts?: ("market" | "social" | "news" | "fundamentals")[];
      full_baseline_run_id?: string | null;
      make_primary?: boolean | null;
      models?: components["schemas"]["RoleSelections"];
      output_language?: components["schemas"]["ReportLanguage"] | string | null;
      profile?: components["schemas"]["RunProfile"];
      research_kind?: "full" | "incremental";
      source_run_id?: string | null;
      ticker: string;
    };
    RunCreationTemplate: {
      full_baseline_run_id?: string | null;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      request: components["schemas"]["RunRequestSnapshot"] | components["schemas"]["AnalysisRequest"];
      research_kind?: "full" | "incremental" | null;
      run_id: string;
      status: components["schemas"]["RunStatus"];
    };
    RunDetail: {
      attempts?: components["schemas"]["RunAttemptView"][];
      evidence_status: components["schemas"]["EvidenceSealView"];
      incremental_context?: components["schemas"]["IncrementalRunContext"] | null;
      research_node?: components["schemas"]["ResearchNodeView"] | null;
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
    RunGroupPage: {
      items: components["schemas"]["RunGroupView"][];
      limit: number;
      offset: number;
      total: number;
    };
    RunGroupView: {
      baseline?: components["schemas"]["RunSummaryView"] | null;
      cycle_warning?: boolean;
      id: string;
      instrument: string;
      is_primary?: boolean;
      kind: "cycle" | "standalone";
      matched_run_ids?: string[];
      related_tasks?: components["schemas"]["RunSummaryView"][];
      research_runs?: components["schemas"]["RunSummaryView"][];
      status_counts?: Record<string, number>;
    };
    RunLifecycleImpact: {
      affected_run_ids?: string[];
      cascade_moved_run_ids?: string[];
      cycle_id?: string | null;
      replacement_primary_cycle_id?: string | null;
      requested_run_id: string;
      research_kind?: "full" | "incremental" | null;
    };
    RunLifecyclePreview: {
      action: "trash" | "restore" | "purge";
      affected_run_ids: string[];
      affected_runs: components["schemas"]["RunView"][];
      blocked_reasons?: string[];
      primary_replacements?: Record<string, components["schemas"]["PrimaryCycleCandidate"][]>;
    };
    RunLifecyclePreviewRequest: {
      action: "trash" | "restore" | "purge";
      run_ids: string[];
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
      items: components["schemas"]["RunSummaryView"][];
      limit: number;
      offset: number;
      total: number;
    };
    RunProfile: "fast" | "standard" | "deep";
    RunRequestSnapshot: {
      analysis_date: string;
      analysts?: ("market" | "social" | "news" | "fundamentals")[];
      full_baseline_run_id?: string | null;
      make_primary?: boolean | null;
      models?: components["schemas"]["RoleSelections"];
      output_language?: components["schemas"]["ReportLanguage"] | string | null;
      profile?: components["schemas"]["RunProfile"];
      research_kind?: "full" | "incremental";
      ticker: string;
    };
    RunStatus: "queued" | "running" | "succeeded" | "failed" | "cancelled";
    RunSummaryView: {
      attempt: number;
      audit_snapshot?: Record<string, unknown> | null;
      cancel_requested: boolean;
      config_snapshot: Record<string, unknown>;
      created_at: string;
      error_code?: string | null;
      error_message?: string | null;
      finished_at?: string | null;
      full_baseline_run_id?: string | null;
      id: string;
      information_cutoff_at?: string | null;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      is_research_node?: boolean;
      method_snapshot?: Record<string, unknown> | null;
      metrics?: components["schemas"]["RunMetrics"];
      request: components["schemas"]["RunRequestSnapshot"] | components["schemas"]["AnalysisRequest"];
      research_confidence?: components["schemas"]["ResearchConfidenceLevel"] | null;
      research_kind?: "full" | "incremental" | null;
      research_rating?: components["schemas"]["ResearchRating"] | null;
      research_schema_version?: string | null;
      source_run_id?: string | null;
      started_at?: string | null;
      status: components["schemas"]["RunStatus"];
      trashed_at?: string | null;
      updated_at: string;
    };
    RunTrashState: "active" | "trashed" | "all";
    RunView: {
      attempt: number;
      audit_snapshot?: Record<string, unknown> | null;
      cancel_requested: boolean;
      config_snapshot: Record<string, unknown>;
      created_at: string;
      error_code?: string | null;
      error_message?: string | null;
      finished_at?: string | null;
      full_baseline_run_id?: string | null;
      id: string;
      information_cutoff_at?: string | null;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      is_research_node?: boolean;
      method_snapshot?: Record<string, unknown> | null;
      metrics?: components["schemas"]["RunMetrics"];
      request: components["schemas"]["RunRequestSnapshot"] | components["schemas"]["AnalysisRequest"];
      research_kind?: "full" | "incremental" | null;
      research_schema_version?: string | null;
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
    TimelineDetail: {
      timeline: components["schemas"]["ResearchTimeline"];
    };
    ValidationError: {
      ctx?: {
      };
      input?: unknown;
      loc: (string | number)[];
      msg: string;
      type: string;
    };
    ValuationAssessment: {
      high: components["schemas"]["AuditedRangeEndpoint"];
      limitations: string[];
      low: components["schemas"]["AuditedRangeEndpoint"];
      measurement_kind: components["schemas"]["MeasurementKind"];
      method: string;
      unit: string;
    };
  };
}
