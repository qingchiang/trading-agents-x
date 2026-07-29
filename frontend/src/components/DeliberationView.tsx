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
import { MarkdownList } from "./AnalystReportView";
import EvidenceLinks from "./EvidenceLinks";
import Markdown from "./Markdown";
import { ResearchDecisionContent } from "./ResearchDecisionView";

type ArtifactContent = ResearchArtifact["content"];

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
  const cases = visible.filter(
    (artifact): artifact is ResearchArtifact & { content: ResearchCase } =>
      isResearchCase(artifact.content),
  );
  const agendas = visible.filter(
    (artifact): artifact is ResearchArtifact & { content: DebateAgenda } =>
      isDebateAgenda(artifact.content),
  );
  const rebuttals = visible.filter(
    (artifact): artifact is ResearchArtifact & { content: RebuttalReview } =>
      isRebuttalReview(artifact.content),
  );
  const judges = visible.filter(
    (artifact): artifact is ResearchArtifact & { content: JudgeDraft } =>
      isJudgeDraft(artifact.content),
  );
  const risks = visible.filter(
    (artifact): artifact is ResearchArtifact & { content: RiskReview } =>
      isRiskReview(artifact.content),
  );
  const decisions = visible.filter(
    (artifact): artifact is ResearchArtifact & { content: ResearchDecision } =>
      isResearchDecision(artifact.content),
  );
  const latestJudge = judges.at(-1)?.content;
  const resolutionByIssue = new Map(
    (latestJudge?.rulings ?? []).map((ruling) => [
      ruling.agenda_id,
      ruling.resolution,
    ]),
  );
  const rebuttalsByRound = useMemo(() => {
    const grouped = new Map<number, typeof rebuttals>();
    rebuttals.forEach((artifact) => {
      const round = artifact.content.round;
      grouped.set(round, [...(grouped.get(round) ?? []), artifact]);
    });
    return Array.from(grouped.entries()).sort(
      ([left], [right]) => left - right,
    );
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

  return (
    <div className="deliberation-flow">
      {cases.length > 0 && (
        <section className="deliberation-stage">
          <StageHeading
            eyebrow={t("independentCases")}
            title={t("bullBearCases")}
          />
          <div className="case-comparison">
            {cases.map((artifact) => (
              <ArtifactFrame artifact={artifact} key={artifact.id}>
                <ResearchCaseView
                  value={artifact.content}
                  evidenceIndex={evidenceIndex}
                  onEvidence={onEvidence}
                />
              </ArtifactFrame>
            ))}
          </div>
        </section>
      )}

      {agendas.map((artifact) => (
        <section className="deliberation-stage" key={artifact.id}>
          <StageHeading
            eyebrow={t("debateAgenda")}
            title={artifact.content.executive_summary}
          />
          <ArtifactFrame artifact={artifact} compactHeader>
            <AgendaView
              value={artifact.content}
              resolutionByIssue={resolutionByIssue}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
          </ArtifactFrame>
        </section>
      ))}

      {rebuttalsByRound.map(([round, roundArtifacts]) => (
        <section className="deliberation-stage" key={round}>
          <StageHeading
            eyebrow={t("targetedRebuttals")}
            title={t("rebuttalRound", { round })}
          />
          <div className="rebuttal-round">
            {roundArtifacts.map((artifact) => (
              <ArtifactFrame artifact={artifact} key={artifact.id}>
                <RebuttalView
                  value={artifact.content}
                  evidenceIndex={evidenceIndex}
                  onEvidence={onEvidence}
                />
              </ArtifactFrame>
            ))}
          </div>
        </section>
      ))}

      {judges.map((artifact) => (
        <section className="deliberation-stage" key={artifact.id}>
          <StageHeading
            eyebrow={t("researchJudge")}
            title={t("judgeDraft")}
          />
          <ArtifactFrame artifact={artifact} compactHeader>
            <JudgeView
              value={artifact.content}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
          </ArtifactFrame>
        </section>
      ))}

      {risks.length > 0 && (
        <section className="deliberation-stage">
          <StageHeading
            eyebrow={t("riskReview")}
            title={t("riskLenses")}
          />
          <div className="risk-review-grid">
            {risks.map((artifact) => (
              <ArtifactFrame artifact={artifact} key={artifact.id}>
                <RiskReviewView
                  value={artifact.content}
                  evidenceIndex={evidenceIndex}
                  onEvidence={onEvidence}
                />
              </ArtifactFrame>
            ))}
          </div>
        </section>
      )}

      {decisions.map((artifact) => (
        <section className="deliberation-stage" key={artifact.id}>
          <StageHeading
            eyebrow={t("finalCommittee")}
            title={t("finalResearchOpinion")}
          />
          <ArtifactFrame artifact={artifact} compactHeader>
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
  compactHeader = false,
}: {
  artifact: ResearchArtifact;
  children: React.ReactNode;
  compactHeader?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <article className="artifact-card typed-artifact-card">
      <header className={compactHeader ? "artifact-header compact" : "artifact-header"}>
        <div>
          <span className="artifact-stage">{artifact.stage}</span>
          <h3>{humanize(artifact.role)}</h3>
        </div>
        <small>
          {t("round")} {artifact.round} · {t("attempt")} {artifact.attempt}
          {" · "}
          {t("promptVersion")} {artifact.prompt_version ?? "—"}
          {" · "}
          {humanize(artifact.generation_method)}
        </small>
      </header>
      <div className="artifact-body">{children}</div>
    </article>
  );
}

function ResearchCaseView({
  value,
  evidenceIndex,
  onEvidence,
}: {
  value: ResearchCase;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={`research-case research-case-${value.role}`}>
      <span className="case-role">
        {t(value.role === "bull" ? "bullCase" : "bearCase")}
      </span>
      <MarkdownText
        title={t("executiveSummary")}
        value={value.executive_summary}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <MarkdownText
        title={t("thesis")}
        value={value.thesis}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <section className="case-arguments">
        <h4>{t("caseArguments")}</h4>
        {value.arguments.map((argument) => (
          <article key={argument.id}>
            <header>
              <code>{argument.id}</code>
              <span>
                {t("confidence")} {Math.round(argument.confidence * 100)}%
              </span>
            </header>
            <Markdown
              evidenceAliases={evidenceIndex.aliases}
              onEvidence={onEvidence}
            >
              {argument.statement}
            </Markdown>
            <dl>
              <div>
                <dt>{t("causalMechanism")}</dt>
                <dd>{argument.mechanism}</dd>
              </div>
              <div>
                <dt>{t("implication")}</dt>
                <dd>{argument.implication}</dd>
              </div>
            </dl>
            <ClaimIds refs={argument.claim_ids} />
            <EvidenceLinks
              refs={argument.evidence_refs}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
              compact
            />
          </article>
        ))}
      </section>
      <div className="case-list-grid">
        <MarkdownList
          title={t("strongestCounterarguments")}
          items={value.strongest_counterarguments}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("fragileAssumptions")}
          items={value.fragile_assumptions}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("catalysts")}
          items={value.catalysts ?? []}
          empty={t("noCatalystsIdentified")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("risks")}
          items={value.risks}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
      </div>
      <EvidenceLinks
        refs={value.evidence_refs}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
    </div>
  );
}

function AgendaView({
  value,
  resolutionByIssue,
  evidenceIndex,
  onEvidence,
}: {
  value: DebateAgenda;
  resolutionByIssue: Map<string, string>;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="agenda-issues">
      {value.issues.map((issue) => (
        <article className={`agenda-issue importance-${issue.importance}`} key={issue.id}>
          <header>
            <code>{issue.id}</code>
            <span>{t("importance")}: {humanize(issue.importance)}</span>
            <strong>
              {t("currentResolution")}:{" "}
              {humanize(resolutionByIssue.get(issue.id) ?? "open")}
            </strong>
          </header>
          <h3>{issue.question}</h3>
          <div className="agenda-positions">
            <section>
              <strong>{t("bullPosition")}</strong>
              <Markdown
                evidenceAliases={evidenceIndex.aliases}
                onEvidence={onEvidence}
              >
                {issue.bull_position}
              </Markdown>
            </section>
            <section>
              <strong>{t("bearPosition")}</strong>
              <Markdown
                evidenceAliases={evidenceIndex.aliases}
                onEvidence={onEvidence}
              >
                {issue.bear_position}
              </Markdown>
            </section>
          </div>
          <ClaimIds refs={issue.claim_ids} />
          <EvidenceLinks
            refs={issue.evidence_refs}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
            compact
          />
        </article>
      ))}
    </div>
  );
}

function RebuttalView({
  value,
  evidenceIndex,
  onEvidence,
}: {
  value: RebuttalReview;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={`rebuttal-view rebuttal-${value.role}`}>
      <MarkdownText
        title={t("thesisUpdate")}
        value={value.thesis_update}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <div className="rebuttal-responses">
        {value.responses.map((response) => (
          <article key={response.agenda_id}>
            <header>
              <code>{response.agenda_id}</code>
              <span className={`rebuttal-outcome outcome-${response.outcome}`}>
                {humanize(response.outcome)}
              </span>
            </header>
            <Markdown
              evidenceAliases={evidenceIndex.aliases}
              onEvidence={onEvidence}
            >
              {response.response}
            </Markdown>
            <dl>
              <div>
                <dt>{t("causalMechanism")}</dt>
                <dd>{response.causal_mechanism}</dd>
              </div>
            </dl>
            <ClaimIds refs={response.claim_ids} />
            <EvidenceLinks
              refs={response.evidence_refs}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
              compact
            />
            <MarkdownList
              title={t("remainingQuestions")}
              items={response.remaining_questions ?? []}
              empty={t("noneRecorded")}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
          </article>
        ))}
      </div>
      <MarkdownList
        title={t("remainingQuestions")}
        items={value.remaining_questions ?? []}
        empty={t("noneRecorded")}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <EvidenceLinks
        refs={value.new_evidence_refs ?? []}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
        className="new-evidence-links"
      />
    </div>
  );
}

function JudgeView({
  value,
  evidenceIndex,
  onEvidence,
}: {
  value: JudgeDraft;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="judge-view">
      <div className="judge-rating">
        <span>{t("preliminaryRating")}</span>
        <strong>{value.preliminary_rating}</strong>
        <small>
          {t("confidence")} {Math.round(value.confidence * 100)}%
        </small>
      </div>
      <MarkdownText
        title={t("executiveSummary")}
        value={value.executive_summary}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <MarkdownText
        title={t("thesis")}
        value={value.thesis}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <section className="ruling-list">
        <h3>{t("disputeRulings")}</h3>
        {value.rulings.map((ruling) => (
          <article key={ruling.agenda_id}>
            <header>
              <code>{ruling.agenda_id}</code>
              <strong>{humanize(ruling.resolution)}</strong>
            </header>
            <Markdown
              evidenceAliases={evidenceIndex.aliases}
              onEvidence={onEvidence}
            >
              {ruling.rationale}
            </Markdown>
            <ClaimIds
              label={t("acceptedClaims")}
              refs={ruling.accepted_claim_ids ?? []}
            />
            <ClaimIds
              label={t("rejectedClaims")}
              refs={ruling.rejected_claim_ids ?? []}
            />
            <EvidenceLinks
              refs={ruling.evidence_refs}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
              compact
            />
          </article>
        ))}
      </section>
      <div className="judge-list-grid">
        <MarkdownList
          title={t("catalysts")}
          items={value.catalysts ?? []}
          empty={t("noCatalystsIdentified")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("risks")}
          items={value.risks}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("invalidation")}
          items={value.invalidation_conditions}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("unresolvedQuestions")}
          items={value.unresolved_questions ?? []}
          empty={t("noneRecorded")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
      </div>
      <p className="artifact-horizon">
        <strong>{t("horizon")}:</strong> {value.time_horizon}
      </p>
      <EvidenceLinks
        refs={value.evidence_refs}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <MemoryRefCodes refs={value.memory_refs ?? []} />
    </div>
  );
}

function RiskReviewView({
  value,
  evidenceIndex,
  onEvidence,
}: {
  value: RiskReview;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={`risk-review risk-${value.role}`}>
      <div className="risk-role-heading">
        <strong>{humanize(value.role)}</strong>
        <span>
          {t("confidenceAdjustment")}{" "}
          {value.confidence_adjustment > 0 ? "+" : ""}
          {Math.round(value.confidence_adjustment * 100)} pp
        </span>
      </div>
      <Markdown
        evidenceAliases={evidenceIndex.aliases}
        onEvidence={onEvidence}
      >
        {value.executive_summary}
      </Markdown>
      <section className="risk-findings">
        <h4>{t("riskFindings")}</h4>
        {value.findings.map((finding) => (
          <article
            className={`risk-finding severity-${finding.severity}`}
            key={finding.id}
          >
            <header>
              <code>{finding.id}</code>
              <span>{humanize(finding.kind)}</span>
              <strong>{humanize(finding.severity)}</strong>
            </header>
            <Markdown
              evidenceAliases={evidenceIndex.aliases}
              onEvidence={onEvidence}
            >
              {finding.statement}
            </Markdown>
            <p>
              <strong>{t("causalMechanism")}:</strong> {finding.mechanism}
            </p>
            <ClaimIds refs={finding.related_claim_ids ?? []} />
            <EvidenceLinks
              refs={finding.evidence_refs}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
              compact
            />
          </article>
        ))}
      </section>
      <MarkdownList
        title={t("invalidationPaths")}
        items={value.invalidation_paths}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <MarkdownList
        title={t("recommendedChanges")}
        items={value.recommended_changes}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
      <EvidenceLinks
        refs={value.evidence_refs}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
      />
    </div>
  );
}

function MarkdownText({
  title,
  value,
  evidenceIndex,
  onEvidence,
}: {
  title: string;
  value: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  return (
    <section className="artifact-text-section">
      <h4>{title}</h4>
      <Markdown
        evidenceAliases={evidenceIndex.aliases}
        onEvidence={onEvidence}
      >
        {value}
      </Markdown>
    </section>
  );
}

function ClaimIds({
  refs,
  label,
}: {
  refs: string[];
  label?: string;
}) {
  const { t } = useTranslation();
  if (refs.length === 0) return null;
  return (
    <div className="claim-id-list">
      <strong>{label ?? t("claimIds")}</strong>
      {refs.map((ref) => (
        <code key={ref}>{ref}</code>
      ))}
    </div>
  );
}

function MemoryRefCodes({ refs }: { refs: string[] }) {
  const { t } = useTranslation();
  if (refs.length === 0) return null;
  return (
    <div className="claim-id-list">
      <strong>{t("memoryRefs")}</strong>
      {refs.map((ref) => (
        <code key={ref}>{ref}</code>
      ))}
    </div>
  );
}

function StageHeading({
  eyebrow,
  title,
}: {
  eyebrow: string;
  title: string;
}) {
  return (
    <header className="deliberation-stage-heading">
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
    </header>
  );
}

function isResearchCase(content: ArtifactContent): content is ResearchCase {
  return "role" in content && "arguments" in content;
}

function isDebateAgenda(content: ArtifactContent): content is DebateAgenda {
  return "issues" in content;
}

function isRebuttalReview(
  content: ArtifactContent,
): content is RebuttalReview {
  return "responses" in content && "thesis_update" in content;
}

function isJudgeDraft(content: ArtifactContent): content is JudgeDraft {
  return "preliminary_rating" in content && "rulings" in content;
}

function isRiskReview(content: ArtifactContent): content is RiskReview {
  return "findings" in content && "confidence_adjustment" in content;
}

function isResearchDecision(
  content: ArtifactContent,
): content is ResearchDecision {
  return "rating" in content && "scenarios" in content;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
