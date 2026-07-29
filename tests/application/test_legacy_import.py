from __future__ import annotations

from pathlib import Path

import pytest

from tradingagents.application.legacy import LegacyMemoryImporter
from tradingagents.application.repository import RunRepository
from tradingagents.application.settings import AppSettings


def test_legacy_import_is_read_only_reported_and_idempotent(
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    source = tmp_path / "trading_memory.md"
    original = (
        "[2026-01-10 | NVDA | Buy | +4.2% | +2.1% | 5d]\n\n"
        "META: asset_type=stock | market=America/New_York\n\n"
        "DECISION:\nBuy because demand accelerated.\n"
        "REFLECTION:\n"
        "[2026-01-12 → 2026-01-20 | 5d]\nThe evidence was useful."
        "\n\n<!-- ENTRY_END -->\n\n"
        "malformed legacy block"
    )
    source.write_text(original, encoding="utf-8")
    importer = LegacyMemoryImporter(app_settings, repository)

    dry_run = importer.import_file(source, dry_run=True)

    assert dry_run.importable == 1
    assert dry_run.malformed == 1
    assert dry_run.backup is None
    assert source.read_text(encoding="utf-8") == original

    imported = importer.import_file(source, dry_run=False)
    repeated = importer.import_file(source, dry_run=False)

    assert imported.imported == 1
    assert imported.malformed == 1
    assert imported.backup is not None
    assert Path(imported.backup).read_text(encoding="utf-8") == original
    assert repeated.imported == 0
    assert repeated.backup is None
    assert source.read_text(encoding="utf-8") == original
    assert repository.get_result(imported.run_ids[0]).evidence.version == "2"
    memory = repository.memory_context("NVDA", "stock")
    assert "The evidence was useful" in memory.prompt_text()


def test_pending_legacy_entry_stays_pending_and_out_of_memory_context(
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    source = tmp_path / "pending.md"
    original = (
        "[2026-01-10 | NVDA | Hold | pending]\n\n"
        "META: asset_type=stock | market=America/New_York\n\n"
        "DECISION:\nWait for stronger evidence.\n"
        "REFLECTION:\nNo completed observation yet."
    )
    source.write_text(original, encoding="utf-8")

    report = LegacyMemoryImporter(app_settings, repository).import_file(
        source,
        dry_run=False,
    )

    assert report.imported == 1
    entries = repository.memory_entries(ticker="NVDA")
    assert entries[0]["outcome"]["status"] == "pending"
    assert entries[0]["reflection"] == "No completed observation yet."
    assert repository.memory_context("NVDA", "stock").items == ()
    assert source.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("holding_intervals", "expected_in_context"),
    (
        (1, False),
        (2, False),
        (3, False),
        (4, False),
        (5, True),
        (6, True),
        (10, True),
    ),
)
def test_legacy_short_window_is_imported_but_not_used_as_memory(
    holding_intervals,
    expected_in_context,
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    source = tmp_path / f"holding-{holding_intervals}.md"
    source.write_text(
        (
            f"[2026-01-10 | NVDA | Hold | +1.0% | +0.5% | "
            f"{holding_intervals}d]\n\n"
            "DECISION:\nFixture decision.\n"
            "REFLECTION:\nFixture reflection."
        ),
        encoding="utf-8",
    )

    report = LegacyMemoryImporter(app_settings, repository).import_file(
        source,
        dry_run=False,
        create_backup=False,
    )

    assert report.imported == 1
    assert report.backup is None
    memory_text = repository.memory_context("NVDA", "stock").prompt_text()
    assert ("Fixture reflection" in memory_text) is expected_in_context
    assert repository.memory_entries(ticker="NVDA")[0]["outcome"][
        "holding_intervals"
    ] == holding_intervals


def test_malformed_block_is_recorded_once_without_modifying_source(
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    source = tmp_path / "malformed.md"
    original = "[2026-01-10 | NVDA | Hold | not-a-percent]\n\nDECISION:\nHold."
    source.write_text(original, encoding="utf-8")
    importer = LegacyMemoryImporter(app_settings, repository)

    first = importer.import_file(source, dry_run=False)
    second = importer.import_file(source, dry_run=False)

    assert first.malformed == 1
    assert first.issues[0].error.startswith("ValueError:")
    assert first.backup is None
    assert second.malformed == 0
    assert second.skipped == 1
    assert source.read_text(encoding="utf-8") == original


def test_duplicate_blocks_import_once_by_content_hash(
    repository: RunRepository,
    app_settings: AppSettings,
    tmp_path: Path,
) -> None:
    block = (
        "[2026-01-10 | 7203.T | Hold | +1.0% | +0.5% | 5d]\n\n"
        "META: asset_type=stock | market=Asia/Tokyo\n\n"
        "DECISION:\nFixture decision.\n"
        "REFLECTION:\nFixture reflection."
    )
    source = tmp_path / "duplicate.md"
    source.write_text(
        f"{block}\n\n<!-- ENTRY_END -->\n\n{block}",
        encoding="utf-8",
    )

    report = LegacyMemoryImporter(app_settings, repository).import_file(
        source,
        dry_run=False,
    )

    assert report.importable == 2
    assert report.imported == 1
    assert report.skipped == 1
    assert len(repository.memory_entries(ticker="7203.T")) == 1
