# Issue tracker: Local Markdown

Issues and specs for this repo live as local Markdown files under `.scratch/`.
The directory is git-ignored and is not a remote publication surface.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at
  `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- When a skill needs workflow state, record it as a `Status:` line near the top
  of the issue file
- Append conversation history under a `## Comments` heading
- A spec is published as `ready-for-agent`. Mark it `resolved` only after all
  accepted in-scope implementation issues are resolved and the maintainer
  confirms that the implemented behavior satisfies the spec. Explicitly
  deferred or out-of-scope work does not keep that completed scope open.

## When a skill says "publish to the issue tracker"

Create the appropriate local Markdown file under `.scratch/<feature-slug>/`.
Do not create a GitHub Issue unless the maintainer explicitly requests remote
publication.

## When a skill says "fetch the relevant ticket"

Read the referenced file under `.scratch/`.

## Wayfinding operations

- **Map**: `.scratch/<effort>/map.md`
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`
- **Blocking**: record `Blocked by: NN, NN` near the top
- **Frontier**: select the first open, unblocked, and unclaimed issue by number
- **Claim**: set `Status: claimed` before beginning work
- **Resolve**: append the answer under `## Answer`, then set `Status: resolved`
