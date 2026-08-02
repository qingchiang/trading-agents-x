import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  DebateAgenda,
  DecisionBrief,
  JudgeDraft,
  RebuttalReview,
  ResearchArtifact,
  ResearchCase,
  ResearchDecision,
  RiskReview,
} from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import Markdown from "./Markdown";
import { ResearchDecisionContent } from "./ResearchDecisionView";

export default function DeliberationView({
  artifacts,
  evidenceIndex,
  onEvidence,
}: {
  artifacts: ResearchArtifact[];
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const visible = artifacts.filter((artifact) => artifact.stage !== "analyst");
  const cases = typedArtifacts(visible, isResearchCase);
  const agendas = typedArtifacts(visible, isDebateAgenda);
  const rebuttals = typedArtifacts(visible, isRebuttalReview);
  const judges = typedArtifacts(visible, isJudgeDraft);
  const risks = typedArtifacts(visible, isRiskReview);
  const briefs = typedArtifacts(visible, isDecisionBrief);
  const decisions = typedArtifacts(visible, isResearchDecision);
  const dispositionByIssue = new Map(
    (judges.at(-1)?.content.issue_dispositions ?? []).map((item) => [
      item.issue_id,
      item.status,
    ]),
  );
  const rebuttalsByRound = useMemo(() => {
    const grouped = new Map<number, typeof rebuttals>();
    rebuttals.forEach((artifact) => {
      const round = artifact.content.round;
      grouped.set(round, [...(grouped.get(round) ?? []), artifact]);
    });
    return [...grouped.entries()].sort(([left], [right]) => left - right);
  }, [rebuttals]);
  const navigation = useMemo<DeliberationNavigationEntry[]>(() => {
    const entries: DeliberationNavigationEntry[] = [];
    if (cases.length > 0) {
      entries.push({ id: "deliberation-case", label: t("bullBearCases") });
    }
    agendas.forEach((_, index) =>
      entries.push({
        id: `deliberation-agenda-${index + 1}`,
        label: numberedLabel(t("debateAgenda"), index, agendas.length),
      }),
    );
    rebuttalsByRound.forEach(([round]) =>
      entries.push({
        id: `deliberation-rebuttal-${round}`,
        label: t("rebuttalRound", { round }),
      }),
    );
    judges.forEach((_, index) =>
      entries.push({
        id: `deliberation-judge-${index + 1}`,
        label: numberedLabel(t("judgeDraft"), index, judges.length),
      }),
    );
    if (risks.length > 0) {
      entries.push({ id: "deliberation-risk", label: t("riskLenses") });
    }
    briefs.forEach((_, index) =>
      entries.push({
        id: `deliberation-decision-brief-${index + 1}`,
        label: numberedLabel(t("decisionBrief"), index, briefs.length),
      }),
    );
    decisions.forEach((_, index) =>
      entries.push({
        id: `deliberation-final-${index + 1}`,
        label: numberedLabel(t("finalResearchOpinion"), index, decisions.length),
      }),
    );
    return entries;
  }, [agendas, briefs, cases, decisions, judges, rebuttalsByRound, risks, t]);

  if (visible.length === 0) {
    return (
      <div className="empty-state">
        {artifacts.length === 0
          ? t("noArtifactsRecorded")
          : t("noDeliberation")}
      </div>
    );
  }

  const markdown = (value: string) => (
    <Markdown
      evidenceAliases={evidenceIndex.aliases}
      onEvidence={onEvidence}
    >
      {value}
    </Markdown>
  );

  return (
    <div className="deliberation-reading-layout">
      <DeliberationNavigation entries={navigation} />
      <div className="deliberation-flow">
      {cases.length > 0 && (
        <section
          className="deliberation-stage"
          id="deliberation-case"
          tabIndex={-1}
        >
          <StageHeading title={t("bullBearCases")} />
          <div className="case-comparison">
            {cases.map((artifact) => (
              <ArtifactFrame artifact={artifact} key={artifact.id}>
                <span className={`case-role case-role-${artifact.content.role}`}>
                  {t(
                    artifact.content.role === "bull"
                      ? "bullCase"
                      : "bearCase",
                  )}
                </span>
                {markdown(artifact.content.markdown)}
              </ArtifactFrame>
            ))}
          </div>
        </section>
      )}

      {agendas.map((artifact, index) => (
        <section
          className="deliberation-stage"
          id={`deliberation-agenda-${index + 1}`}
          tabIndex={-1}
          key={artifact.id}
        >
          <StageHeading title={t("debateAgenda")} />
          <ArtifactFrame artifact={artifact}>
            <p>{artifact.content.summary}</p>
            <ol className="agenda-list">
              {artifact.content.issues.map((issue) => (
                <li key={issue.id}>
                  <strong>{issue.question}</strong>
                  <span>
                    {issue.importance}
                    {dispositionByIssue.has(issue.id)
                      ? ` · ${dispositionByIssue.get(issue.id)}`
                      : ""}
                  </span>
                </li>
              ))}
            </ol>
          </ArtifactFrame>
        </section>
      ))}

      {rebuttalsByRound.map(([round, entries]) => (
        <section
          className="deliberation-stage"
          id={`deliberation-rebuttal-${round}`}
          tabIndex={-1}
          key={round}
        >
          <StageHeading title={t("rebuttalRound", { round })} />
          <div className="rebuttal-round">
            {entries.map((artifact) => (
              <ArtifactFrame artifact={artifact} key={artifact.id}>
                {markdown(artifact.content.markdown)}
                <NavigationFields
                  labels={[
                    [
                      t("addressedIssues"),
                      artifact.content.addressed_issue_ids,
                    ],
                    [t("openIssues"), artifact.content.open_issue_ids],
                  ]}
                />
              </ArtifactFrame>
            ))}
          </div>
        </section>
      ))}

      {judges.map((artifact, index) => (
        <section
          className="deliberation-stage"
          id={`deliberation-judge-${index + 1}`}
          tabIndex={-1}
          key={artifact.id}
        >
          <StageHeading title={t("judgeDraft")} />
          <ArtifactFrame artifact={artifact}>
            <div className="artifact-rating">
              <strong>{artifact.content.preliminary_rating ?? "—"}</strong>
              <span>
                {t("confidence")}{" "}
                {artifact.content.confidence == null
                  ? "—"
                  : `${Math.round(artifact.content.confidence * 100)}%`}
              </span>
            </div>
            {markdown(artifact.content.markdown)}
          </ArtifactFrame>
        </section>
      ))}

      {risks.length > 0 && (
        <section
          className="deliberation-stage"
          id="deliberation-risk"
          tabIndex={-1}
        >
          <StageHeading title={t("riskLenses")} />
          <div className="risk-review-grid">
            {risks.map((artifact) => (
              <ArtifactFrame artifact={artifact} key={artifact.id}>
                {markdown(artifact.content.markdown)}
                <NavigationFields
                  labels={[
                    [
                      t("challengedIssues"),
                      artifact.content.challenged_issue_ids,
                    ],
                    [
                      t("unresolvedIssues"),
                      artifact.content.unresolved_issue_ids,
                    ],
                  ]}
                />
              </ArtifactFrame>
            ))}
          </div>
        </section>
      )}

      {briefs.map((artifact, index) => (
        <section
          className="deliberation-stage"
          id={`deliberation-decision-brief-${index + 1}`}
          tabIndex={-1}
          key={artifact.id}
        >
          <StageHeading title={t("decisionBrief")} />
          <ArtifactFrame artifact={artifact}>
            <div className="decision-brief-notice" role="note">
              {t("decisionBriefNotice")}
            </div>
            {markdown(artifact.content.markdown)}
          </ArtifactFrame>
        </section>
      ))}

      {decisions.map((artifact, index) => (
        <section
          className="deliberation-stage"
          id={`deliberation-final-${index + 1}`}
          tabIndex={-1}
          key={artifact.id}
        >
          <StageHeading title={t("finalResearchOpinion")} />
          <ArtifactFrame artifact={artifact}>
            <ResearchDecisionContent
              decision={artifact.content}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
              embedded
            />
          </ArtifactFrame>
        </section>
      ))}
      </div>
    </div>
  );
}

type DeliberationNavigationEntry = {
  id: string;
  label: string;
};

function DeliberationNavigation({
  entries,
}: {
  entries: DeliberationNavigationEntry[];
}) {
  const { t } = useTranslation();
  const [active, setActive] = useState(entries[0]?.id ?? "");
  const entryIds = entries.map((entry) => entry.id).join("|");

  useEffect(() => {
    setActive(entries[0]?.id ?? "");
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (observations) => {
        const visible = observations
          .filter((item) => item.isIntersecting)
          .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
        const id = visible[0]?.target.id;
        if (id) setActive(id);
      },
      { rootMargin: "-18% 0px -68% 0px", threshold: 0 },
    );
    entries.forEach((entry) => {
      const target = document.getElementById(entry.id);
      if (target) observer.observe(target);
    });
    return () => observer.disconnect();
  }, [entryIds]);

  if (entries.length === 0) return null;
  const jump = (id: string) => {
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView?.({ behavior: "smooth", block: "start" });
    target.focus({ preventScroll: true });
    setActive(id);
  };

  return (
    <>
      <nav
        className="deliberation-stage-nav"
        aria-label={t("deliberationNavigation")}
      >
        <strong>{t("researchStages")}</strong>
        {entries.map((entry) => (
          <button
            type="button"
            className={active === entry.id ? "active" : ""}
            aria-current={active === entry.id ? "step" : undefined}
            onClick={() => jump(entry.id)}
            key={entry.id}
          >
            {entry.label}
          </button>
        ))}
      </nav>
      <label className="deliberation-stage-select">
        <span>{t("jumpToStage")}</span>
        <select value={active} onChange={(event) => jump(event.target.value)}>
          {entries.map((entry) => (
            <option value={entry.id} key={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function numberedLabel(label: string, index: number, count: number): string {
  return count > 1 ? `${label} ${index + 1}` : label;
}

function ArtifactFrame({
  artifact,
  children,
}: {
  artifact: ResearchArtifact;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <article className="artifact-card typed-artifact-card">
      <header className="artifact-header compact">
        <div>
          <span className="artifact-stage">{artifact.stage}</span>
          <h3>{humanize(artifact.role)}</h3>
        </div>
        <small>
          {t("round")} {artifact.round} · {t("attempt")} {artifact.attempt}
          {" · "}
          {artifact.prompt_version ?? "—"}
        </small>
      </header>
      <div className="artifact-body">{children}</div>
    </article>
  );
}

function StageHeading({ title }: { title: string }) {
  return (
    <div className="stage-heading">
      <h2>{title}</h2>
    </div>
  );
}

function NavigationFields({
  labels,
}: {
  labels: Array<[string, string[] | undefined]>;
}) {
  const visible = labels.filter(([, values]) => values && values.length > 0);
  if (visible.length === 0) return null;
  return (
    <details className="artifact-navigation-fields">
      <summary>Navigation</summary>
      {visible.map(([label, values]) => (
        <p key={label}>
          <strong>{label}:</strong> {values?.join(", ")}
        </p>
      ))}
    </details>
  );
}

function typedArtifacts<T extends ResearchArtifact["content"]>(
  artifacts: ResearchArtifact[],
  guard: (value: ResearchArtifact["content"]) => value is T,
): Array<ResearchArtifact & { content: T }> {
  return artifacts.filter(
    (artifact): artifact is ResearchArtifact & { content: T } =>
      guard(artifact.content),
  );
}

function isResearchCase(value: ResearchArtifact["content"]): value is ResearchCase {
  return (
    "role" in value &&
    "markdown" in value &&
    (value.role === "bull" || value.role === "bear")
  );
}

function isDebateAgenda(value: ResearchArtifact["content"]): value is DebateAgenda {
  return "summary" in value && "issues" in value;
}

function isRebuttalReview(
  value: ResearchArtifact["content"],
): value is RebuttalReview {
  return "round" in value && "addressed_issue_ids" in value && "markdown" in value;
}

function isJudgeDraft(value: ResearchArtifact["content"]): value is JudgeDraft {
  return "preliminary_rating" in value && "issue_dispositions" in value;
}

function isRiskReview(value: ResearchArtifact["content"]): value is RiskReview {
  return "challenged_issue_ids" in value && "unresolved_issue_ids" in value;
}

function isDecisionBrief(
  value: ResearchArtifact["content"],
): value is DecisionBrief {
  return (
    "markdown" in value &&
    "evidence_refs" in value &&
    "warnings" in value &&
    !("role" in value)
  );
}

function isResearchDecision(
  value: ResearchArtifact["content"],
): value is ResearchDecision {
  return "rating" in value && "thesis" in value && "scenarios" in value;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
