import { useEffect, useState } from "react";
import { api, type TimelineDetail } from "../api/client";
import { Link, usePathname } from "../router";

export default function Timeline() {
  const pathname = usePathname();
  const instrument = decodeURIComponent(pathname.split("/").at(-1) ?? "");
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void api.timeline(instrument).then(
      (value) => active && setDetail(value),
      (cause) =>
        active &&
        setError(
          cause instanceof Error ? cause.message : "Unable to load timeline.",
        ),
    );
    return () => {
      active = false;
    };
  }, [instrument]);

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">Research Timeline</p>
          <h1>{instrument}</h1>
          <p className="subtitle">
            Run-backed research cycles. Execution History remains available from
            each run.
          </p>
        </div>
        <Link className="button" to="/runs">
          Execution History
        </Link>
      </header>
      {error && <div className="alert">{error}</div>}
      {!detail && !error && <div className="loading">Loading</div>}
      {detail && (detail.timeline.nodes?.length ?? 0) === 0 && (
        <div className="empty-state">No committed Full Research yet.</div>
      )}
      {detail?.timeline.nodes?.map((node) => (
        <article className="panel" key={node.id}>
          <div className="panel-header">
            <div>
              <p className="eyebrow">Full Research Node</p>
              <h2>{node.analysis_date}</h2>
            </div>
            {node.is_primary && <strong>Primary Cycle</strong>}
          </div>
          <dl className="definition-list">
            <div>
              <dt>Cycle ID</dt>
              <dd>{node.cycle_id}</dd>
            </div>
            <div>
              <dt>Research schema</dt>
              <dd>{node.research_schema_version}</dd>
            </div>
            <div>
              <dt>Information cutoff</dt>
              <dd>{new Date(node.information_cutoff_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Method provider</dt>
              <dd>
                {String(node.method_snapshot.llm_provider ?? "not recorded")}
              </dd>
            </div>
          </dl>
          <Link className="text-link" to={`/runs/${node.id}`}>
            Open operational Run →
          </Link>
        </article>
      ))}
    </section>
  );
}
