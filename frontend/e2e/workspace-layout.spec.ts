import configurationFixture from "./fixtures/configuration.json" with { type: "json" };
import { expect, test } from "@playwright/test";
import { makeRun, result, cycleTimeline } from "./fixtures/research";

const sizes = [[390, 844], [768, 1024], [1080, 1920], [1440, 1000], [1920, 1080], [2560, 1440]];
for (const locale of ["en", "zh-CN", "ja"]) {
  test(`workspace reading and cycle layouts in ${locale}`, async ({ page }, info) => {
    test.setTimeout(150_000);
    await page.addInitScript(value => localStorage.setItem("tradingagents-locale", value), locale);
    const name = "DAIICHI SANKYO COMPANY LIMITED";
    const full = { ...makeRun("full", "succeeded", { instrumentName: name, instrumentLocalName: "第一三共" }), is_research_node: true, research_kind: "full" };
    full.request.analysis_date = "2026-07-20";
    const increment = { ...full, request: { ...full.request, analysis_date: "2026-07-24" }, id: "increment", research_kind: "incremental", full_baseline_run_id: "full" };
    const report = result("full");
    const text = locale === "en" ? "Demand remains resilient, while valuation and source coverage require careful interpretation." : locale === "ja" ? "需要は底堅いものの、評価と資料の対象範囲には注意が必要です。" : "需求保持韧性，但估值和资料覆盖仍需审慎解读。";
    report.decision.executive_summary = text;
    report.decision.thesis = text.repeat(5);
    const rangeName = locale === 'en' ? 'Neutral consolidation technical reference range under the recorded assumptions' : locale === 'ja' ? '中立的な推移を想定した技術的参考範囲と前提条件' : '中性震荡技术参考区间及当前已记录的假设条件';
    Object.assign(report.decision.scenarios[0], { reference_ranges: [{ category: 'technical', label: rangeName, unit: 'USD', interpretation: text, low: { value: 100, basis: 'observed', evidence_refs: [], as_of_date: '2026-07-24' }, high: { value: 120, basis: 'observed', evidence_refs: [], as_of_date: '2026-07-24' } }] });
    const node = { id: "full", cycle_id: "full", research_kind: "full" as const, instrument: "NVDA", analysis_date: "2026-07-20", decision: report.decision, is_active: true, is_primary: true, is_cycle_head: false };
    const calculation = { provider: "fixture-feed", fallback: false, adjustment_basis: "split-adjusted close", retrieved_at: "2026-07-24T20:05:00Z", baseline_information_cutoff_at: "2026-07-20T20:00:00Z", target_information_cutoff_at: "2026-07-24T20:00:00Z", start_session: "2026-07-20", end_session: "2026-07-24", start_value: 100, end_value: 112, formula: "(end / start) - 1", unrounded_return: 0.12 };
    const incNode = { ...node, id: "increment", research_kind: "incremental" as const, full_baseline_run_id: "full", analysis_date: "2026-07-24", is_cycle_head: true, decision_outcome: "unchanged", decision_outcome_reason: text, reassessment: { entries: [] }, performance: { stock: { status: "calculated", calculation }, benchmarks: [{ name: "S&P 500", component: { status: "calculated", calculation: { ...calculation, end_value: 104, unrounded_return: 0.04 } }, reported_difference: 0.08 }, { name: "NASDAQ 100", component: { status: "unavailable", reason: "No price observations." } }] } };
    const timeline = { ...cycleTimeline("NVDA", [node, ...[21, 22, 23].map(day => ({ ...incNode, id: `increment-${day}`, analysis_date: `2026-07-${day}`, is_cycle_head: false })), incNode, { ...node, id: "older", cycle_id: "older", analysis_date: "2026-07-10", is_primary: false }], "full") };
    Object.assign(timeline.timeline, { instrument_name: name, instrument_local_name: "第一三共" });
    const summary = { instrument: "NVDA", instrument_name: name, instrument_local_name: "第一三共", primary_cycle_id: "full", primary_head_run_id: "increment", primary_rating: "Hold", primary_confidence: "medium", primary_analysis_date: "2026-07-24", primary_baseline_date: "2026-07-20", primary_thesis: text.repeat(5), full_cycle_count: 2, latest_analysis_date: "2026-07-24" };
    await page.route("**/api/v1/**", async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      let json: unknown = {};
      if (path === "/api/v1/timelines") json = { items: Array.from({ length: 6 }, (_, index) => ({ ...summary, instrument: index ? `NVDA${index}` : "NVDA" })), total: 6, limit: 12, offset: 0 };
      else if (path.startsWith("/api/v1/timelines/")) json = timeline;
      else if (path === "/api/v1/run-groups") json = { items: [{ id: "full", kind: "cycle", instrument: "NVDA", baseline: full, is_primary: true, research_runs: [full, increment], related_tasks: [{ ...increment, id: "pending", status: "running", is_research_node: false }], matched_run_ids: ["full", "increment", "pending"], status_counts: { succeeded: 2, running: 1 } }], total: 1, limit: 12, offset: 0 };
      else if (path.includes("analysis-cutoff")) json = { instrument: "NVDA", max_analysis_date: "2026-07-25", observed_at: "2026-07-25T00:00:00Z", valid_until: "2026-07-26T00:00:00Z" };
      else if (path === "/api/v1/health") json = { status: "ok", queue: { queued: 0, running: 1 } };
      else if (path === "/api/v1/runs") json = { items: [], total: 0, limit: 4, offset: 0 };
      else if (path.endsWith("/artifacts")) json = [];
      else if (path.endsWith("/events")) json = [];
      else if (path.endsWith("/evidence")) json = report.evidence;
      else if (path.startsWith("/api/v1/runs/")) {
        const inc = path.endsWith("increment");
        json = { run: inc ? increment : full, result: report, research_node: inc ? incNode : node, incremental_context: inc ? { analysis_brief: { markdown: `# Brief opening\n\n${text}\n\n## Changes\n\n${text.repeat(30)}`, report_sections: [{ id: "opening", title: "Brief opening", anchor: "brief-opening", source_refs: [] }, { id: "changes", title: "Changes", anchor: "changes", source_refs: [] }], evidence_refs: [], warnings: [] }, full_baseline: { run_id: "full", analysis_date: "2026-07-20", decision: report.decision } } : null };
      }
      else if (path === "/api/v1/instruments/recent") json = [];
      else if (path.includes("/models")) json = { models: [], source: "fixture", fetched_at: "2026-07-25T00:00:00Z" };
      else if (path === "/api/v1/settings") json = configurationFixture.view;
      else if (path === "/api/v1/settings/schema") json = configurationFixture.schema;
      else if (path === "/api/v1/capabilities") json = { defaults: { trash_retention_days: 30, profile: "standard", llm_provider: "openai", quick_model: "quick", deep_model: "deep", quick_reasoning_effort: "provider_default", deep_reasoning_effort: "provider_default", output_language: "en", lan_enabled: false }, profiles: ["fast", "standard", "deep"], analysts: ["market", "news"], output_languages: ["en", "zh-CN", "ja"], providers: { openai: { label: "OpenAI", configured: true, selectable: true, api_key_configured: true } } };
      return route.fulfill({ json });
    });
    for (const [width, height] of sizes) {
      await page.setViewportSize({ width, height });
      for (const [label, path, ready] of [
        ["full", "/timelines/NVDA?node=full", ".decision-hero"],
        ["brief", "/timelines/NVDA?node=increment", ".markdown"],
        ["report", "/timelines/NVDA?node=full&view=reports", ".markdown"],
        ["dashboard", "/", ".research-summary"],
        ["runs", "/runs", ".task-group"],
        ["library", "/timelines", ".research-library-table"],
        ["new", "/runs/new", ".run-form"],
        ["settings", "/settings", ".configuration-group"],
        ["diagnostics", "/runs/full?view=diagnostics", ".diagnostic-block"],
      ]) {
        await page.goto(path);
        await expect(page.locator(ready).first()).toBeVisible();
        const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, header: document.querySelector(".research-header")?.getBoundingClientRect().height ?? 0 }));
        expect(geometry.scroll, `${label} overflow at ${width}`).toBeLessThanOrEqual(geometry.width + 1);
        expect(geometry.header).toBeLessThan(350);
        if (["full", "brief", "report"].includes(label)) {
          const workspace = page.locator(".research-workspace");
          const available = (await workspace.boundingBox())!.width;
          await expect(workspace).toHaveClass(available >= 1100 ? /wide/ : /compact/);
          const reader = (await page.locator(".workspace-reader").boundingBox())!;
          expect(reader.width).toBeGreaterThanOrEqual(Math.min(960, available - (available >= 1100 ? 270 : 2)));
          expect(reader.width).toBeLessThanOrEqual(960);
          const toolbar = (await page.locator(".reading-toolbar").boundingBox())!;
          expect(Math.abs(toolbar.x - reader.x)).toBeLessThan(2);
          expect(Math.abs(toolbar.width - reader.width)).toBeLessThan(2);
          if (label === "brief") {
            await expect(page.getByRole("heading", { name: "Brief opening", exact: true })).toBeVisible();
            expect((await page.getByRole("heading", { name: "Brief opening", exact: true }).boundingBox())!.y).toBeLessThan(height);
            await expect(page.locator(".incremental-reassessment")).toHaveCount(0);
          }
          if (label === "full" && available < 1100) {
            const trigger = page.locator(".reading-navigation-actions button").first();
            await trigger.click();
            await expect(page.locator(".workspace-auxiliary")).toBeVisible();
            await page.keyboard.press("Escape");
            await expect(trigger).toBeFocused();
            await expect(page.locator(".workspace-auxiliary")).toBeHidden();
          }
        }
        if (label === 'full') {
          const range = page.locator('.scenario-range-name').first();
          await expect(range).toHaveText(rangeName);
          const available = (await page.locator('.scenario-reference-heading').first().boundingBox())!.width;
          expect((await range.boundingBox())!.width).toBeGreaterThan(available * 0.95);
          const cards = await page.locator('.scenario-card').all();
          for (let i = 1; i < cards.length; i++) {
            const before = (await cards[i - 1].boundingBox())!;
            const after = (await cards[i].boundingBox())!;
            expect(after.y).toBeGreaterThanOrEqual(before.y + before.height);
          }
        }
        if (label === "new") {
          await page.locator(".advanced-configuration > summary").click();
          await expect(page.locator(".model-group")).toHaveCount(2);
          for (const group of await page.locator(".model-group").all()) {
            await expect(group.locator("legend")).toBeVisible();
            await expect(group.locator("select")).toHaveCount(2);
          }
          await page.locator(".model-groups").scrollIntoViewIfNeeded();
          expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
        }
        if (label === "settings") {
          const checkbox = await page.locator(".interface-preferences input[type=checkbox]").boundingBox();
          expect(checkbox!.width).toBeLessThanOrEqual(24);
          expect(checkbox!.height).toBeLessThanOrEqual(24);
        }
        await page.screenshot({ path: info.outputPath(`${locale}-${width}-${label}.png`), fullPage: false });
      }
    }
    await page.goto("/timelines/NVDA?node=increment");
    await expect(page.locator(".performance-section")).toBeVisible();
    await page.locator(".auxiliary-tabs button").last().click();
    await page.locator(".workspace-contents .floating-navigation-items button").last().click();
    await expect(page).toHaveURL(/#workspace-period-performance/);
    await expect(page.locator(".workspace-contents .floating-navigation-items button").last()).toHaveAttribute("aria-current", "location");
    await page.screenshot({ path: info.outputPath(`${locale}-performance.png`) });
    await expect(page.locator(".performance-value").first()).toContainText("12");
    await page.goto("/timelines/NVDA?node=full&view=reports&report=market");
    const evidence = page.locator(".inline-evidence-ref").first();
    await evidence.click();
    await expect(page.locator(".source-drawer")).toBeVisible();
    await page.locator(".source-drawer > header button").click();
    await expect(evidence).toBeFocused();
    await page.locator(".auxiliary-tabs button").last().click();
    await page.locator(".workspace-contents .floating-navigation-items button").last().click();
    await expect(page).toHaveURL(/#market-risk-lens|#risk-lens/);
    await page.locator(".workspace-reader .reading-toolbar nav a").first().click();
    await page.goBack();
    await expect(page.getByRole("heading", { name: "Risk lens", exact: true })).toBeInViewport();
    await page.goto("/timelines/NVDA?node=full");
    await expect(page.locator(".decision-hero")).toBeVisible();
    await page.locator(".sidebar-collapse-button").click();
    await page.setViewportSize({ width: 1080, height: 1920 });
    await expect(page.locator(".research-workspace")).toHaveClass(/compact/);
    await page.evaluate(() => { document.body.style.zoom = "1.5"; });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: info.outputPath(`${locale}-zoom.png`) });
  });
}
