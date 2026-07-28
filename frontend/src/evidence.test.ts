import { expect, test } from "vitest";

import type { EvidenceBundle } from "./api/client";
import {
  buildEvidenceReferenceIndex,
  groupEvidenceRefs,
} from "./evidence";

test("assigns one stable alias to exact duplicate evidence bodies", () => {
  const evidence = {
    version: "1",
    instrument: "7011.T",
    analysis_date: "2026-07-27",
    items: [
      {
        ref: "ev_000000000001",
        source: "source-a",
        evidence_type: "news",
        requested_date: "2026-07-27",
        content: "EXACT BODY",
        quality: "high",
        fallback: false,
      },
      {
        ref: "ev_000000000002",
        source: "source-b",
        evidence_type: "filing",
        requested_date: "2026-07-27",
        content: "EXACT BODY",
        quality: "low",
        fallback: true,
      },
      {
        ref: "ev_000000000003",
        source: "source-c",
        evidence_type: "price",
        requested_date: "2026-07-27",
        content: "DIFFERENT BODY",
        quality: "high",
        fallback: false,
      },
    ],
  } as EvidenceBundle;

  const index = buildEvidenceReferenceIndex(evidence);

  expect(index.groups).toHaveLength(2);
  expect(index.aliases).toEqual({
    ev_000000000001: "E01",
    ev_000000000002: "E01",
    ev_000000000003: "E02",
  });
  expect(index.groups[0]).toMatchObject({
    alias: "E01",
    refs: ["ev_000000000001", "ev_000000000002"],
    sources: ["source-a", "source-b"],
    quality: "low",
    fallback: true,
  });
  expect(
    groupEvidenceRefs(
      ["ev_000000000002", "ev_000000000001"],
      index,
    ),
  ).toEqual([
    {
      alias: "E01",
      targetRef: "ev_000000000001",
      refs: ["ev_000000000001", "ev_000000000002"],
    },
  ]);
});
