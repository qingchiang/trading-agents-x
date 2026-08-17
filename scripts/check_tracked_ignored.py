#!/usr/bin/env python3
"""Reject paths in the Git index that match the repository's ignore rules."""

from __future__ import annotations

import subprocess
import sys

_GIT_COMMAND = ("git", "ls-files", "-ci", "--exclude-standard")


def main() -> int:
    try:
        result = subprocess.run(
            _GIT_COMMAND,
            check=False,
            capture_output=True,
        )
    except OSError:
        print(
            "Repository hygiene check could not inspect the Git index. "
            "Run it from a Git repository with Git available.",
            file=sys.stderr,
        )
        return 2

    if result.returncode != 0:
        print(
            "Repository hygiene check could not inspect the Git index. "
            "Run it from a valid Git repository and try again.",
            file=sys.stderr,
        )
        return 2

    if result.stdout.strip():
        print(
            "Repository hygiene check failed: the Git index contains tracked "
            "path(s) matched by the effective ignore rules.",
            file=sys.stderr,
        )
        print(
            "Inspect the index locally with `git ls-files -ci --exclude-standard`; "
            "then remove the tracked path or adjust the ignore rules intentionally. "
            "This check is read-only.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
