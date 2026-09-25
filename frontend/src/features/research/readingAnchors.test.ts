import { expect, test } from "vitest";
import { resolveReadingAnchor } from "./readingAnchors";

test("resolves stable and sanitized Markdown anchors and leaves missing chapters unresolved", () => {
  const root = document.createElement("div");
  root.innerHTML = '<h2 id="assessment-thesis">Thesis</h2><h3 id="user-content-risk">Risk</h3>';
  expect(resolveReadingAnchor(root, "assessment-thesis")?.textContent).toBe("Thesis");
  expect(resolveReadingAnchor(root, "risk")?.textContent).toBe("Risk");
  expect(resolveReadingAnchor(root, "missing")).toBeNull();
});
