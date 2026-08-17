from __future__ import annotations

import subprocess
from pathlib import Path

CHECK_SCRIPT = Path(__file__).parents[1] / "scripts" / "check_tracked_ignored.py"


def _run(
    repo: Path,
    *command: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _repository_state(repo: Path) -> tuple[str, str]:
    status = _run(
        repo,
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout
    index = _run(
        repo,
        "git",
        "diff",
        "--cached",
        "--name-status",
    ).stdout
    return status, index


def _new_repository(tmp_path: Path) -> Path:
    _run(tmp_path, "git", "init", "--quiet")
    return tmp_path


def _write_ignore_rules(repo: Path, rules: str) -> None:
    (repo / ".gitignore").write_text(rules, encoding="utf-8")
    _run(repo, "git", "add", ".gitignore")


def test_clean_index_passes(tmp_path: Path) -> None:
    repo = _new_repository(tmp_path)
    (repo / "tracked.txt").write_text("tracked", encoding="utf-8")
    _run(repo, "git", "add", "tracked.txt")

    result = _run(repo, str(CHECK_SCRIPT), check=False)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_ignored_untracked_file_is_allowed(tmp_path: Path) -> None:
    repo = _new_repository(tmp_path)
    _write_ignore_rules(repo, "local-only.txt\n")
    ignored_file = repo / "local-only.txt"
    ignored_file.write_text("local", encoding="utf-8")
    before = _repository_state(repo)

    result = _run(repo, str(CHECK_SCRIPT), check=False)

    assert result.returncode == 0
    assert ignored_file.is_file()
    assert _repository_state(repo) == before


def test_file_rule_rejects_tracked_ignored_path_without_revealing_name(
    tmp_path: Path,
) -> None:
    repo = _new_repository(tmp_path)
    _write_ignore_rules(repo, "fixture-secret-name.txt\n")
    ignored_file = repo / "fixture-secret-name.txt"
    ignored_file.write_text("fixture", encoding="utf-8")
    _run(repo, "git", "add", "--force", ignored_file.name)
    before = _repository_state(repo)

    result = _run(repo, str(CHECK_SCRIPT), check=False)

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert ignored_file.name not in output
    assert "git ls-files -ci --exclude-standard" in output
    assert "tracked" in output.lower() and "ignore" in output.lower()
    assert ignored_file.is_file()
    assert _repository_state(repo) == before


def test_ignored_directory_rejects_tracked_path(tmp_path: Path) -> None:
    repo = _new_repository(tmp_path)
    _write_ignore_rules(repo, "ignored-directory/\n")
    ignored_file = repo / "ignored-directory" / "tracked.txt"
    ignored_file.parent.mkdir()
    ignored_file.write_text("fixture", encoding="utf-8")
    _run(
        repo,
        "git",
        "add",
        "--force",
        "ignored-directory/tracked.txt",
    )

    result = _run(repo, str(CHECK_SCRIPT), check=False)

    assert result.returncode != 0
    assert ignored_file.name not in result.stdout + result.stderr


def test_effective_negation_allows_intentionally_admitted_path(tmp_path: Path) -> None:
    repo = _new_repository(tmp_path)
    _write_ignore_rules(repo, "admitted/*\n!admitted/kept.txt\n")
    admitted_file = repo / "admitted" / "kept.txt"
    admitted_file.parent.mkdir()
    admitted_file.write_text("kept", encoding="utf-8")
    _run(repo, "git", "add", "admitted/kept.txt")

    result = _run(repo, str(CHECK_SCRIPT), check=False)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_ci_runs_the_repository_owned_check() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "repository-hygiene:" in workflow
    assert "run: ./scripts/check_tracked_ignored.py" in workflow
