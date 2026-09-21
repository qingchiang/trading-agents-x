import { afterEach, expect, test, vi } from "vitest";

import { api, ApiError, type RunCreateRequest } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("preserves the typed future-cutoff response on ApiError", async () => {
  const context = {
    instrument: "NVDA",
    market_timezone: "America/New_York",
    market_date: "2026-09-03",
    max_analysis_date: "2026-09-03",
    observed_at: "2026-09-04T01:00:00Z",
    valid_until: "2026-09-04T04:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: {
      code: "future_analysis_cutoff",
      message: "The requested analysis date is in the future for this market.",
    },
    requested_analysis_date: "2026-09-04",
    context,
  }), {
    status: 422,
    headers: { "Content-Type": "application/json" },
  })));

  const request = {
    ticker: "NVDA",
    analysis_date: "2026-09-04",
  } as RunCreateRequest;
  const error = await api.createRun(request, "test-key").catch((cause) => cause);

  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({
    status: 422,
    code: "future_analysis_cutoff",
    requestedAnalysisDate: "2026-09-04",
    context,
  });
});
