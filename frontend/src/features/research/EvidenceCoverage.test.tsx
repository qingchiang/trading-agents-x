import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { ResearchNodeView } from "../../shared/api/client";
import { buildEvidenceReferenceIndex } from "./evidence";
import i18n from "../../shared/i18n";
import { Router } from "../../app/router";
import EvidenceCoverage from "./EvidenceCoverage";

test("preserves unknown restrictions and uses explicit domain references, not attempted providers", async () => {
  await i18n.changeLanguage("en");
  const onDomain = vi.fn();
  const node = { instrument: "TEST", full_baseline_run_id: "baseline", collection_summary: { version: "1", market: "united_states", domains: [
    { domain: "news", state: "partial", temporal_bases: ["pit"], diagnostic: { code: "bounded_news_feed" }, evidence_refs: ["ev_0123456789ab"], sources: [{ source: "failed-attempt", retrieved_at: "2026-01-01T00:00:00Z", diagnostic: { code: "unavailable" } }] },
    { domain: "social", state: "partial", diagnostic: { code: "unknown_restriction" }, evidence_refs: [] },
  ] }, research_availability: { version: "1", domains: [{ domain: "news", status: "limited" }, { domain: "social", status: "limited" }] } } as ResearchNodeView;
  const index = buildEvidenceReferenceIndex({ instrument: "TEST", analysis_date: "2026-01-01", items: [{ ref: "ev_0123456789ab", source: "recorded-source", evidence_type: "news", content: "Persisted evidence", requested_date: "2026-01-01", quality: "high", fallback: false }] });
  render(<Router initialPath="/timelines/TEST?node=update&view=evidence&trash_state=all"><EvidenceCoverage node={node} evidenceIndex={index} onDomain={onDomain} /></Router>);
  expect(screen.getByText(/does not establish complete coverage/)).toBeVisible();
  expect(screen.getByText("unknown_restriction")).toBeVisible();
  expect(screen.queryByText("failed-attempt")).toBeNull();
  expect(screen.getByText(/recorded-source/)).toBeVisible();
  expect(screen.getByRole("link")).toHaveAttribute("href", "/timelines/TEST?node=baseline&view=evidence&trash_state=all");
  fireEvent.click(screen.getByRole("button", { name: "View materials in this domain" }));
  expect(onDomain).toHaveBeenCalledWith("news", ["ev_0123456789ab"]);
});
