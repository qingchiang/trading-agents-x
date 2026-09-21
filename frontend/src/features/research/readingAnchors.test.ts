import { expect, test } from "vitest";
import { resolveReadingAnchor } from "./readingAnchors";

test("resolves known old assessment sections and declines ambiguous historical numbers", () => {
  const root = document.createElement("div");
  root.innerHTML = '<article class="decision-panel-v2"><h2 id="assessment-summary">Summary</h2><h2 id="assessment-thesis">Thesis</h2></article>';
  expect(resolveReadingAnchor(root, "research-section-1")?.id).toBe("assessment-thesis");
  expect(resolveReadingAnchor(root, "research-section-19")).toBeNull();
  expect(resolveReadingAnchor(root, "assessment-summary")?.textContent).toBe("Summary");
});

test("keeps old numbering accurate when summary prose contains its own headings", () => {
  const root = document.createElement("div");
  root.innerHTML = '<article class="decision-panel-v2"><h2 id="assessment-summary">Summary</h2><div class="markdown"><h3>Recorded subheading</h3></div><h2 id="assessment-thesis">Thesis</h2></article>';
  expect(resolveReadingAnchor(root, "research-section-1")?.textContent).toBe("Recorded subheading");
  expect(resolveReadingAnchor(root, "research-section-2")?.id).toBe("assessment-thesis");
});
