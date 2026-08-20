import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type ResearchTimelinePage, type TimelineDetail } from "../api/client";
import { Link, usePathname } from "../router";

const NODE_PAGE_SIZE = 20;

export default function Timeline() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const isList = pathname === "/timelines";
  const instrument = decodeURIComponent(pathname.split("/").at(-1) ?? "");
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [timelines, setTimelines] = useState<ResearchTimelinePage | null>(null);
  const [timelineOffset, setTimelineOffset] = useState(0);
  const [nodeOffset, setNodeOffset] = useState(0);
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
      void api.timeline(instrument, NODE_PAGE_SIZE, nodeOffset).then(
        (value) => active && setDetail(value),
        onError,
      );
    }
    return () => {
      active = false;
    };
  }, [instrument, isList, nodeOffset, t, timelineOffset]);

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
          {node.collection_manifest && (
            <details>
              <summary>Incremental Manifest</summary>
              <ul>
                {node.collection_manifest.entries.map((entry) => (
                  <li key={`${entry.domain}:${entry.source}`}>
                    {entry.domain} / {entry.source}: {entry.outcome}
                    {entry.source_watermark ? ` (${entry.source_watermark})` : ""}
                    {entry.evidence_refs?.length ? (
                      <>
                        {" · "}
                        {entry.evidence_refs.map((ref, index) => (
                          <span key={ref}>
                            {index > 0 ? ", " : ""}
                            <code>{ref}</code>
                          </span>
                        ))}
                      </>
                    ) : null}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {node.research_coverage && (
            <details>
              <summary>Coverage</summary>
              <ul>
                {node.research_coverage.domains.map((domain) => (
                  <li key={domain.domain}>
                    {domain.domain}: {domain.requirement} / {domain.status}
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
            <p>Performance: {node.performance.status} — {node.performance.reason}</p>
          )}
          {node.outcome_review_status === "omitted" && <p>{t("outcomeReviewOmitted")}</p>}
          {node.outcome_review_status === "failed" && <p>Outcome review failed</p>}
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
    </section>
  );
}
