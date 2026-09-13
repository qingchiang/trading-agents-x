import { useReadingPosition } from "../useReadingPosition";
import {
  useEffect,
  useRef,
  useLayoutEffect,
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
          {report.audit_status === "incomplete" && (
            <div className="audit-incomplete-notice" role="status">
              {t("auditIncomplete")}
            </div>
          )}
        </>
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
  extraSections = [],
}: {
  markdown: string;
  sections: AnalystReport["report_sections"];
  runId: string;
  reportKey: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  before?: ReactNode;
  after?: ReactNode;
  extraSections?: AnalystReport["report_sections"];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [fallback, setFallback] = useState<AnalystReport["report_sections"]>([]);
  useLayoutEffect(() => {
    if (sections.length) { setFallback([]); return; }
    const headings = [...(scrollRef.current?.querySelectorAll<HTMLElement>(".markdown:first-of-type :is(h1,h2,h3)") ?? [])];
    const occurrences = new Map<string, number>();
    setFallback(headings.map(heading => {
      const title = heading.textContent ?? "";
      let hash = 2166136261;
      for (const char of `${reportKey}:${title}`) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
      const identity = `legacy-${(hash >>> 0).toString(36)}`;
      const count = occurrences.get(identity) ?? 0; occurrences.set(identity, count + 1);
      const anchor = `${identity}${count ? `-${count}` : ""}`;
      heading.id = `user-content-${anchor}`;
      return { id: anchor, anchor, title, source_refs: [] };
    }));
  }, [markdown, reportKey, sections.length]);
  const scrollStorageKey = `tradingagents-report-scroll:${runId}:${reportKey}`;
  useReadingPosition(scrollStorageKey, scrollRef);

  return (
    <div className="report-reading-layout" data-report-outline>
      <ReportSectionNavigation sections={[...(sections.length ? sections : fallback), ...extraSections]} containerRef={scrollRef} />
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
  return <ResearchMarkdownReader markdown={markdown} sections={emptySections} runId={runId} reportKey={reportKey} evidenceIndex={evidenceIndex} onEvidence={onEvidence} />;
}
const emptySections: AnalystReport["report_sections"] = [];

function ReportSectionNavigation({
  sections,
  containerRef,
}: {
  sections: AnalystReport["report_sections"];
  containerRef: RefObject<HTMLDivElement | null>;
}) {
  const { t } = useTranslation();
  const [active, setActive] = useState(sections[0]?.anchor ?? "");
  const [levels, setLevels] = useState<Record<string, number>>({});

  useEffect(() => {
    const container = containerRef.current;
    if (!container || sections.length === 0) return;
    const update = () => {
      const threshold = 96;
      let next = sections[0].anchor;
      for (const section of sections) {
        const candidate = document.getElementById(headingDomId(section.anchor)) ?? document.getElementById(section.anchor);
        const heading =
          candidate && container.contains(candidate) ? candidate : null;
        if (heading && heading.getBoundingClientRect().top <= threshold) {
          next = section.anchor;
        }
      }
      // The final section may be too short to reach the top of the viewport.
      if (window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2) {
        const last = sections.at(-1)!;
        const heading = document.getElementById(headingDomId(last.anchor)) ?? document.getElementById(last.anchor);
        if (heading && container.contains(heading) && heading.getBoundingClientRect().top < window.innerHeight) next = last.anchor;
      }
      setActive(next);
    };
    const nextLevels = Object.fromEntries(sections.map(section => [section.anchor, Number(document.getElementById(headingDomId(section.anchor))?.tagName.slice(1)) || 2]));
    setLevels(current => JSON.stringify(current) === JSON.stringify(nextLevels) ? current : nextLevels);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, [containerRef, sections]);

  if (sections.length === 0) return null;
  const jump = (anchor: string) => {
    const container = containerRef.current;
    const candidate = document.getElementById(headingDomId(anchor)) ?? document.getElementById(anchor);
    const heading =
      container && candidate && container.contains(candidate) ? candidate : null;
    if (!container || !heading) return;
    heading.scrollIntoView?.({ block: "start" });
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#${encodeURIComponent(anchor)}`);
    heading.focus({ preventScroll: true });
    setActive(anchor);
  };

  return (
    <FloatingSectionNavigation
      entries={sections.map((section) => ({
        id: section.anchor,
        label: section.title,
        level: levels[section.anchor] ?? 2,
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


function headingDomId(anchor: string): string {
  return `user-content-${anchor}`;
}

export function MarkdownList({
  title,
  outlineId,
  items,
  empty = "—",
  evidenceIndex,
  onEvidence,
}: {
  title: string;
  outlineId?: string;
  items: string[];
  empty?: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  return (
    <section className="research-list">
      {outlineId ? <h2 id={outlineId} data-outline={title}>{title}</h2> : <h3>{title}</h3>}
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
