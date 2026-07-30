import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type {
  DebateAgenda,
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
      evidenceStyle="footnote"
      onEvidence={onEvidence}
    >
      {value}
    </Markdown>
  );

  return (
    <div className="deliberation-flow">
      {cases.length > 0 && (
        <section className="deliberation-stage">
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
                <NavigationFields
                  labels={[
                    [t("focusClaims"), artifact.content.focus_claim_ids],
                    [
                      t("reportSections"),
                      artifact.content.report_section_refs,
                    ],
                  ]}
                />
              </ArtifactFrame>
            ))}
          </div>
        </section>
      )}

      {agendas.map((artifact) => (
        <section className="deliberation-stage" key={artifact.id}>
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
        <section className="deliberation-stage" key={round}>
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

      {judges.map((artifact) => (
        <section className="deliberation-stage" key={artifact.id}>
          <StageHeading title={t("judgeDraft")} />
          <ArtifactFrame artifact={artifact}>
            <div className="artifact-rating">
              <strong>{artifact.content.preliminary_rating}</strong>
              <span>
                {t("confidence")}{" "}
                {Math.round(artifact.content.confidence * 100)}%
              </span>
            </div>
            {markdown(artifact.content.markdown)}
          </ArtifactFrame>
        </section>
      ))}

      {risks.length > 0 && (
        <section className="deliberation-stage">
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

      {decisions.map((artifact) => (
        <section className="deliberation-stage" key={artifact.id}>
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
  );
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
  return "role" in value && "markdown" in value && "focus_claim_ids" in value;
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

function isResearchDecision(
  value: ResearchArtifact["content"],
): value is ResearchDecision {
  return "rating" in value && "thesis" in value && "scenarios" in value;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
