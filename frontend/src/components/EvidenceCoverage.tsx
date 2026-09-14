import { useTranslation } from "react-i18next";
import type { ResearchNodeView } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import { Link, useLocation } from "../router";

/** Describe recorded collection boundaries without inferring source completeness. */
export default function EvidenceCoverage({ node, evidenceIndex, onDomain }: {
  node: ResearchNodeView; evidenceIndex: EvidenceReferenceIndex;
  onDomain: (domain: string, refs: string[]) => void;
}) {
  const { t } = useTranslation();
  const location = useLocation();
  const trashState = new URLSearchParams(location.search).get("trash_state");
  const collection = node.collection_summary?.domains ?? [];
  const availability = node.research_availability?.domains ?? [];
  const domains = [...new Set([...collection.map(item => item.domain), ...availability.map(item => item.domain)])];
  return <section className="evidence-coverage" id="evidence-coverage" data-outline={t("materialsAndLimits")}>
    <h2>{t("materialsAndLimits")}</h2>
    <p>{t("currentUpdateMaterials")}</p>
    {node.full_baseline_run_id && <Link to={`/timelines/${encodeURIComponent(node.instrument)}?node=${encodeURIComponent(node.full_baseline_run_id)}&view=evidence${trashState ? `&trash_state=${encodeURIComponent(trashState)}` : ""}`}>{t("openBaselineEvidence")}</Link>}
    {!domains.length && <p>{t("coverageNotRecorded")}</p>}
    <div className="coverage-domains">{domains.map(domain => {
      const item = collection.find(value => value.domain === domain);
      const status = availability.find(value => value.domain === domain)?.status;
      const refs = item?.evidence_refs ?? [];
      const sources = [...new Set(refs.flatMap(ref => evidenceIndex.groups.find(group => group.refs.includes(ref))?.sources ?? []))];
      const codes = [...new Set([item?.diagnostic?.code, item?.omitted_by_temporal_boundary ? "inadmissible_market_rows_omitted" : undefined].filter((code): code is string => !!code))];
      return <section className="coverage-domain" key={domain}>
        <header><h3>{t(`${domain}Analyst`)}</h3><span className={`availability-chip ${status ?? "missing"}`}>{status ? t(`availability_${status}`) : t("notRecorded")}</span></header>
        <p className="coverage-time">{t("temporalBasis")}: {item?.temporal_bases?.length ? item.temporal_bases.map(basis => t(`coverageTime_${basis}`)).join(" · ") : t("notRecorded")}</p>
        {codes.map(code => <p className="coverage-limitation" key={code}>{t(`coverageReason_${code}`, { defaultValue: code })}</p>)}
        {!item ? <p>{t("coverageNotRecorded")}</p> : item.state === "empty" ? <p>{t("noNewMaterialsCollected")}</p> : !codes.length && <p>{t(`collection_${item.state}`)}</p>}
        {!!sources.length && <p className="secondary-line">{t("source")}: {sources.join(" · ")}</p>}
        {!!refs.length && <button type="button" className="text-button" onClick={() => onDomain(domain, refs)}>{t("showDomainEvidence")}</button>}
      </section>;
    })}</div>
  </section>;
}
