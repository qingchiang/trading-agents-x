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
    AssetType: "stock";
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
    ClaimChange: "introduced" | "reaffirmed" | "strengthened" | "weakened" | "invalidated" | "retired" | "superseded";
    ClaimConfidence: "low" | "medium" | "high" | "indeterminate";
    ClaimImportance: "primary" | "supporting";
    ClaimRevisionDelta: {
      change: components["schemas"]["ClaimChange"];
      identity_disposition: components["schemas"]["IdentityDisposition"];
      object_id: string;
      previous_object_id?: string | null;
    };
    ClaimStanding: "active" | "invalidated" | "retired";
    CoverageAttestation: {
      claims: components["schemas"]["ResearchObjectCoverage"][];
      domains: components["schemas"]["ResearchDomainCoverage"][];
      limitations?: string[];
      questions: components["schemas"]["ResearchObjectCoverage"][];
      schema_version?: string;
      supports_no_material_change?: boolean;
    };
    CoverageRequirement: "required" | "advisory";
    CoverageStatus: "complete" | "limited" | "unavailable";
    CurrentResearchState: {
      catalysts?: components["schemas"]["ResearchFactor"][];
      claims: components["schemas"]["ResearchClaim"][];
      cutoff: string;
      evidence_refs: string[];
      instrument: string;
      invalidation_conditions?: components["schemas"]["ResearchFactor"][];
      language: string;
      market_reference_levels?: components["schemas"]["MarketReferenceLevel"][];
      opinion: components["schemas"]["ResearchOpinion"];
      prompt_version?: string;
      questions?: components["schemas"]["ResearchQuestion"][];
      risks?: components["schemas"]["ResearchFactor"][];
      scenarios: components["schemas"]["ResearchScenarioState"][];
      schema_version?: string;
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
    DecisionConfidence: "low" | "medium" | "high" | "indeterminate";
    DecisionNumericAuditAppendix: {
      omitted_components?: components["schemas"]["NumericAuditOmission"][];
      requirement_checks?: components["schemas"]["NumericRequirementCheck"][];
      snapshots: components["schemas"]["NumericAuditSnapshot"][];
      status: components["schemas"]["NumericAuditAppendixStatus"];
    };
    DecisionRole: "thesis" | "risk" | "catalyst" | "invalidation" | "scenario_assumption";
    DiscoveredModelView: {
      compatibility: "supported" | "unknown";
      default_roles: ("quick" | "deep")[];
      id: string;
      label: string;
      reasoning_efforts: string[];
    };
    EffectiveEvidenceSnapshot: {
      bundle: components["schemas"]["EvidenceBundle"];
      lineage: components["schemas"]["EvidenceSnapshotItem"][];
      schema_version?: string;
      source_record_lineage?: components["schemas"]["SourceRecordSnapshotItem"][];
      source_records?: components["schemas"]["SourceRecordVersion"][];
      source_watermarks?: components["schemas"]["SourceWatermarkSnapshot"][];
    };
    EpistemicKind: "observation" | "inference" | "forecast";
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
    EvidenceSnapshotItem: {
      evidence_ref: string;
      lineage: "new" | "inherited";
      source_revision_id?: string | null;
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
    IdentityDisposition: "exact_match" | "new" | "ambiguous_new" | "conservative_retirement";
    IndeterminateReason: "coverage_incomplete" | "question_disposition_limited";
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
      required_sources?: string[];
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
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      market: string | null;
      outcome: components["schemas"]["OutcomeObservationView"];
      outcome_feedback: components["schemas"]["OutcomeFeedbackView"] | null;
      outcome_id: number;
      outcome_reflection: components["schemas"]["OutcomeReflectionView"] | null;
      profile: components["schemas"]["RunProfile"];
      reflection: string | null;
      run_id: string;
      ticker: string;
    };
    ModelDiscoveryWarningView: {
      code: string;
      message: string;
    };
    NextUpdateReason: "indeterminate_head" | "coverage_incomplete" | "incompatible_market_semantics";
    NodeMetrics: {
      cache_hit_input_tokens?: number;
      cache_miss_input_tokens?: number;
      cost_usd?: number | null;
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
      candidate_omitted?: string | null;
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
    OutcomeFeedbackApplicabilityView: {
      horizon: string;
      instrument: string | null;
      market: string | null;
      method_category: string;
      research_domains: string[];
      research_stages: string[];
      schema_version: string;
      scope: "instrument" | "market";
    };
    OutcomeFeedbackRetireRequest: {
      reason: string;
    };
    OutcomeFeedbackStatus: "eligible" | "ineligible" | "retired";
    OutcomeFeedbackView: {
      applicability: components["schemas"]["OutcomeFeedbackApplicabilityView"];
      available_at: string;
      horizon_limit: string;
      id: number;
      method_category: string;
      qualified_at: string;
      reasons: string[];
      retired_at: string | null;
      status: components["schemas"]["OutcomeFeedbackStatus"];
    };
    OutcomeObservationStatus: "pending" | "resolved";
    OutcomeObservationView: {
      adjustment_semantics: string;
      alpha_return: number | null;
      benchmark: string;
      data_available_at: string | null;
      holding_intervals: number;
      horizon_limit: string;
      limitations: string[];
      market_timezone: string;
      method_category: string;
      method_version: string;
      observation_end: string | null;
      observation_start: string | null;
      price_semantics: string;
      raw_return: number | null;
      source_decision_id: number;
      source_revision_id: string | null;
      status: components["schemas"]["OutcomeObservationStatus"];
    };
    OutcomeReflectionStatus: "pending" | "generated" | "invalid" | "retryable_failure";
    OutcomeReflectionView: {
      error_code: string | null;
      generated_at: string | null;
      last_attempted_at: string | null;
      next_retry_at: string | null;
      status: components["schemas"]["OutcomeReflectionStatus"];
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
    QuestionChange: "introduced" | "reaffirmed" | "answered" | "reopened" | "superseded" | "retired";
    QuestionDispositionAudit: {
      dispositions?: components["schemas"]["QuestionDispositionRecord"][];
      language: string;
      limitation_reason?: components["schemas"]["QuestionDispositionLimitation"] | null;
      repair_attempted?: boolean;
      schema_version?: string;
      status: "complete" | "limited";
    };
    QuestionDispositionKind: "reaffirmed" | "answered" | "reopened" | "superseded" | "retired";
    QuestionDispositionLimitation: "question_disposition_output_invalid" | "question_disposition_evidence_invalid" | "question_disposition_ambiguous_identity" | "question_disposition_incomplete";
    QuestionDispositionRecord: {
      baseline_question_id: string;
      candidate_question_id?: string | null;
      disposition: components["schemas"]["QuestionDispositionKind"];
      evidence_refs: string[];
      reason: string;
      successor_question_id?: string | null;
    };
    QuestionRevisionDelta: {
      change: components["schemas"]["QuestionChange"];
      evidence_refs?: string[];
      identity_disposition: components["schemas"]["IdentityDisposition"];
      object_id: string;
      previous_object_id?: string | null;
      reason?: string | null;
      successor_object_id?: string | null;
    };
    QuestionStatus: "open" | "answered" | "superseded" | "retired";
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
      schema_version?: string;
      stage: string;
    };
    ResearchCase: {
      markdown: string;
      role: "bull" | "bear";
    };
    ResearchChain: {
      created_at: string;
      current_revision?: components["schemas"]["ResearchRevision"] | null;
      current_revision_id: string;
      id: string;
      instrument: string;
      is_primary: boolean;
      next_update_policy?: "incremental_allowed" | "full_required";
      next_update_reason?: components["schemas"]["NextUpdateReason"] | null;
      revisions?: components["schemas"]["ResearchRevision"][];
      updated_at: string;
    };
    ResearchChainUpdateRequest: {
      analysis_date: string;
      baseline_revision_id: string;
      execution_strategy?: "full" | "incremental" | null;
    };
    ResearchChangeConclusion: "material_change" | "no_material_change" | "indeterminate";
    ResearchChangeKind: "new_fundamental_filing" | "fundamental_correction" | "fundamental_restatement" | "accounting_scope_change" | "unclassifiable_fundamental_change" | "market_semantic_incompatibility" | "market_boundary_crossing" | "ordinary_market_move" | "unchanged_observation";
    ResearchChangeSignal: {
      boundary_label?: string | null;
      boundary_value?: number | null;
      current_value?: number | null;
      current_version_id?: string | null;
      detail: string;
      domain: "fundamentals" | "market";
      kind: components["schemas"]["ResearchChangeKind"];
      previous_value?: number | null;
      previous_version_id?: string | null;
      record_id: string;
      requires_full_analysis: boolean;
    };
    ResearchClaim: {
      confidence: components["schemas"]["ClaimConfidence"];
      decision_role: components["schemas"]["DecisionRole"];
      epistemic_kind: components["schemas"]["EpistemicKind"];
      evidence_refs: string[];
      evidence_relationship?: "direct" | "decision_envelope";
      falsifier?: string | null;
      id: string;
      observed_at?: string | null;
      required_sources?: string[];
      standing?: components["schemas"]["ClaimStanding"];
      statement: string;
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
      question_source_dependencies?: components["schemas"]["ResearchQuestionSourceDependency"][];
      rating: components["schemas"]["ResearchRating"];
      risk_review_adjustments?: components["schemas"]["RiskReviewAdjustment"][];
      risks: string[];
      scenarios: components["schemas"]["ResearchScenario"][];
      thesis: string;
      time_horizon: string;
      unresolved_questions?: string[];
      valuation_assessment?: components["schemas"]["ValuationAssessment"] | null;
    };
    ResearchDomainCoverage: {
      domain: string;
      evidence_refs?: string[];
      limitations?: string[];
      requirement?: components["schemas"]["CoverageRequirement"];
      source?: string | null;
      status: components["schemas"]["CoverageStatus"];
    };
    ResearchExecutionStrategy: "full" | "incremental";
    ResearchFactor: {
      claim_ids: string[];
      evidence_refs: string[];
      statement: string;
    };
    ResearchObjectCoverage: {
      evidence_refs?: string[];
      limitations?: string[];
      object_id: string;
      status: components["schemas"]["CoverageStatus"];
    };
    ResearchOpinion: {
      confidence: components["schemas"]["DecisionConfidence"];
      evidence_refs: string[];
      primary_claim_ids: string[];
      rating: components["schemas"]["ResearchRating"];
      thesis: string;
    };
    ResearchQuestion: {
      disposition_reason?: string | null;
      evidence_refs?: string[];
      id: string;
      last_disposition?: components["schemas"]["QuestionDispositionKind"] | null;
      question: string;
      required_sources?: string[];
      status?: components["schemas"]["QuestionStatus"];
      successor_question_id?: string | null;
    };
    ResearchQuestionSourceDependency: {
      question: string;
      required_sources: string[];
    };
    ResearchRating: "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell";
    ResearchRevision: {
      chain_id: string;
      change_conclusion?: components["schemas"]["ResearchChangeConclusion"] | null;
      coverage: components["schemas"]["CoverageAttestation"];
      created_at: string;
      current_state: components["schemas"]["CurrentResearchState"];
      cutoff: string;
      delta: components["schemas"]["RevisionDelta"];
      evidence_snapshot: components["schemas"]["EffectiveEvidenceSnapshot"];
      execution_strategy: components["schemas"]["ResearchExecutionStrategy"];
      id: string;
      indeterminate_reason?: components["schemas"]["IndeterminateReason"] | null;
      metrics?: components["schemas"]["RunMetrics"];
      predecessor_revision_id?: string | null;
      producing_run_id?: string | null;
      research_update_audit?: components["schemas"]["ResearchUpdateAudit"] | null;
      role: components["schemas"]["ResearchRevisionRole"];
      sequence: number;
      update_summary: components["schemas"]["UpdateSummary"];
    };
    ResearchRevisionRole: "initial" | "update";
    ResearchScenario: {
      core_assumptions: string[];
      evidence_refs?: string[];
      kind: components["schemas"]["ResearchScenarioKind"];
      outcome: string;
      reference_ranges?: components["schemas"]["ScenarioReferenceRange"][];
    };
    ResearchScenarioKind: "base" | "bull" | "bear";
    ResearchScenarioState: {
      assumption_claim_ids: string[];
      cutoff: string;
      evidence_refs: string[];
      horizon: string;
      kind: components["schemas"]["ResearchScenarioKind"];
      likelihood: components["schemas"]["ScenarioLikelihood"];
      outcome: string;
    };
    ResearchUpdateAudit: {
      authoritative_strategy?: "full" | "incremental";
      bounded_metrics?: components["schemas"]["RunMetrics"];
      candidate?: components["schemas"]["ResearchUpdateCandidate"] | null;
      checked_windows?: components["schemas"]["ResearchUpdateCheckedWindow"][];
      comparison: "agreement" | "disagreement" | "inconclusive" | "not_applicable";
      coverage?: components["schemas"]["ResearchUpdateCoverageAttestation"] | null;
      escalation_reason?: "invalid_baseline" | "source_correction" | "source_withdrawal" | "source_replacement" | "source_version_change" | "incompatible_semantics" | "threshold_crossing" | "coverage_incomplete" | "schema_invalid" | "semantic_weakening" | "semantic_contradiction" | "semantic_answering" | "semantic_reopening" | "semantic_uncertainty" | "potentially_material_novelty" | "confidence_change" | "ambiguous_identity" | "semantic_output_invalid" | "semantic_input_oversize" | null;
      evidence_lineage?: components["schemas"]["ResearchUpdateEvidenceLineage"][];
      full_metrics?: components["schemas"]["RunMetrics"];
      mode?: "shadow" | "experimental";
      schema_version?: string;
      semantic_assessment?: components["schemas"]["ResearchUpdateSemanticAssessment"] | null;
    };
    ResearchUpdateCandidate: {
      change_conclusion: string;
      coverage: components["schemas"]["ResearchUpdateCoverageAttestation"];
      evidence_snapshot: components["schemas"]["ResearchUpdateEvidenceSnapshot"];
      schema_version?: string;
      update_summary: components["schemas"]["ResearchUpdateSummaryContract"];
    };
    ResearchUpdateCheckedWindow: {
      baseline_cutoff?: string | null;
      limitations?: string[];
      overlap_start?: string | null;
      reported_records?: number | null;
      returned_records?: number;
      scanned_end: string;
      scanned_start: string;
      source: string;
      status: "complete" | "limited" | "unavailable";
      temporal_scope?: "point_in_time" | "live_only" | "unknown";
    };
    ResearchUpdateCoverageAttestation: {
      claims: components["schemas"]["ResearchUpdateObjectCoverage"][];
      domains: components["schemas"]["ResearchUpdateDomainCoverage"][];
      limitations?: string[];
      questions: components["schemas"]["ResearchUpdateObjectCoverage"][];
      schema_version?: string;
      supports_no_material_change: boolean;
    };
    ResearchUpdateDomainCoverage: {
      domain: string;
      evidence_refs?: string[];
      limitations?: string[];
      requirement?: "required" | "advisory";
      source?: string | null;
      status: "complete" | "limited" | "unavailable";
    };
    ResearchUpdateEvidenceLineage: {
      evidence_ref: string;
      lineage: "new" | "inherited";
      source_revision_id?: string | null;
    };
    ResearchUpdateEvidenceSnapshot: {
      bundle: components["schemas"]["EvidenceBundle"];
      lineage: components["schemas"]["ResearchUpdateEvidenceSnapshotItem"][];
      schema_version?: string;
      source_record_lineage?: components["schemas"]["ResearchUpdateSourceRecordSnapshotItem"][];
      source_records?: components["schemas"]["ResearchUpdateSourceRecordVersion"][];
      source_watermarks?: components["schemas"]["ResearchUpdateSourceWatermarkSnapshot"][];
    };
    ResearchUpdateEvidenceSnapshotItem: {
      evidence_ref: string;
      lineage: "new" | "inherited";
      source_revision_id?: string | null;
    };
    ResearchUpdateObjectCoverage: {
      evidence_refs?: string[];
      limitations?: string[];
      object_id: string;
      status: "complete" | "limited" | "unavailable";
    };
    ResearchUpdateSemanticAssessment: {
      language: string;
      relationships: components["schemas"]["ResearchUpdateSemanticRelationship"][];
      schema_version?: string;
      summary: string;
    };
    ResearchUpdateSemanticRelationship: {
      evidence_refs: string[];
      relationship: "support" | "weakening" | "contradiction" | "answering" | "reopening" | "irrelevance" | "uncertainty" | "potentially_material_novelty";
      suggested_claim_confidence?: "low" | "medium" | "high" | "indeterminate" | null;
      suggested_claim_ids?: string[];
      suggested_question_ids?: string[];
    };
    ResearchUpdateSourceRecordSnapshotItem: {
      lineage: "new" | "inherited";
      observed_in_execution: boolean;
      source_revision_id?: string | null;
      version_id: string;
    };
    ResearchUpdateSourceRecordVersion: {
      accounting_scope?: string | null;
      adjustment?: string | null;
      availability_basis?: string | null;
      available_at: string;
      change_hint?: "new_filing" | "correction" | "restatement" | "accounting_scope_change" | "unclassifiable" | null;
      comparison_key?: string | null;
      evidence_ref: string;
      fallback?: boolean;
      native_record_id?: string | null;
      observation_value?: number | null;
      precision?: number | null;
      published_at: string;
      record_id: string;
      record_kind?: "disclosure" | "fundamental" | "market";
      replaces_version_id?: string | null;
      source: string;
      status: "published" | "corrected" | "withdrawn" | "replaced";
      title: string;
      unit?: string | null;
      url?: string | null;
      version_id: string;
    };
    ResearchUpdateSourceWatermarkSnapshot: {
      baseline_cutoff?: string | null;
      limitations?: string[];
      overlap_start?: string | null;
      reported_records?: number | null;
      returned_records?: number;
      scanned_end: string;
      scanned_start: string;
      source: string;
      status: "complete" | "limited" | "unavailable";
      temporal_scope?: "point_in_time" | "live_only" | "unknown";
    };
    ResearchUpdateSummaryContract: {
      analysis_cutoff?: string | null;
      baseline_cutoff?: string | null;
      change_conclusion?: "material_change" | "no_material_change" | "indeterminate" | null;
      checked_domains: string[];
      execution_strategy?: "full" | "incremental" | null;
      language: string;
      limitations?: string[];
      new_evidence_refs?: string[];
      schema_version?: string;
      summary: string;
    };
    ResearchWarning: {
      code?: string;
      evidence_ref?: string | null;
      message: string;
      source?: string | null;
    };
    RevisionDelta: {
      change_signals?: components["schemas"]["ResearchChangeSignal"][];
      changed_sections?: ("opinion" | "claims" | "questions" | "scenarios" | "risks" | "catalysts" | "invalidation_conditions")[];
      claims: components["schemas"]["ClaimRevisionDelta"][];
      inherited_evidence_refs?: string[];
      new_evidence_refs?: string[];
      opinion_changed: boolean;
      question_disposition?: components["schemas"]["QuestionDispositionAudit"] | null;
      questions: components["schemas"]["QuestionRevisionDelta"][];
      schema_version?: string;
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
      cost_usd?: number | null;
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
    RunStatus: "queued" | "running" | "succeeded" | "failed" | "cancelled";
    RunSummaryView: {
      attempt: number;
      baseline_revision_id?: string | null;
      cancel_requested: boolean;
      config_snapshot: Record<string, unknown>;
      created_at: string;
      error_code?: string | null;
      error_message?: string | null;
      finished_at?: string | null;
      id: string;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      metrics?: components["schemas"]["RunMetrics"];
      request: components["schemas"]["AnalysisRequest"];
      research_chain_id?: string | null;
      research_chain_requested?: boolean;
      research_execution_strategy?: "full" | "incremental" | null;
      research_rating?: components["schemas"]["ResearchRating"] | null;
      research_update_audit?: components["schemas"]["ResearchUpdateAudit"] | null;
      source_run_id?: string | null;
      started_at?: string | null;
      status: components["schemas"]["RunStatus"];
      trashed_at?: string | null;
      update_intent_id?: string | null;
      updated_at: string;
    };
    RunTrashState: "active" | "trashed" | "all";
    RunView: {
      attempt: number;
      baseline_revision_id?: string | null;
      cancel_requested: boolean;
      config_snapshot: Record<string, unknown>;
      created_at: string;
      error_code?: string | null;
      error_message?: string | null;
      finished_at?: string | null;
      id: string;
      instrument_local_name?: string | null;
      instrument_name?: string | null;
      metrics?: components["schemas"]["RunMetrics"];
      request: components["schemas"]["AnalysisRequest"];
      research_chain_id?: string | null;
      research_chain_requested?: boolean;
      research_execution_strategy?: "full" | "incremental" | null;
      research_update_audit?: components["schemas"]["ResearchUpdateAudit"] | null;
      source_run_id?: string | null;
      started_at?: string | null;
      status: components["schemas"]["RunStatus"];
      trashed_at?: string | null;
      update_intent_id?: string | null;
      updated_at: string;
    };
    ScenarioLikelihood: "low" | "medium" | "high" | "indeterminate";
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
    SourceRecordKind: "disclosure" | "fundamental" | "market";
    SourceRecordSnapshotItem: {
      lineage: "new" | "inherited";
      observed_in_execution: boolean;
      source_revision_id?: string | null;
      version_id: string;
    };
    SourceRecordStatus: "published" | "corrected" | "withdrawn" | "replaced";
    SourceRecordVersion: {
      accounting_scope?: string | null;
      adjustment?: string | null;
      availability_basis?: string | null;
      available_at: string;
      change_hint?: "new_filing" | "correction" | "restatement" | "accounting_scope_change" | "unclassifiable" | null;
      comparison_key?: string | null;
      evidence_ref: string;
      fallback?: boolean;
      native_record_id?: string | null;
      observation_value?: number | null;
      precision?: number | null;
      published_at: string;
      record_id: string;
      record_kind?: components["schemas"]["SourceRecordKind"];
      replaces_version_id?: string | null;
      source: string;
      status: components["schemas"]["SourceRecordStatus"];
      title: string;
      unit?: string | null;
      url?: string | null;
      version_id: string;
    };
    SourceWatermarkSnapshot: {
      baseline_cutoff?: string | null;
      limitations?: string[];
      overlap_start?: string | null;
      reported_records?: number | null;
      returned_records?: number;
      scanned_end: string;
      scanned_start: string;
      source: string;
      status: components["schemas"]["CoverageStatus"];
      temporal_scope?: "point_in_time" | "live_only" | "unknown";
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
    UpdateSummary: {
      analysis_cutoff?: string | null;
      baseline_cutoff?: string | null;
      change_conclusion?: components["schemas"]["ResearchChangeConclusion"] | null;
      checked_domains: string[];
      execution_strategy?: components["schemas"]["ResearchExecutionStrategy"] | null;
      language: string;
      limitations?: string[];
      new_evidence_refs?: string[];
      schema_version?: string;
      summary: string;
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
