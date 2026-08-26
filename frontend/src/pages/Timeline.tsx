import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type ResearchTimelinePage, type TimelineDetail } from "../api/client";
import type { components } from "../api/types.generated";
import ConfirmDialog from "../components/ConfirmDialog";
import { Link, usePathname } from "../router";

const NODE_PAGE_SIZE = 20;
type PerformanceCalculation = components["schemas"]["PerformanceCalculationRecord"];
type ResearchNode = components["schemas"]["ResearchNodeView"];

function PerformanceCalculationAudit({
  calculation,
}: {
  calculation: PerformanceCalculation;
}) {
  return (
    <dl className="definition-list">
      <div><dt>Provider</dt><dd>{calculation.provider}</dd></div>
      <div><dt>Fallback</dt><dd>{calculation.fallback ? "yes" : "no"}</dd></div>
      <div><dt>Adjustment basis</dt><dd>{calculation.adjustment_basis}</dd></div>
      <div><dt>Retrieved at</dt><dd>{calculation.retrieved_at}</dd></div>
      <div>
        <dt>Information cutoffs</dt>
        <dd>{calculation.baseline_information_cutoff_at} → {calculation.target_information_cutoff_at}</dd>
      </div>
      <div><dt>Endpoint sessions</dt><dd>{calculation.start_session} → {calculation.end_session}</dd></div>
      <div><dt>Endpoint values</dt><dd>{calculation.start_value} → {calculation.end_value}</dd></div>
      <div><dt>Formula</dt><dd>{calculation.formula}</dd></div>
      <div><dt>Unrounded return</dt><dd>{calculation.unrounded_return}</dd></div>
    </dl>
  );
}

