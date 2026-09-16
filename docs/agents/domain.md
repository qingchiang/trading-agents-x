# Domain Docs

How engineering skills should consume this repository's domain documentation.

## Read when relevant

- Read `CONTEXT.md` when working on domain terminology or concepts.
- Read relevant ADRs under `docs/adr/` when work touches their design decisions.

These documents are not prerequisites for unrelated exploration or mechanical
edits. Read only the material relevant to the current task.

If either path does not exist, proceed silently. Domain documentation is
created lazily when terminology or durable decisions are resolved.

## Layout

This is a single-context repository:

```text
/
├── CONTEXT.md
└── docs/
    └── adr/
```

`CONTEXT.md` defines the ubiquitous language. ADRs record durable decisions
that are hard to reverse, surprising without context, and based on a real
trade-off.

## Use the glossary's vocabulary

When naming a domain concept in specifications, issues, tests, or proposals,
use the canonical term from `CONTEXT.md` and avoid its rejected synonyms.

If a required concept is absent, either reconsider whether it belongs to this
domain or record the gap for domain modeling.

## Flag ADR conflicts

If proposed work contradicts an ADR, surface the conflict explicitly rather
than silently overriding the existing decision.
