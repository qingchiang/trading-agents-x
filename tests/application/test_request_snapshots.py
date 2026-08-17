"""Retained Run requests are readable independently of creation admission."""

from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import select

from tests.factories import analyst_report, research_decision
from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalysisResult,
    ArtifactGenerationMethod,
    AssetType,
    EvidenceBundle,
    EvidenceItem,
    ResearchArtifactDraft,
    RunRequestSnapshot,
    RunStatus,
)
from tradingagents.application.database import RunRecord
from tradingagents.application.exporting import render_run_export_markdown
from tradingagents.application.service import AnalysisService


def test_list_and_detail_preserve_stock_and_legacy_crypto_snapshots(
    repository,
    app_settings,
) -> None:
    stock_request = AnalysisRequest(
        ticker="NVDA",
        analysis_date="2026-07-24",
    )
    crypto_request = AnalysisRequest(
        ticker="BTC-USD",
        analysis_date="2026-07-24",
        asset_type=AssetType.CRYPTO,
        analysts=("market", "social", "news"),
    )
    stock, _ = repository.create_run(
        stock_request,
        app_settings.resolve_run(stock_request).snapshot(),
    )
    crypto, _ = repository.create_run(
        crypto_request,
        app_settings.resolve_run(crypto_request).snapshot(),
    )

    with repository.sessions() as session:
        before = {
            record.id: record.request_json
            for record in session.scalars(
                select(RunRecord).where(RunRecord.id.in_((stock.id, crypto.id)))
            )
        }

    stock_detail = repository.get_run(stock.id)
    crypto_detail = repository.get_run(crypto.id)
    page = repository.list_runs()
    listed = {item.id: item for item in page.items}

    assert type(stock_detail.request) is RunRequestSnapshot
    assert type(crypto_detail.request) is RunRequestSnapshot
    assert stock_detail.request.asset_type == "stock"
    assert crypto_detail.request.asset_type == "crypto"
    assert listed[stock.id].request.model_dump(mode="json") == before[stock.id]
    assert listed[crypto.id].request.model_dump(mode="json") == before[crypto.id]
    assert crypto_detail.request.to_analysis_request() == crypto_request

    service = AnalysisService(app_settings, repository=repository)
    try:
        service.enqueue(crypto_detail.request)
    except TypeError as exc:
        assert "AnalysisRequest" in str(exc)
    else:  # pragma: no cover - defensive assertion for the contract boundary
        raise AssertionError("history snapshots must not create new Runs")


def test_legacy_crypto_run_with_research_outputs_remains_exportable(
    repository,
    app_settings,
) -> None:
    request = AnalysisRequest(
        ticker="BTC-USD",
        analysis_date="2026-07-24",
        asset_type=AssetType.CRYPTO,
        analysts=("market", "social", "news"),
    )
    run, _ = repository.create_run(
        request,
        app_settings.resolve_run(request).snapshot(),
    )
    repository.claim_run(run.id, "history-fixture", 30)
    item = EvidenceItem.create(
        source="legacy-fixture",
        evidence_type="market snapshot",
        requested_date=request.analysis_date,
        content="Retained legacy source body.",
    )
    evidence = EvidenceBundle(
        instrument=request.ticker,
        analysis_date=request.analysis_date,
        items=(item,),
    )
    report = analyst_report(evidence_ref=item.ref)
    decision = research_decision(evidence_refs=(item.ref,))
    repository.append_artifact(
        run.id,
        ResearchArtifactDraft(
            node="analyst.market",
            stage="analyst",
            role="market",
            generation_method=ArtifactGenerationMethod.TOOL_CALL,
            content=report,
        ),
    )
    repository.seal_evidence(run.id, evidence)
    repository.complete(
        run.id,
        AnalysisResult(
            run_id=run.id,
            status=RunStatus.SUCCEEDED,
            instrument=request.ticker,
            reports={"market": report},
            decision=decision,
            evidence=evidence,
        ),
        evidence=evidence,
        benchmark="^GSPC",
    )

    service = AnalysisService(app_settings, repository=repository)
    exported = service.get_export(run.id)
    assert type(exported.run.request) is RunRequestSnapshot
    assert exported.run.request.asset_type == "crypto"
    media_type, json_body = service.export(run.id, format="json")
    markdown_type, markdown_body = service.export(run.id, format="markdown")
    package_type, package_body = service.export(run.id, format="package")

    assert media_type == "application/json"
    assert json.loads(json_body)["run"]["request"]["asset_type"] == "crypto"
    assert markdown_type == "text/markdown; charset=utf-8"
    assert "Retained legacy source body." in markdown_body
    assert package_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(package_body)) as archive:
        package_run = json.loads(archive.read("run.json"))
        assert package_run["run"]["request"]["asset_type"] == "crypto"

    trashed, changed = repository.trash_runs((run.id,))
    restored, restored_count = repository.restore_runs((run.id,))
    assert changed == 1
    assert restored_count == 1
    assert trashed[0].request.asset_type == "crypto"
    assert restored[0].request.asset_type == "crypto"
    assert render_run_export_markdown(exported) == markdown_body
