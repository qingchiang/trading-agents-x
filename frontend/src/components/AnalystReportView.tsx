import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useTranslation } from "react-i18next";

import type { AnalystReport } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import FloatingSectionNavigation from "./FloatingSectionNavigation";
import Markdown from "./Markdown";

export default function AnalystReportView({
  report,
  runId,
  reportKey,
  evidenceIndex,
  onEvidence,
}: {
  report: AnalystReport | string;
  runId: string;
  reportKey: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  if (typeof report === "string") {
    return (
      <LegacyMarkdownReader
        markdown={report}
        runId={runId}
        reportKey={reportKey}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
    );
  }
  const claims = report.key_claims ?? [];
  const sections = report.report_sections ?? [];

  return (
    <ResearchMarkdownReader
      markdown={report.markdown}
      sections={sections}
      runId={runId}
      reportKey={reportKey}
      evidenceIndex={evidenceIndex}
      onEvidence={onEvidence}
      before={
        <>
          <div className="report-audit-summary">
            {report.confidence !== null && report.confidence !== undefined && (
              <span>
                {t("confidence")} {Math.round(report.confidence * 100)}%
              </span>
            )}
            <span>
              {t("keyClaimsCount", { count: claims.length })}
            </span>
          </div>
          {report.audit_status === "incomplete" && (
            <div className="audit-incomplete-notice" role="status">
              {t("auditIncomplete")}
            </div>
          )}
        </>
      }
      after={
        claims.length > 0 ? (
          <details className="claim-audit-details">
            <summary>{t("keyClaimsAudit")}</summary>
            <ol>
              {claims.map((claim) => (
                <li key={claim.id}>
                  <strong>{claim.statement}</strong>
                  {claim.implication && <p>{claim.implication}</p>}
                </li>
              ))}
            </ol>
          </details>
        ) : null
      }
    />
  );
}

export function ResearchMarkdownReader({
  markdown,
  sections,
  runId,
  reportKey,
  evidenceIndex,
  onEvidence,
  before,
  after,
}: {
  markdown: string;
  sections: AnalystReport["report_sections"];
  runId: string;
  reportKey: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  before?: ReactNode;
  after?: ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollStorageKey = `tradingagents-report-scroll:${runId}:${reportKey}`;
  useReadingPosition(scrollStorageKey, scrollRef);

  return (
    <div className="report-reading-layout">
      <ReportSectionNavigation sections={sections} containerRef={scrollRef} />
      <div className="analyst-report" ref={scrollRef}>
        {before}
        <Markdown
          evidenceAliases={evidenceIndex.aliases}
          onEvidence={onEvidence}
          headingAnchors={sections.map((section) => section.anchor)}
        >
          {markdown}
        </Markdown>
        {after}
      </div>
    </div>
  );
}

function LegacyMarkdownReader({
  markdown,
  runId,
  reportKey,
  evidenceIndex,
  onEvidence,
}: {
  markdown: string;
  runId: string;
  reportKey: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrollStorageKey = `tradingagents-report-scroll:${runId}:${reportKey}`;
  useReadingPosition(scrollStorageKey, scrollRef);

  return (
    <div className="analyst-report" ref={scrollRef}>
      <Markdown
        evidenceAliases={evidenceIndex.aliases}
        onEvidence={onEvidence}
      >
        {markdown}
      </Markdown>
    </div>
  );
}

function ReportSectionNavigation({
  sections,
  containerRef,
}: {
  sections: AnalystReport["report_sections"];
  containerRef: RefObject<HTMLDivElement | null>;
}) {
  const { t } = useTranslation();
  const [active, setActive] = useState(sections[0]?.anchor ?? "");

  useEffect(() => {
    const container = containerRef.current;
    if (!container || sections.length === 0) return;
    const update = () => {
      const threshold = 96;
      let next = sections[0].anchor;
      for (const section of sections) {
        const candidate = document.getElementById(headingDomId(section.anchor));
        const heading =
          candidate && container.contains(candidate) ? candidate : null;
        if (heading && heading.getBoundingClientRect().top <= threshold) {
          next = section.anchor;
        }
      }
      setActive(next);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, [containerRef, sections]);

  if (sections.length === 0) return null;
  const jump = (anchor: string) => {
    const container = containerRef.current;
    const candidate = document.getElementById(headingDomId(anchor));
    const heading =
      container && candidate && container.contains(candidate) ? candidate : null;
    if (!container || !heading) return;
    heading.scrollIntoView?.({ block: "start" });
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${encodeURIComponent(anchor)}`);
    heading.focus({ preventScroll: true });
    setActive(anchor);
  };

  return (
    <FloatingSectionNavigation
      entries={sections.map((section) => ({
        id: section.anchor,
        label: section.title,
      }))}
      active={active}
      title={t("onThisReport")}
      ariaLabel={t("reportNavigation")}
      selectLabel={t("jumpToSection")}
      storageKey="tradingagents-toc:reports"
      onSelect={jump}
    />
  );
}

function useReadingPosition(key: string, ref: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    let frame = requestAnimationFrame(() => {
      const anchor = decodeURIComponent(window.location.hash.slice(1));
      const heading = anchor ? document.getElementById(headingDomId(anchor)) : null;
      if (heading && ref.current?.contains(heading)) heading.scrollIntoView?.({ block: "start" });
      else {
        const saved = sessionStorage.getItem(key);
        if (saved !== null) window.scrollTo?.(0, Math.max(0, Number(saved) || 0));
      }
    });
    const save = () => sessionStorage.setItem(key, String(window.scrollY));
    window.addEventListener("scroll", save, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", save);
    };
  }, [key, ref]);
}

function headingDomId(anchor: string): string {
  return `user-content-${anchor}`;
}

export function MarkdownList({
  title,
  items,
  empty = "—",
  evidenceIndex,
  onEvidence,
}: {
  title: string;
  items: string[];
  empty?: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  return (
    <section className="research-list">
      <h3>{title}</h3>
      {items.length > 0 ? (
        <ul>
          {items.map((item, index) => (
            <li key={`${index}:${item}`}>
              <Markdown
                evidenceAliases={evidenceIndex.aliases}
                onEvidence={onEvidence}
              >
                {item}
              </Markdown>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{empty}</p>
      )}
    </section>
  );
}
