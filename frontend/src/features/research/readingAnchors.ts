/** Resolve stable reader anchors, including sanitized Markdown heading IDs. */
export function resolveReadingAnchor(root: ParentNode, anchor: string): HTMLElement | null {
  const elements = [...root.querySelectorAll<HTMLElement>("[id]")];
  return elements.find(element => element.id === `user-content-${anchor}`)
    ?? elements.find(element => element.id === anchor)
    ?? null;
}
