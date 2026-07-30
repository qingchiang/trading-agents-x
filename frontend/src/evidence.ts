import type {
  EvidenceBundle,
  EvidenceItem,
} from "./api/client";

export type EvidenceDisplayGroup = {
  alias: string;
  canonical: EvidenceItem;
  items: EvidenceItem[];
  refs: string[];
  sources: string[];
  evidenceTypes: string[];
  quality: NonNullable<EvidenceItem["quality"]>;
  fallback: boolean;
};

export type EvidenceReferenceIndex = {
  aliases: Record<string, string>;
  primaryRefs: Record<string, string>;
  refsByAlias: Record<string, string[]>;
  groups: EvidenceDisplayGroup[];
};

export type EvidenceRefGroup = {
  alias: string;
  targetRef: string;
  refs: string[];
  sources: string[];
};

const qualityRank: Record<
  NonNullable<EvidenceItem["quality"]>,
  number
> = {
  high: 0,
  medium: 1,
  low: 2,
  unavailable: 3,
};

export function buildEvidenceReferenceIndex(
  evidence: EvidenceBundle | null,
): EvidenceReferenceIndex {
  const grouped = new Map<string, EvidenceItem[]>();
  for (const item of evidence?.items ?? []) {
    const key =
      item.content !== null &&
      item.content !== undefined &&
      item.content.length > 0
        ? `content:${item.content}`
        : `ref:${item.ref}`;
    grouped.set(key, [...(grouped.get(key) ?? []), item]);
  }

  const aliases: Record<string, string> = {};
  const primaryRefs: Record<string, string> = {};
  const refsByAlias: Record<string, string[]> = {};
  const groups = Array.from(grouped.values()).map(
    (items, index): EvidenceDisplayGroup => {
      const alias = `E${String(index + 1).padStart(2, "0")}`;
      const refs = items.map((item) => item.ref);
      const primaryRef = refs[0];
      refsByAlias[alias] = refs;
      refs.forEach((ref) => {
        aliases[ref] = alias;
        primaryRefs[ref] = primaryRef;
      });
      return {
        alias,
        canonical: items[0],
        items,
        refs,
        sources: unique(
          items.flatMap((item) =>
            item.origins?.length
              ? item.origins.map((origin) => origin.source)
              : [item.source],
          ),
        ),
        evidenceTypes: unique(
          items.flatMap((item) =>
            item.origins?.length
              ? item.origins.map((origin) => origin.evidence_type)
              : [item.evidence_type],
          ),
        ),
        quality: items.reduce<
          NonNullable<EvidenceItem["quality"]>
        >((worst, item) => {
          const quality = item.quality ?? "medium";
          return qualityRank[quality] > qualityRank[worst]
            ? quality
            : worst;
        }, "high"),
        fallback: items.some((item) => item.fallback),
      };
    },
  );

  return { aliases, primaryRefs, refsByAlias, groups };
}

export function groupEvidenceRefs(
  refs: string[],
  index: EvidenceReferenceIndex,
): EvidenceRefGroup[] {
  const seen = new Set<string>();
  const groups: EvidenceRefGroup[] = [];
  refs.forEach((ref) => {
    const alias = index.aliases[ref] ?? ref;
    if (seen.has(alias)) return;
    seen.add(alias);
    groups.push({
      alias,
      targetRef: index.primaryRefs[ref] ?? ref,
      refs: index.refsByAlias[alias] ?? [ref],
      sources:
        index.groups.find((group) => group.alias === alias)?.sources ?? [],
    });
  });
  return groups;
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}
