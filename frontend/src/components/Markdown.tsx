import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  children?: MarkdownNode[];
};

type EvidenceReferenceOptions = {
  aliases: Record<string, string>;
  style: "alias" | "footnote";
};

export default function Markdown({
  children,
  evidenceAliases = {},
  evidenceStyle = "alias",
  onEvidence,
}: {
  children: string;
  evidenceAliases?: Record<string, string>;
  evidenceStyle?: "alias" | "footnote";
  onEvidence?: (ref: string) => void;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          [
            remarkEvidenceReferences,
            { aliases: evidenceAliases, style: evidenceStyle },
          ],
        ]}
        rehypePlugins={[rehypeSanitize]}
        skipHtml
        components={{
          a: ({ href, children: linkChildren }) => {
            const evidenceRef = evidenceRefFromHref(href);
            if (evidenceRef && onEvidence) {
              return (
                <button
                  type="button"
                  className="inline-evidence-ref"
                  title={evidenceRef}
                  aria-label={`Open evidence ${evidenceRef}`}
                  onClick={() => onEvidence(evidenceRef)}
                >
                  {linkChildren}
                </button>
              );
            }
            return <a href={href}>{linkChildren}</a>;
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

function remarkEvidenceReferences(options: EvidenceReferenceOptions) {
  return (tree: MarkdownNode) => {
    transformEvidenceText(tree, options.aliases, options.style);
  };
}

function transformEvidenceText(
  node: MarkdownNode,
  aliases: Record<string, string>,
  style: "alias" | "footnote",
) {
  if (
    ["code", "inlineCode", "html", "link", "linkReference"].includes(
      node.type,
    )
  ) {
    return;
  }
  if (!node.children) return;

  const children: MarkdownNode[] = [];
  node.children.forEach((child) => {
    if (child.type !== "text" || !child.value) {
      transformEvidenceText(child, aliases, style);
      children.push(child);
      return;
    }
    children.push(...splitEvidenceText(child.value, aliases, style));
  });
  node.children = children;
}

function splitEvidenceText(
  value: string,
  aliases: Record<string, string>,
  style: "alias" | "footnote",
): MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  const pattern = /\[\^(ev_[a-f0-9]{12})\]|\b(ev_[a-f0-9]{12})\b/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    const ref = match[1] ?? match[2];
    const start = match.index ?? 0;
    const alias = aliases[ref];
    if (!alias) continue;
    if (start > cursor) {
      nodes.push({ type: "text", value: value.slice(cursor, start) });
    }
    nodes.push({
      type: "link",
      url: `#evidence-${ref}`,
      children: [
        {
          type: "text",
          value: style === "footnote" ? `[${footnoteNumber(alias)}]` : alias,
        },
      ],
    });
    cursor = start + match[0].length;
  }
  if (cursor === 0) return [{ type: "text", value }];
  if (cursor < value.length) {
    nodes.push({ type: "text", value: value.slice(cursor) });
  }
  return nodes;
}

function footnoteNumber(alias: string): string {
  const match = /^E0*(\d+)$/.exec(alias);
  return match?.[1] ?? alias;
}

function evidenceRefFromHref(href: string | undefined): string | null {
  const match = /^#evidence-(ev_[a-f0-9]{12})$/.exec(href ?? "");
  return match?.[1] ?? null;
}