export default function Timeline() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const isList = pathname === "/timelines";
  const instrument = decodeURIComponent(pathname.split("/").at(-1) ?? "");
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [timelines, setTimelines] = useState<ResearchTimelinePage | null>(null);
  const [timelineOffset, setTimelineOffset] = useState(0);
  const [nodeOffset, setNodeOffset] = useState(0);
  const [showRetainedTrash, setShowRetainedTrash] = useState(false);
  const [pendingNode, setPendingNode] = useState<ResearchNode | null>(null);
  const [lifecycleMode, setLifecycleMode] = useState<"trash" | "purge" | null>(null);
  const [replacementPrimary, setReplacementPrimary] = useState("");
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const timelineItems = timelines?.items ?? [];
  const detailNodes = detail?.timeline.nodes ?? [];
  const nodeTotal = detail?.timeline.node_total ?? 0;
  const nodeLimit = detail?.timeline.node_limit ?? NODE_PAGE_SIZE;
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const onError = (cause: unknown) =>
      active &&
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    if (isList) {
      void api.timelines(50, timelineOffset).then(
        (value) => active && setTimelines(value),
        onError,
      );
    } else {
      const request = showRetainedTrash
        ? api.timeline(instrument, NODE_PAGE_SIZE, nodeOffset, "all")
        : api.timeline(instrument, NODE_PAGE_SIZE, nodeOffset);
      void request.then(
        (value) => active && setDetail(value),
        onError,
      );
    }
    return () => {
      active = false;
    };
  }, [instrument, isList, nodeOffset, showRetainedTrash, t, timelineOffset]);

  const reloadDetail = async () => {
    const value = showRetainedTrash
      ? await api.timeline(instrument, NODE_PAGE_SIZE, nodeOffset, "all")
      : await api.timeline(instrument, NODE_PAGE_SIZE, nodeOffset);
    setDetail(value);
  };

  const applyLifecycle = async () => {
    if (!pendingNode || !lifecycleMode) return;
    const replacements =
      pendingNode.research_kind === "full" && pendingNode.is_primary && replacementPrimary
        ? { [pendingNode.id]: replacementPrimary }
        : {};
    if (
      lifecycleMode === "trash" &&
      pendingNode.research_kind === "full" &&
      pendingNode.is_primary &&
      detailNodes.some((node) =>
        node.research_kind === "full" && node.is_active && node.id !== pendingNode.id
      ) &&
      !replacementPrimary
    ) {
      setError(t("selectReplacementCycle"));
      return;
    }
    setLifecycleBusy(true);
    setError("");
    try {
      if (lifecycleMode === "trash") {
        await api.trashRuns([pendingNode.id], replacements);
      } else {
        await api.purgeRuns([pendingNode.id]);
      }
      setPendingNode(null);
      setLifecycleMode(null);
      setReplacementPrimary("");
      await reloadDetail();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    } finally {
      setLifecycleBusy(false);
    }
  };

  if (isList) {
    return (
      <section>
        <header className="page-header">
          <div>
            <p className="eyebrow">{t("researchTimeline")}</p>
            <h1>{t("researchTimelines")}</h1>
            <p className="subtitle">{t("researchTimelinesHint")}</p>
          </div>
          <Link className="button" to="/runs">
            {t("executionHistory")}
          </Link>
        </header>
        {error && <div className="alert">{error}</div>}
        {!timelines && !error && <div className="loading">{t("loading")}</div>}
        {timelines && (timelines.items?.length ?? 0) === 0 && (
          <div className="empty-state">{t("noResearchTimelines")}</div>
        )}
        {timelineItems.map((timeline) => (
          <article className="panel" key={timeline.instrument}>
            <div className="panel-header">
              <div>
                <p className="eyebrow">{t("researchTimeline")}</p>
                <h2>{timeline.instrument}</h2>
              </div>
              <strong>{t("researchNodeCount", { count: timeline.node_count })}</strong>
            </div>
            <Link className="text-link" to={`/timelines/${encodeURIComponent(timeline.instrument)}`}>
              {timeline.instrument}
            </Link>
          </article>
        ))}
        {timelines && timelines.total > (timelines.limit ?? 50) && (
          <div className="pagination">
            <button
              type="button"
              className="button"
              disabled={timelineOffset === 0}
              onClick={() =>
                setTimelineOffset((current) => Math.max(0, current - (timelines.limit ?? 50)))
              }
            >
              ← {t("previous")}
            </button>
            <span>
              {t("runRange", {
                start: timelineOffset + 1,
                end: Math.min(timelineOffset + timelineItems.length, timelines.total),
                total: timelines.total,
              })}
            </span>
            <button
              type="button"
              className="button"
              disabled={timelineOffset + timelineItems.length >= timelines.total}
              onClick={() => setTimelineOffset((current) => current + (timelines.limit ?? 50))}
            >
              {t("next")} →
            </button>
          </div>
        )}
      </section>
    );
  }

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("researchTimeline")}</p>
          <h1>{instrument}</h1>
          <p className="subtitle">
            {t("researchTimelineHint")}
          </p>
        </div>
        <Link className="button" to="/runs">
          {t("executionHistory")}
        </Link>
        <button
          type="button"
          className="button"
          onClick={() => {
            setNodeOffset(0);
            setShowRetainedTrash((current) => !current);
          }}
        >
          {t(showRetainedTrash ? "hideRetainedTrash" : "showRetainedTrash")}
        </button>
      </header>
      {error && <div className="alert">{error}</div>}
      {!detail && !error && <div className="loading">{t("loading")}</div>}
      {detail && (detail.timeline.nodes?.length ?? 0) === 0 && (
        <div className="empty-state">{t("noCommittedFullResearch")}</div>
      )}
      {detail?.timeline.timeline_warning && (
        <div className="alert">{t("fullResearchRecommended")}</div>
      )}
      {detailNodes.map((node) => (
        <article className="panel" key={node.id}>
          <div className="panel-header">
            <div>
              <p className="eyebrow">{t(node.research_kind === "full" ? "fullResearchNode" : "incrementalResearchNode")}</p>
              <h2>{node.analysis_date}</h2>
              {node.research_kind === "full" && <span>{t("fullBaseline")}</span>}
            </div>
            <div>
              {node.is_cycle_head && <strong>{t("cycleHead")}</strong>}
              {node.is_primary && <strong>{t("primaryCycle")}</strong>}
              {!node.is_active && <strong>{t("retainedInTrash")}</strong>}
            </div>
          </div>
          <dl className="definition-list">
            <div>
              <dt>{t("cycleId")}</dt>
              <dd>{node.cycle_id}</dd>
            </div>
            <div>
              <dt>{t("researchSchema")}</dt>
              <dd>{node.research_schema_version}</dd>
            </div>
            <div>
              <dt>{t("informationCutoff")}</dt>
              <dd>{new Date(node.information_cutoff_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>{t("methodProvider")}</dt>
              <dd>
                {String(node.method_snapshot.llm_provider ?? t("notRecorded"))}
              </dd>
            </div>
          </dl>
          {node.collection_summary && (
            <details>
              <summary>Collection Summary</summary>
              <ul>
                {node.collection_summary.domains.map((result) => (
                  <li key={result.domain}>
                    {result.domain}: {result.state}
                    {result.diagnostic ? ` [${result.diagnostic.code}]` : ""}
                    {result.omitted_by_temporal_boundary
                      ? " [outside_temporal_boundary]"
                      : ""}
                    {(result.sources?.length ?? 0) > 0 && (
                      <ul>
                        {(result.sources ?? []).map((source) => (
                          <li key={source.source}>
                            {source.source}{source.fallback ? " (fallback)" : ""}
                            {source.diagnostic ? ` [${source.diagnostic.code}]` : ""}
                            <dl className="definition-list">
                              <div><dt>Retrieved at</dt><dd>{source.retrieved_at}</dd></div>
                            </dl>
                          </li>
                        ))}
                      </ul>
                    )}
                    {result.evidence_refs?.length ? (
                      <>
                        {" · "}
                        {result.evidence_refs.map((ref, index) => (
                          <span key={ref}>
                            {index > 0 ? ", " : ""}
                            <code>{ref}</code>
                          </span>
                        ))}
                      </>
                    ) : null}
                    <dl className="definition-list">
                      {result.observed_from && result.observed_through && (
                        <div>
                          <dt>Observed window</dt>
                          <dd>{result.observed_from} → {result.observed_through}</dd>
                        </div>
                      )}
                      {(result.temporal_bases?.length ?? 0) > 0 && (
                        <div>
                          <dt>Temporal basis</dt>
                          <dd>{result.temporal_bases?.join(", ")}</dd>
                        </div>
                      )}
                    </dl>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {node.research_availability && (
            <details>
              <summary>Research Availability</summary>
              <ul>
                {node.research_availability.domains.map((domain) => (
                  <li key={domain.domain}>
                    {domain.domain}: {domain.status}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {node.information_advancement && (
            <details>
              <summary>Information Advancement</summary>
              <ul>
                {(node.information_advancement.reasons ?? []).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </details>
          )}
          {node.reassessment && (
            <details>
              <summary>Reassessment</summary>
              <ul>
                {node.reassessment.entries.map((entry) => (
                  <li key={entry.component_id}>
                    {entry.component_id}: {entry.disposition} — {entry.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {node.decision && (
            <section aria-label="Current Decision">
              <h3>Current Decision</h3>
              <p>{node.decision.rating} — {node.decision.thesis}</p>
            </section>
          )}
          {node.performance && (
            <details>
              <summary>Performance</summary>
              <ul>
                <li>
                  Stock: {node.performance.stock.status}
                  {node.performance.stock.reason
                    ? ` — ${node.performance.stock.reason}`
                    : node.performance.stock.calculation
                      ? ` — ${node.performance.stock.calculation.start_session} to ${node.performance.stock.calculation.end_session}: ${node.performance.stock.calculation.unrounded_return}`
                      : ""}
                  {node.performance.stock.calculation && (
                    <PerformanceCalculationAudit calculation={node.performance.stock.calculation} />
                  )}
                </li>
                {(node.performance.benchmarks ?? []).map((benchmark) => (
                  <li key={benchmark.name}>
                    {benchmark.name}: {benchmark.component.status}
                    {benchmark.component.reason
                      ? ` — ${benchmark.component.reason}`
                      : benchmark.component.calculation
                        ? ` — ${benchmark.component.calculation.unrounded_return}`
                        : ""}
                    {benchmark.component.calculation && (
                      <PerformanceCalculationAudit calculation={benchmark.component.calculation} />
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {node.cycle_warning && <p className="alert">Cycle warning</p>}
          {node.full_research_required_reasons?.map((reason) => (
            <p className="alert" key={reason.code}>{reason.message}</p>
          ))}
          {!node.is_primary && node.is_active && node.research_kind === "full" && (
            <button
              type="button"
              className="button"
              onClick={() => {
                setError("");
                void api.selectPrimaryCycle(instrument, node.id).then(
                  (value) => {
                    setDetail(value);
                    setNodeOffset(value.timeline.node_offset ?? 0);
                  },
                  (cause: unknown) =>
                    setError(cause instanceof Error ? cause.message : t("timelineLoadFailed")),
                );
              }}
            >
              {t("makePrimary")}
            </button>
          )}
          {node.is_active ? (
            <button
              type="button"
              className="button danger"
              onClick={() => {
                setPendingNode(node);
                setLifecycleMode("trash");
                setReplacementPrimary("");
              }}
            >
              {t(node.research_kind === "full" ? "moveCycleToTrash" : "moveNodeToTrash")}
            </button>
          ) : (
            <>
              <button
                type="button"
                className="button"
                onClick={() => {
                  setError("");
                  void api.restoreRuns([node.id]).then(reloadDetail, (cause: unknown) =>
                    setError(cause instanceof Error ? cause.message : t("timelineLoadFailed")),
                  );
                }}
              >
                {t("restoreResearchNode")}
              </button>
              <button
                type="button"
                className="button danger"
                onClick={() => {
                  setPendingNode(node);
                  setLifecycleMode("purge");
                }}
              >
                {t("purgeResearchNode")}
              </button>
            </>
          )}
          <Link className="text-link" to={`/runs/${node.id}`}>
            {t("openOperationalRun")} →
          </Link>
        </article>
      ))}
      {detail && nodeTotal > nodeLimit && (
        <div className="pagination">
          <button
            type="button"
            className="button"
            disabled={nodeOffset === 0}
            onClick={() =>
              setNodeOffset((current) =>
                Math.max(0, current - nodeLimit),
              )
            }
          >
            ← {t("previous")}
          </button>
          <span>
            {t("runRange", {
              start: nodeTotal ? nodeOffset + 1 : 0,
              end: Math.min(
                nodeOffset + detailNodes.length,
                nodeTotal,
              ),
              total: nodeTotal,
            })}
          </span>
          <button
            type="button"
            className="button"
            disabled={nodeOffset + detailNodes.length >= nodeTotal}
            onClick={() => setNodeOffset((current) => current + nodeLimit)}
          >
            {t("next")} →
          </button>
        </div>
      )}
      {pendingNode && lifecycleMode && (
        <ConfirmDialog
          title={t(
            lifecycleMode === "purge"
              ? "purgeResearchTitle"
              : pendingNode.research_kind === "full"
                ? "cycleTrashTitle"
                : "nodeTrashTitle",
          )}
          confirmLabel={t(
            lifecycleMode === "purge" ? "confirmPurge" : "confirmTimelineTrash",
          )}
          cancelLabel={t("cancel")}
          busy={lifecycleBusy}
          onCancel={() => {
            setPendingNode(null);
            setLifecycleMode(null);
          }}
          onConfirm={() => void applyLifecycle()}
        >
          <p>
            {t(
              lifecycleMode === "purge"
                ? "purgeResearchImpact"
                : pendingNode.research_kind === "full"
                  ? "fullOwnsCycle"
                  : "incrementalTrashImpact",
            )}
          </p>
          {lifecycleMode === "trash" &&
            pendingNode.research_kind === "full" &&
            pendingNode.is_primary &&
            detailNodes.some((node) =>
              node.research_kind === "full" && node.is_active && node.id !== pendingNode.id
            ) && (
              <label>
                {t("replacementPrimaryCycle")}
                <select
                  value={replacementPrimary}
                  onChange={(event) => setReplacementPrimary(event.target.value)}
                >
                  <option value="">{t("selectReplacementCycle")}</option>
                  {detailNodes
                    .filter((node) =>
                      node.research_kind === "full" &&
                      node.is_active &&
                      node.id !== pendingNode.id
                    )
                    .map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.analysis_date} · {node.id}
                      </option>
                    ))}
                </select>
              </label>
            )}
        </ConfirmDialog>
      )}
    </section>
  );
}
