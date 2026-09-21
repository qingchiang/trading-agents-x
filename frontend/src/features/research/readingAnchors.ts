/** Resolve only historical numbered links whose old identity is still unambiguous. */
export function resolveReadingAnchor(root: ParentNode, anchor: string): HTMLElement | null {
  const byId = (id: string) => [...root.querySelectorAll<HTMLElement>("[id]")].find(element => element.id === id) ?? null;
  const exact = byId(`user-content-${anchor}`) ?? byId(anchor);
  if (exact) return exact;
  const numbered = /^research-section-(\d+)$/.exec(anchor);
  if (!numbered) return null;
  const index = Number(numbered[1]);
  if (root.querySelector(".decision-panel-v2") && !root.querySelector(".deliberation-flow")) {
    const reader = root.querySelector(".workspace-reader, .run-reader") ?? root;
    // Heading levels changed, but assessment heading order is preserved. Market
    // references previously used a span, so that new heading must not shift aliases.
    const headings = [...reader.querySelectorAll<HTMLElement>("h1,h2,h3")].filter(element => element.id !== "assessment-market" && !element.closest("details:not([open]), .reading-toolbar, .source-drawer-layer"));
    return headings[index] ?? null;
  }
  if (root.querySelector("[data-historical-outline]")) {
    // The old outline counted the view header before the Markdown headings.
    const headings = [...root.querySelectorAll<HTMLElement>("h1,h2,h3")].filter(element => !element.closest("details:not([open]), .reading-toolbar, .source-drawer-layer"));
    const target = headings[index];
    return target?.closest(".analyst-report > .markdown") ? target : null;
  }
  return null;
}
