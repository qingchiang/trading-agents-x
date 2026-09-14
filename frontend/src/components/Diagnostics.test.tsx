import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import i18n from "../i18n";
import RunMetricsPanel from "./RunMetricsPanel";
import NumericAuditAppendixView from "./NumericAuditAppendixView";
import { buildEvidenceReferenceIndex } from "../evidence";

test("shows recorded zero separately from missing metrics and never invents a completed audit", async () => {
  await i18n.changeLanguage('en');
  const { container } = render(<><RunMetricsPanel metrics={{ llm_calls: 0 }} attempts={[]} events={[]} artifacts={[]} /><NumericAuditAppendixView calculationRecords={[]} calculationUses={new Map()} evidenceIndex={buildEvidenceReferenceIndex(null)} onEvidence={() => {}} /></>);
  const calls = screen.getByText('LLM calls').parentElement!;
  expect(within(calls).getByText('0')).toBeVisible();
  const tools = screen.getByText('Tool calls').parentElement!;
  expect(within(tools).getByText('Not recorded')).toBeVisible();
  expect(screen.getByText('Audit not recorded')).toBeVisible();
  expect(container.querySelector('.run-metrics-disclosure')).toBeNull();
});

test("keeps conflicting snapshot values separate and filters all recorded events before paging", async () => {
  await i18n.changeLanguage('en');
  const { default: DiagnosticSnapshot } = await import('./DiagnosticSnapshot');
  const { default: DiagnosticEvents } = await import('./DiagnosticEvents');
  const { fireEvent } = await import('@testing-library/react');
  const events = Array.from({ length: 31 }, (_, index) => ({ run_id: 'run', attempt: 1, sequence: index + 1, created_at: '2026-09-01T12:00:00Z', event_type: index === 30 ? 'node.failed' : 'node.completed', node: 'analyst.market', payload: { message: index === 30 ? 'Final failure' : `Observed ${index}` } }));
  render(<><DiagnosticSnapshot label="Configuration snapshot" snapshot={{ quick_model: 'configured-quick', temperature: 0 }} /><DiagnosticSnapshot label="Method snapshot" snapshot={{ quick_model: 'recorded-quick' }} /><DiagnosticEvents events={events} /></>);
  expect(within(screen.getByRole('heading', { name: 'Configuration snapshot' }).closest('section')!).getByText('configured-quick')).toBeVisible();
  expect(within(screen.getByRole('heading', { name: 'Method snapshot' }).closest('section')!).getByText('recorded-quick')).toBeVisible();
  expect(screen.queryByText('Final failure')).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: 'Event type' }), { target: { value: 'node.failed' } });
  expect(screen.getByText('Final failure')).toBeVisible();
  expect(screen.queryByText('Observed 0')).toBeNull();
});
