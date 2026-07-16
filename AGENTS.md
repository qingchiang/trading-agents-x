# AGENTS.md

This is the public Codex entry point for the repository. It stays intentionally
small so the shared engineering guide has a single source of truth.

**Before doing any work, read [`CLAUDE.md`](CLAUDE.md) in full.** All of its
guidance applies. It is authoritative for the architecture, data vendor/routing
subsystem, config system, commands, CI, and how to change the code.

## Git Commit Instructions

When creating or proposing a Git commit message, inspect the staged diff
(`git diff --cached`) and ensure the message accurately describes only the
staged changes. Do not derive the message from the chat title or use a generic
summary.

Use Conventional Commits:

1. Write a concise title in the form `<type>[optional scope]: <description>`.
2. A body may be omitted only for a single, trivial change that is fully
   explained by the title.
3. Otherwise, add a blank line followed by a concise bulleted body explaining:
   - what changed;
   - why it changed, when supported by the staged diff or task context.
4. Do not include claims unsupported by the staged changes or task context.
5. Mark breaking changes with `!` and explain them in the body or a
   `BREAKING CHANGE:` footer.

Allowed types: `feat`, `fix`, `refactor`, `perf`, `style`, `test`, `docs`,
`build`, `chore`, `ci`, and `revert`.
