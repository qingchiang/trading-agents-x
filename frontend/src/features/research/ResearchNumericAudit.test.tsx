import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import i18n from '../../shared/i18n';
import { buildEvidenceReferenceIndex } from './evidence';
import { Router } from '../../app/router';
import { api, type CalculationRecord, type NumericRequirementCheck, type RunDetail } from '../../shared/api/client';
import ResearchNumericAudit from './ResearchNumericAudit';
import { makeRun, result } from '../../../e2e/fixtures/research';
vi.mock('../../shared/api/client', async original => ({ ...await original<typeof import('../../shared/api/client')>(), api: { run: vi.fn() } }));
import NumericAuditAppendixView from './NumericAuditAppendixView';

const calculation: CalculationRecord = { id: 'calc-1', formula: 'a + b', inputs: { a: 1, b: 2 }, result: 3, unit: 'USD', as_of_date: '2026-08-24', input_evidence_refs: ['ev_baseline'], date_evidence_refs: [], limitations: [], decision_uses: [{ component_path: 'thesis', label: 'Recorded earnings' }] };
const check: NumericRequirementCheck = { requirement_id: 'req-1', calculation_id: 'calc-1', component_path: 'thesis', label: 'Recorded earnings', stated_value: 30, canonical_result: 3, comparison_result: 3, comparison_difference: -27, rounded_stated_value: 30, rounded_canonical_result: 3, unit: 'USD', fraction_digits: 2, display_scale: 'base', formula: 'a + b', inputs: { a: 1, b: 2 }, input_evidence_refs: ['ev_baseline'], date_evidence_refs: [], calculation_status: 'verified', display_status: 'mismatched', issue_codes: ['numeric.display_mismatch'] };
const baseline = { runId: 'full', date: '2026-08-24', calculations: [calculation], appendix: { status: 'complete' as const, requirement_checks: [check], snapshots: [], omitted_components: [] } };

test('labels matching baseline calculations and preserves baseline display problems without claiming a new audit', async () => {
  await i18n.changeLanguage('en');
  render(<Router><NumericAuditAppendixView calculationRecords={[calculation]} baseline={baseline} evidenceIndex={buildEvidenceReferenceIndex(null)} onEvidence={vi.fn()} /></Router>);
  expect(screen.getByText('Audit not recorded')).toBeVisible();
  expect(screen.getByText('Calculation record unchanged from full baseline')).not.toBeVisible();
  expect(screen.getByText('Baseline audit result')).toBeVisible();
  expect(screen.getByText('Display mismatched')).toBeVisible();
  const row = screen.getByText('Recorded earnings', { selector: 'strong' }).closest('article')!;
  expect(within(row).getByText('Thesis')).not.toBeVisible();
  fireEvent.click(within(row).getByText('Formula and Evidence'));
  expect(within(row).getByText('Thesis')).toBeVisible();
  expect(screen.getByText('Calculation record unchanged from full baseline')).toBeVisible();
  expect(screen.getByRole('link', { name: /View baseline audit/ })).toHaveAttribute('href', '/runs/full?view=diagnostics#numeric-audit');
  expect(screen.queryByText('Other verified calculations')).toBeNull();
});

test('does not borrow baseline checks when a calculation keeps its ID but changes inputs or values', async () => {
  await i18n.changeLanguage('en');
  render(<Router><NumericAuditAppendixView calculationRecords={[{ ...calculation, inputs: { a: 1, b: 4 }, result: 5 }]} baseline={baseline} evidenceIndex={buildEvidenceReferenceIndex(null)} onEvidence={vi.fn()} /></Router>);
  expect(screen.queryByText('Baseline audit result')).toBeNull();
  expect(screen.queryByText('Calculation record unchanged from full baseline')).toBeNull();
  expect(screen.getByText('Per-item audit not recorded')).toBeVisible();
});

test('uses an explicitly recorded current audit instead of presenting a baseline check as current', async () => {
  await i18n.changeLanguage('en');
  render(<Router><NumericAuditAppendixView calculationRecords={[calculation]} appendix={{ status: 'complete', snapshots: [], requirement_checks: [{ ...check, display_status: 'matched', stated_value: 3, rounded_stated_value: 3, comparison_difference: 0, issue_codes: [] }] }} baseline={baseline} evidenceIndex={buildEvidenceReferenceIndex(null)} onEvidence={vi.fn()} /></Router>);
  expect(screen.getByText('Current audit result')).toBeVisible();
  expect(screen.getByText('Display matched')).toBeVisible();
  expect(screen.queryByText('Baseline audit result')).toBeNull();
  expect(screen.queryByText('Display mismatched')).toBeNull();
});


test('ignores a late baseline response after switching research records', async () => {
  await i18n.changeLanguage('en');
  const makeDetail = (id: string, baselineId?: string) => ({ run: { ...makeRun(id, 'succeeded'), research_kind: baselineId ? 'incremental' : 'full', full_baseline_run_id: baselineId }, result: { ...result(id), decision: { ...result(id).decision, calculation_records: [calculation] } } }) as unknown as RunDetail;
  let resolveOld!: (value: RunDetail) => void;
  vi.mocked(api.run).mockImplementation(id => id === 'old-base' ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(makeDetail(id)));
  const { rerender } = render(<Router><ResearchNumericAudit detail={makeDetail('old-update', 'old-base')} /></Router>);
  rerender(<Router><ResearchNumericAudit detail={makeDetail('new-update', 'new-base')} /></Router>);
  const link = await screen.findByRole('link', { name: /View baseline audit/ });
  expect(link).toHaveAttribute('href', '/runs/new-base?view=diagnostics#numeric-audit');
  await act(async () => { resolveOld(makeDetail('old-base')); });
  expect(screen.getByRole('link', { name: /View baseline audit/ })).toHaveAttribute('href', '/runs/new-base?view=diagnostics#numeric-audit');
});
