import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

type MarkdownNode = {
  type: string;
  value?: string;
  url?: string;
  identifier?: string;
  label?: string;
  children?: MarkdownNode[];
  data?: {
    hProperties?: Record<string, unknown>;
  };
};

type EvidenceReferenceOptions = {
  aliases: Record<string, string>;
};

export default function Markdown({
  children,
  evidenceAliases = {},
  onEvidence,
  headingAnchors = [],
}: {
  children: string;
  evidenceAliases?: Record<string, string>;
  onEvidence?: (ref: string) => void;
  headingAnchors?: string[];
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          [
            remarkEvidenceReferences,
            { aliases: evidenceAliases },
          ],
          [remarkHeadingAnchors, { anchors: headingAnchors }],
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
          h1: ({ children: headingChildren, node: _node, ...props }) => (
            <h1 {...props} tabIndex={-1}>{headingChildren}</h1>
          ),
          h2: ({ children: headingChildren, node: _node, ...props }) => (
            <h2 {...props} tabIndex={-1}>{headingChildren}</h2>
          ),
          h3: ({ children: headingChildren, node: _node, ...props }) => (
            <h3 {...props} tabIndex={-1}>{headingChildren}</h3>
          ),
          h4: ({ children: headingChildren, node: _node, ...props }) => (
            <h4 {...props} tabIndex={-1}>{headingChildren}</h4>
          ),
          h5: ({ children: headingChildren, node: _node, ...props }) => (
            <h5 {...props} tabIndex={-1}>{headingChildren}</h5>
          ),
          h6: ({ children: headingChildren, node: _node, ...props }) => (
            <h6 {...props} tabIndex={-1}>{headingChildren}</h6>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

function remarkHeadingAnchors({ anchors }: { anchors: string[] }) {
  return (tree: MarkdownNode) => {
    let index = 0;
    const visit = (node: MarkdownNode) => {
      if (node.type === "heading" && index < anchors.length) {
        const anchor = anchors[index];
        node.data = {
          ...node.data,
          hProperties: {
            ...node.data?.hProperties,
            id: anchor,
          },
        };
        index += 1;
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}

function remarkEvidenceReferences(options: EvidenceReferenceOptions) {
  return (tree: MarkdownNode) => {
    transformEvidenceTree(tree, options.aliases);
  };
}

function transformEvidenceTree(
  node: MarkdownNode,
  aliases: Record<string, string>,
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
    const footnoteRef = evidenceRefFromFootnoteNode(child);
    if (child.type === "footnoteDefinition" && footnoteRef) {
      return;
    }
    if (child.type === "footnoteReference" && footnoteRef) {
      const alias = aliases[footnoteRef];
      children.push(
        alias
          ? evidenceLink(footnoteRef, alias)
          : { type: "text", value: `[^${footnoteRef}]` },
      );
      return;
    }
    if (child.type !== "text" || !child.value) {
      transformEvidenceTree(child, aliases);
      children.push(child);
      return;
    }
    children.push(...splitEvidenceText(child.value, aliases));
  });
  node.children = children;
}

function splitEvidenceText(
  value: string,
  aliases: Record<string, string>,
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
    nodes.push(evidenceLink(ref, alias));
    cursor = start + match[0].length;
  }
  if (cursor === 0) return [{ type: "text", value }];
  if (cursor < value.length) {
    nodes.push({ type: "text", value: value.slice(cursor) });
  }
  return nodes;
}

function evidenceLink(ref: string, alias: string): MarkdownNode {
  return {
    type: "link",
    url: `#evidence-${ref}`,
    children: [{ type: "text", value: alias }],
  };
}

function evidenceRefFromFootnoteNode(node: MarkdownNode): string | null {
  const candidate = node.identifier ?? node.label ?? "";
  const match = /^(ev_[a-f0-9]{12})$/.exec(candidate);
  return match?.[1] ?? null;
}

function evidenceRefFromHref(href: string | undefined): string | null {
  const match = /^#evidence-(ev_[a-f0-9]{12})$/.exec(href ?? "");
  return match?.[1] ?? null;
}
