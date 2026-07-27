from __future__ import annotations

from pathlib import Path

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
    assert "The evidence was useful" in repository.memory_context("NVDA", "stock")
