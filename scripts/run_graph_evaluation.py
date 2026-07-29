#!/usr/bin/env python3
"""Plan, record, review, and gate opt-in Research Graph V2 evaluations."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tradingagents.application.contracts import (
    AnalysisRequest,
    AnalystReport,
    ResearchArtifact,
    RunProfile,
    report_language_prompt_label,
    report_language_value,
)
from tradingagents.application.llms import create_run_llms
from tradingagents.application.metrics import MetricsCallback
from tradingagents.application.runtime import RunContext
from tradingagents.application.settings import RunSettings
from tradingagents.evals import (
    EvalMeasurement,
    EvaluationRuntimeIdentity,
    PromptHashCallback,
    RecordedEvalOutput,
    build_call_plan,
    canonical_hash,
    evaluate_release_gates,
    load_records,
    load_reviews,
    load_suite,
    measurement_from_record,
    prepare_contract_fixture_suite,
    prepare_quality_fixture_suite,
    validate_analyst_output,
    validate_research_output,
    write_jsonl,
)
from tradingagents.graph.analyst_synthesis import (
    analyst_report_prompt,
    evidence_warnings,
    invoke_analyst_report,
)
from tradingagents.graph.research_graph import ResearchGraph

_LIVE_GATE = "RUN_LIVE_LLM_EVALS"


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Quality-first evaluation harness. No model is called unless a run "
            f"subcommand receives --execute and {_LIVE_GATE}=1."
        )
    )
    subparsers = parser.add_subparsers(required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Convert checked-in contract fixtures into sealed prompt inputs.",
    )
    prepare.add_argument("--fixtures", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.set_defaults(handler=_prepare)

    prepare_quality = subparsers.add_parser(
        "prepare-quality",
        help="Expand the curated cross-market spec into sealed role inputs.",
    )
    prepare_quality.add_argument("--spec", type=Path, required=True)
    prepare_quality.add_argument("--output", type=Path, required=True)
    prepare_quality.set_defaults(handler=_prepare_quality)

    plan = subparsers.add_parser("plan", help="Print the logical call matrix.")
    plan.add_argument("--suite", type=Path, required=True)
    plan.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=3)
    plan.set_defaults(handler=_plan)

    for name, profile in (
        ("run-v2-analyst", None),
        ("run-v2-standard", RunProfile.STANDARD),
        ("run-v2-deep", RunProfile.DEEP),
    ):
        command = subparsers.add_parser(
            name,
            help="Execute real V2 prompts and persist complete output records.",
        )
        _add_run_arguments(command)
        command.set_defaults(
            handler=(
                _run_analysts
                if profile is None
                else _run_graph
            ),
            profile=profile,
        )

    materialize = subparsers.add_parser(
        "materialize",
        help="Join generated records with separate blinded reviews.",
    )
    materialize.add_argument(
        "--records",
        type=Path,
        action="append",
        required=True,
        help="A records.jsonl file; repeat for each evaluation variant.",
    )
    materialize.add_argument("--reviews", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    materialize.set_defaults(handler=_materialize)

    freeze = subparsers.add_parser(
        "freeze-graph-inputs",
        help="Promote zero-severe V2 Analyst records into graph inputs.",
    )
    freeze.add_argument("--suite", type=Path, required=True)
    freeze.add_argument("--records", type=Path, required=True)
    freeze.add_argument("--repetition", type=int, choices=(1, 2, 3), required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.set_defaults(handler=_freeze_graph_inputs)

    gate = subparsers.add_parser(
        "gate",
        help="Evaluate a complete five-variant measurement matrix.",
    )
    gate.add_argument("--measurements", type=Path, required=True)
    gate.set_defaults(handler=_gate)
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--quick-model", required=True)
    parser.add_argument("--deep-model", required=True)
    parser.add_argument("--quick-reasoning")
    parser.add_argument("--deep-reasoning")
    parser.add_argument("--output-language", default="en")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acknowledge that this subcommand makes paid/live model calls.",
    )


def _prepare(args: argparse.Namespace) -> int:
    suite = prepare_contract_fixture_suite(args.fixtures)
    _write_model(args.output, suite)
    print(f"Wrote {len(suite.cases)} sealed cases to {args.output}")
    return 0


def _prepare_quality(args: argparse.Namespace) -> int:
    suite = prepare_quality_fixture_suite(args.spec)
    _write_model(args.output, suite)
    analyst_jobs = sum(len(case.analyst_inputs) for case in suite.cases)
    print(
        f"Wrote {len(suite.cases)} quality cases and {analyst_jobs} "
        f"Analyst inputs to {args.output}"
    )
    return 0


def _plan(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    analyst_jobs = sum(len(case.analyst_inputs) for case in suite.cases)
    graph_cases = len(suite.cases)
    ready_graph_cases = sum(bool(case.reports) for case in suite.cases)
    plan = build_call_plan(
        analyst_jobs=analyst_jobs,
        graph_cases=graph_cases,
        repetitions=args.repetitions,
    )
    print(
        json.dumps(
            {
                **plan.model_dump(mode="json"),
                "graph_inputs_ready": ready_graph_cases,
            },
            indent=2,
        )
    )
    return 0


def _run_analysts(args: argparse.Namespace) -> int:
    _require_live_authorization(args)
    suite = load_suite(args.suite)
    cases = _selected_cases(suite.cases, args.cases)
    settings = _run_settings(args, RunProfile.STANDARD)
    runtime = _runtime_identity(settings)
    records: list[RecordedEvalOutput] = []
    for case in cases:
        for analyst_input in case.analyst_inputs:
            job_id = f"{case.case_id}:{analyst_input.analyst}"
            for repetition in range(1, args.repetitions + 1):
                metrics = MetricsCallback()
                prompts = PromptHashCallback()
                quick_llm, _deep_llm = create_run_llms(
                    settings,
                    callbacks=[metrics, prompts],
                )
                warnings = evidence_warnings(analyst_input.evidence.items)
                output_language = report_language_prompt_label(
                    settings.output_language
                )
                confidence_override = _analyst_confidence_override(
                    analyst_input
                )
                prompt = analyst_report_prompt(
                    analyst=analyst_input.analyst,
                    draft_narrative=analyst_input.tool_response,
                    bundle=analyst_input.evidence,
                    output_language=output_language,
                )
                output = invoke_analyst_report(
                    quick_llm,
                    analyst=analyst_input.analyst,
                    draft_narrative=analyst_input.tool_response,
                    bundle=analyst_input.evidence,
                    output_language=output_language,
                    confidence_override=confidence_override,
                    warnings=warnings,
                    node=f"analyst.{analyst_input.analyst}",
                )
                artifact = _artifact(
                    case_id=job_id,
                    repetition=repetition,
                    index=1,
                    stage="analyst",
                    role=analyst_input.analyst,
                    prompt_version=(
                        f"analyst-{analyst_input.analyst}-v2"
                    ),
                    generation_method=output.generation_method,
                    content=output.value,
                )
                evaluation = validate_analyst_output(
                    bundle=analyst_input.evidence,
                    reports=(output.value,),
                    artifacts=(artifact,),
                    table_expected=analyst_input.table_expected,
                )
                payload = {
                    "reports": {
                        analyst_input.analyst: output.value.model_dump(
                            mode="json"
                        )
                    },
                    "artifacts": [artifact.model_dump(mode="json")],
                }
                record = RecordedEvalOutput(
                    variant="v2_analyst",
                    case_id=job_id,
                    source_case_id=case.case_id,
                    repetition=repetition,
                    runtime=runtime,
                    prompt_hash=canonical_hash(
                        {
                            "contract": "analyst-report-v2",
                            "prompt": prompt,
                            "schema": AnalystReport.model_json_schema(),
                            "confidence_override": confidence_override,
                        }
                    ),
                    runtime_prompt_hash=prompts.digest(),
                    evidence_hash=_evidence_hash(analyst_input.evidence),
                    output_hash=canonical_hash(payload),
                    metrics=metrics.snapshot(),
                    risk_recall=1.0,
                    issues=evaluation.issues,
                    evidence=analyst_input.evidence,
                    reports={analyst_input.analyst: output.value},
                    artifacts=(artifact,),
                )
                records.append(record)
                _persist_progress(args.output_dir, records, record)
                _print_progress(record)
    return 0


def _run_graph(args: argparse.Namespace) -> int:
    _require_live_authorization(args)
    profile: RunProfile = args.profile
    variant = (
        "v2_standard"
        if profile is RunProfile.STANDARD
        else "v2_deep"
    )
    suite = load_suite(args.suite)
    cases = _selected_cases(suite.cases, args.cases)
    missing = [case.case_id for case in cases if not case.reports]
    if missing:
        raise ValueError(
            "graph evaluation requires approved frozen reports for: "
            + ", ".join(missing)
        )
    settings = _run_settings(args, profile)
    runtime = _runtime_identity(settings)
    records: list[RecordedEvalOutput] = []
    for case in cases:
        for repetition in range(1, args.repetitions + 1):
            metrics = MetricsCallback()
            prompts = PromptHashCallback()
            quick_llm, deep_llm = create_run_llms(
                settings,
                callbacks=[metrics, prompts],
            )
            drafts = []
            request = AnalysisRequest(
                ticker=case.evidence.instrument,
                analysis_date=case.evidence.analysis_date,
                profile=profile,
                analysts=tuple(case.reports),
                llm_provider=settings.llm_provider,
                quick_model=settings.quick_model,
                deep_model=settings.deep_model,
                quick_reasoning_effort=settings.quick_reasoning_effort,
                deep_reasoning_effort=settings.deep_reasoning_effort,
                output_language=settings.output_language,
            )
            context = RunContext(
                run_id=f"eval:{variant}:{case.case_id}:{repetition}",
                request=request,
                settings=settings,
                dataflow_config={},
                memory=_empty_memory(case.evidence),
                instrument_context=(
                    f"Frozen evaluation instrument: {case.evidence.instrument}."
                ),
                cancel_requested=lambda: False,
                artifact_writer=drafts.append,
            )
            execution = ResearchGraph(
                quick_llm=quick_llm,
                deep_llm=deep_llm,
                profile=profile,
                selected_analysts=tuple(case.reports),
                metrics=metrics,
            ).execute_frozen(
                context,
                evidence=case.evidence,
                reports=case.reports,
            )
            artifacts = tuple(
                _artifact(
                    case_id=case.case_id,
                    repetition=repetition,
                    index=index,
                    stage=draft.stage,
                    role=draft.role,
                    prompt_version=draft.prompt_version,
                    generation_method=draft.generation_method,
                    content=draft.content,
                    round_number=draft.round,
                )
                for index, draft in enumerate(drafts, start=1)
            )
            evaluation = validate_research_output(
                bundle=case.evidence,
                reports=execution.reports.values(),
                decision=execution.decision,
                artifacts=artifacts,
                expected_rating=case.expected_rating,
                expected_risk_terms=case.expected_risk_terms,
                table_expected=case.table_expected,
            )
            payload = {
                "reports": {
                    key: report.model_dump(mode="json")
                    for key, report in execution.reports.items()
                },
                "decision": execution.decision.model_dump(mode="json"),
                "artifacts": [
                    artifact.model_dump(mode="json")
                    for artifact in artifacts
                ],
            }
            record = RecordedEvalOutput(
                variant=variant,
                case_id=case.case_id,
                source_case_id=case.case_id,
                repetition=repetition,
                runtime=runtime,
                prompt_hash=_graph_prompt_contract_hash(
                    profile=profile,
                    case=case,
                    settings=settings,
                ),
                runtime_prompt_hash=prompts.digest(),
                evidence_hash=_evidence_hash(case.evidence),
                output_hash=canonical_hash(payload),
                metrics=metrics.snapshot(),
                risk_recall=evaluation.risk_recall,
                issues=evaluation.issues,
                evidence=case.evidence,
                reports=execution.reports,
                decision=execution.decision,
                artifacts=artifacts,
            )
            records.append(record)
            _persist_progress(args.output_dir, records, record)
            _print_progress(record)
    return 0


def _materialize(args: argparse.Namespace) -> int:
    records = []
    artifact_paths: dict[str, str] = {}
    for path in args.records:
        for record in load_records(path):
            if record.record_id in artifact_paths:
                raise ValueError("record inputs contain duplicate record IDs")
            records.append(record)
            artifact_paths[record.record_id] = (
                f"{path}#{record.record_id}"
            )
    review_rows = load_reviews(args.reviews)
    reviews = {review.record_id: review for review in review_rows}
    if len(reviews) != len(review_rows):
        raise ValueError("review input contains duplicate record IDs")
    missing = [record.record_id for record in records if record.record_id not in reviews]
    if missing:
        raise ValueError("missing blinded reviews for: " + ", ".join(missing))
    extra = set(reviews) - set(artifact_paths)
    if extra:
        raise ValueError(
            "reviews contain unknown record IDs: " + ", ".join(sorted(extra))
        )
    measurements = [
        measurement_from_record(
            record,
            reviews[record.record_id],
            artifact_path=artifact_paths[record.record_id],
        )
        for record in records
    ]
    write_jsonl(args.output, measurements)
    print(f"Wrote {len(measurements)} measurements to {args.output}")
    return 0


def _freeze_graph_inputs(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    records = tuple(
        record
        for record in load_records(args.records)
        if record.variant == "v2_analyst"
        and record.repetition == args.repetition
    )
    by_case: dict[str, dict[str, RecordedEvalOutput]] = {}
    for record in records:
        for analyst in record.reports:
            case_records = by_case.setdefault(record.source_case_id, {})
            if analyst in case_records:
                raise ValueError(
                    "duplicate V2 Analyst record for "
                    f"{record.source_case_id}:{analyst}"
                )
            case_records[analyst] = record
    missing = [
        f"{case.case_id}:{analyst_input.analyst}"
        for case in suite.cases
        for analyst_input in case.analyst_inputs
        if analyst_input.analyst not in by_case.get(case.case_id, {})
    ]
    if missing:
        raise ValueError("missing V2 Analyst records for: " + ", ".join(missing))
    frozen_cases = []
    for case in suite.cases:
        case_records = by_case[case.case_id]
        reports = {}
        for analyst_input in case.analyst_inputs:
            record = case_records[analyst_input.analyst]
            if record.severe_issues:
                raise ValueError(
                    f"{record.record_id} has {record.severe_issues} severe issues"
                )
            if record.evidence_hash != _evidence_hash(
                analyst_input.evidence
            ):
                raise ValueError(
                    f"{record.record_id} does not use the suite evidence"
                )
            reports.update(record.reports)
        frozen_cases.append(case.model_copy(update={"reports": reports}))
    _write_model(
        args.output,
        suite.model_copy(update={"cases": tuple(frozen_cases)}),
    )
    print(f"Wrote {len(frozen_cases)} frozen graph cases to {args.output}")
    return 0


def _gate(args: argparse.Namespace) -> int:
    measurements = tuple(
        EvalMeasurement.model_validate_json(line)
        for line in args.measurements.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    groups = {
        "baseline_analyst": tuple(
            item for item in measurements if item.variant == "main_analyst"
        ),
        "current_analyst": tuple(
            item for item in measurements if item.variant == "v2_analyst"
        ),
        "baseline_medium": tuple(
            item for item in measurements if item.variant == "main_medium"
        ),
        "current_standard": tuple(
            item for item in measurements if item.variant == "v2_standard"
        ),
        "current_deep": tuple(
            item for item in measurements if item.variant == "v2_deep"
        ),
    }
    result = evaluate_release_gates(**groups)
    print(result.model_dump_json(indent=2))
    return 0 if result.passed else 1


def _require_live_authorization(args: argparse.Namespace) -> None:
    if not args.execute or os.environ.get(_LIVE_GATE) != "1":
        raise RuntimeError(
            "Live evaluation is disabled. Review `plan`, obtain explicit cost "
            f"authorization, then pass --execute with {_LIVE_GATE}=1."
        )


def _run_settings(
    args: argparse.Namespace,
    profile: RunProfile,
) -> RunSettings:
    if args.max_retries is not None and args.max_retries < 0:
        raise ValueError("--max-retries must be >= 0")
    return RunSettings(
        profile=profile,
        llm_provider=args.provider,
        quick_model=args.quick_model,
        deep_model=args.deep_model,
        quick_reasoning_effort=args.quick_reasoning,
        deep_reasoning_effort=args.deep_reasoning,
        temperature=args.temperature,
        llm_max_retries=args.max_retries,
        output_language=args.output_language,
        data_config={},
    )


def _runtime_identity(settings: RunSettings) -> EvaluationRuntimeIdentity:
    return EvaluationRuntimeIdentity(
        commit_sha=_git_commit(),
        provider=settings.llm_provider,
        quick_model=settings.quick_model,
        deep_model=settings.deep_model,
        quick_reasoning_effort=settings.quick_reasoning_effort,
        deep_reasoning_effort=settings.deep_reasoning_effort,
        output_language=report_language_value(settings.output_language),
        temperature=settings.temperature,
    )


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _selected_cases(cases, selected: list[str] | None):
    if not selected:
        return tuple(cases)
    wanted = set(selected)
    result = tuple(case for case in cases if case.case_id in wanted)
    missing = wanted - {case.case_id for case in result}
    if missing:
        raise ValueError("unknown case IDs: " + ", ".join(sorted(missing)))
    return result


def _empty_memory(evidence):
    from tradingagents.application.contracts import MemoryContext

    return MemoryContext(instrument=evidence.instrument)


def _artifact(
    *,
    case_id: str,
    repetition: int,
    index: int,
    stage: str,
    role: str,
    prompt_version: str,
    generation_method,
    content,
    round_number: int = 0,
) -> ResearchArtifact:
    return ResearchArtifact(
        id=f"eval-{index}",
        run_id=f"eval:{case_id}:{repetition}",
        attempt=1,
        stage=stage,
        role=role,
        round=round_number,
        prompt_version=prompt_version,
        generation_method=generation_method,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


def _evidence_hash(evidence) -> str:
    if evidence.digest is None:
        raise ValueError("sealed evaluation evidence has no digest")
    return evidence.digest


def _graph_prompt_contract_hash(
    *,
    profile: RunProfile,
    case,
    settings: RunSettings,
) -> str:
    prompt_versions = [
        "research-case-bull-v2",
        "research-case-bear-v2",
        "debate-agenda-v2",
        "rebuttal-bull-v2",
        "rebuttal-bear-v2",
        "research-judge-v2",
        "final-committee-v2",
    ]
    if profile is RunProfile.STANDARD:
        prompt_versions.append("risk-review-integrated-v2")
        debate_policy = "one-required-round"
    else:
        prompt_versions.extend(
            (
                "risk-review-aggressive-v2",
                "risk-review-neutral-v2",
                "risk-review-conservative-v2",
            )
        )
        debate_policy = "one-required-plus-two-material-progress-rounds"
    return canonical_hash(
        {
            "contract": "research-graph-v2",
            "profile": profile.value,
            "prompt_versions": sorted(prompt_versions),
            "debate_policy": debate_policy,
            "output_language": report_language_prompt_label(
                settings.output_language
            ),
            "evidence_hash": _evidence_hash(case.evidence),
            "reports": {
                key: report.model_dump(mode="json")
                for key, report in sorted(case.reports.items())
            },
        }
    )


def _analyst_confidence_override(analyst_input) -> float | None:
    if analyst_input.analyst != "social":
        return None
    usable = any(
        item.quality.value != "unavailable"
        and (item.content is not None or item.value is not None)
        for item in analyst_input.evidence.items
    )
    return 0.55 if usable else 0.25


def _persist_progress(
    output_dir: Path,
    records: list[RecordedEvalOutput],
    record: RecordedEvalOutput,
) -> None:
    record_path = (
        output_dir
        / "records"
        / record.variant
        / record.case_id
        / f"{record.repetition}.json"
    )
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = record_path.with_suffix(".json.tmp")
    temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(record_path)
    write_jsonl(output_dir / "records.jsonl", records)


def _write_model(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _print_progress(record: RecordedEvalOutput) -> None:
    print(
        json.dumps(
            {
                "record_id": record.record_id,
                "llm_calls": record.metrics.llm_calls,
                "input_tokens": record.metrics.input_tokens,
                "output_tokens": record.metrics.output_tokens,
                "wall_time_seconds": round(
                    record.metrics.wall_time_seconds,
                    3,
                ),
                "severe_issues": record.severe_issues,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    sys.exit(main())
