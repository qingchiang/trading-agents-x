import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type ResearchTimelinePage, type TimelineDetail } from "../api/client";
import { Link, usePathname } from "../router";

export default function Timeline() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const isList = pathname === "/timelines";
  const instrument = decodeURIComponent(pathname.split("/").at(-1) ?? "");
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [timelines, setTimelines] = useState<ResearchTimelinePage | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const onError = (cause: unknown) =>
      active &&
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    if (isList) {
      void api.timelines().then(
        (value) => active && setTimelines(value),
        onError,
      );
    } else {
      void api.timeline(instrument).then(
        (value) => active && setDetail(value),
        onError,
      );
    }
    return () => {
      active = false;
    };
  }, [instrument, isList, t]);

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
        {timelines?.items?.map((timeline) => (
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
      {detail?.timeline.nodes?.map((node) => (
        <article className="panel" key={node.id}>
          <div className="panel-header">
            <div>
              <p className="eyebrow">{t("fullResearchNode")}</p>
              <h2>{node.analysis_date}</h2>
            </div>
            {node.is_primary && <strong>{t("primaryCycle")}</strong>}
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
          <Link className="text-link" to={`/runs/${node.id}`}>
            {t("openOperationalRun")} →
          </Link>
        </article>
      ))}
    </section>
  );
}
