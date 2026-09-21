import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  api,
  ApiError,
  type Capabilities,
  type FullBaselineCandidate,
  type ProviderModelCatalog,
  type RunView,
} from "../api/client";
import i18n from "../i18n";
import { Router, usePathname } from "../router";
import NewRun from "./NewRun";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: {
    analysisCutoffContext: vi.fn(),
    capabilities: vi.fn(),
    connectionModels: vi.fn(),
    createRun: vi.fn(),
    run: vi.fn(),
    creationTemplate: vi.fn(),
    timeline: vi.fn(),
    baselineCandidates: vi.fn(),
    recentInstruments: vi.fn(),
  },
}));

const capabilities = {
  profiles: ["fast", "standard", "deep"],
  analysts: ["market", "social", "news", "fundamentals"],
  output_languages: ["en", "zh-CN", "ja"],
  connections: Object.fromEntries(["openai", "ollama"].map(id => [id, {connection: {id, name: id === "openai" ? "OpenAI" : "Ollama", preset: id, transport: {kind: "chat_completions", base_url: "https://example.invalid/v1"}}, selectable: true, credentials: {}, missing_fields: [], reasoning_efforts: ["provider_default", "low", "medium", "high"]}])),
  providers: {
    openai: {
      label: "OpenAI",
      api_key_required: true,
      api_key_configured: true,
      configured: true,
      selectable: true,
      unavailable_reason: null,
      model_discovery_supported: true,
    },
    anthropic: {
      label: "Anthropic",
      api_key_required: true,
      api_key_configured: false,
      configured: false,
      selectable: false,
      unavailable_reason: "api_key_missing",
      model_discovery_supported: true,
    },
    ollama: {
      label: "Ollama",
      api_key_required: false,
      api_key_configured: null,
      configured: true,
      selectable: true,
      unavailable_reason: null,
      model_discovery_supported: true,
    },
  },
  defaults: {
    profile: "standard",
    models: { quick: { connection_id: "openai", model: "gpt-5.4-mini", reasoning_effort: "low" }, deep: { connection_id: "openai", model: "gpt-5.5", reasoning_effort: "high" } },
    output_language: "zh-CN",
    lan_enabled: false,
    trash_retention_days: 30,
  },
} as Capabilities;

const modelCatalog = {
  provider: "openai",
  models: [
    {
      id: "gpt-5.4-mini",
      label: "Quick",
      compatibility: "supported",
      reasoning_efforts: ["provider_default", "low", "high"],
      default_roles: ["quick"],
    },
    {
      id: "gpt-5.5",
      label: "Deep",
      compatibility: "supported",
      reasoning_efforts: ["provider_default", "medium", "high"],
      default_roles: ["deep"],
    },
    {
      id: "future-model",
      label: "Future model",
      compatibility: "unknown",
      reasoning_efforts: ["provider_default"],
      default_roles: [],
    },
  ],
  source: "live",
  fetched_at: "2026-07-28T00:00:00Z",
  stale: false,
  warning: null,
} as ProviderModelCatalog;

const analysisCutoffContext = {
  instrument: "NVDA",
  market_timezone: "America/New_York",
  market_date: "2026-08-29",
  max_analysis_date: "2026-08-29",
  observed_at: "2026-08-29T03:00:00Z",
  valid_until: "2026-08-30T04:00:00Z",
};

function baseline(id: string, cycleWarning = false): FullBaselineCandidate {
  return {
    id,
    analysis_date: "2026-07-20",
    is_primary: true,
    rating: "Overweight",
    confidence: "high",
    instrument_name: "NVIDIA Corporation",
    instrument_local_name: "英伟达",
    thesis: "Durable demand supports the current view.",
    cycle_warning: cycleWarning,
  };
}

function NewRunRoutes() {
  const pathname = usePathname();
  return pathname === "/runs/new" ? <NewRun /> : <div>Run opened</div>;
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-08-29T12:00:00+09:00"));
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
  vi.mocked(api.analysisCutoffContext).mockImplementation(async (instrument) => ({
    ...analysisCutoffContext,
    instrument: instrument.trim().toUpperCase(),
  }));
  vi.mocked(api.connectionModels).mockResolvedValue(modelCatalog);
  vi.mocked(api.recentInstruments).mockResolvedValue([]);
  vi.mocked(api.baselineCandidates).mockResolvedValue({
    instrument: "NVDA",
    before: "2026-07-24",
    items: [],
  });
});

test.each([
  ["ahead", "2026-08-28"],
  ["behind", "2026-08-30"],
])("uses the server market date when the browser date is %s", async (_, marketDate) => {
  vi.mocked(api.analysisCutoffContext).mockResolvedValue({
    ...analysisCutoffContext,
    market_date: marketDate,
    max_analysis_date: marketDate,
  });

  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );

  await screen.findByDisplayValue("gpt-5.4-mini");
  const date = screen.getByLabelText(/^Analysis date/);
  expect(date).toBeDisabled();
  expect(date).toHaveValue("");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });

  await waitFor(() => expect(date).toHaveValue(marketDate));
  expect(date).toHaveAttribute("max", marketDate);
  expect(api.baselineCandidates).toHaveBeenCalledWith("NVDA", marketDate);
});

test("preserves a valid manual date and resets it when a new market makes it future", async () => {
  vi.mocked(api.analysisCutoffContext).mockImplementation(async (instrument) => {
    const normalized = instrument.trim().toUpperCase();
    return normalized === "7203.T"
      ? {
          ...analysisCutoffContext,
          instrument: normalized,
          market_timezone: "Asia/Tokyo",
          market_date: "2026-08-28",
          max_analysis_date: "2026-08-28",
        }
      : { ...analysisCutoffContext, instrument: normalized };
  });
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  const ticker = screen.getByLabelText(/^Ticker/);
  const date = screen.getByLabelText(/^Analysis date/);
  fireEvent.change(ticker, { target: { value: "NVDA" } });
  await waitFor(() => expect(date).toHaveValue("2026-08-29"));

  fireEvent.change(date, { target: { value: "2026-08-20" } });
  fireEvent.change(ticker, { target: { value: "7203.T" } });
  expect(date).toHaveValue("");
  await waitFor(() => expect(date).toHaveAttribute("max", "2026-08-28"));
  expect(date).toHaveValue("2026-08-20");

  fireEvent.change(ticker, { target: { value: "NVDA" } });
  await waitFor(() => expect(date).toHaveAttribute("max", "2026-08-29"));
  fireEvent.change(date, { target: { value: "2026-08-29" } });
  fireEvent.change(ticker, { target: { value: "7203.T" } });

  await waitFor(() => expect(date).toHaveValue("2026-08-28"));
  expect(
    screen.getByText(/selected date is in the future for this market/i),
  ).toBeVisible();
  expect(api.createRun).not.toHaveBeenCalled();
});

test("ignores a stale cutoff response after the ticker changes", async () => {
  let resolveNvda!: (value: typeof analysisCutoffContext) => void;
  let resolveAapl!: (value: typeof analysisCutoffContext) => void;
  vi.mocked(api.analysisCutoffContext).mockImplementation(
    (instrument) => new Promise((resolve) => {
      if (instrument === "NVDA") resolveNvda = resolve;
      if (instrument === "AAPL") resolveAapl = resolve;
    }),
  );
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  const ticker = screen.getByLabelText(/^Ticker/);
  const date = screen.getByLabelText(/^Analysis date/);
  fireEvent.change(ticker, { target: { value: "NVDA" } });
  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledWith("NVDA"));
  fireEvent.change(ticker, { target: { value: "AAPL" } });
  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledWith("AAPL"));

  resolveAapl({
    ...analysisCutoffContext,
    instrument: "AAPL",
    market_date: "2026-08-28",
    max_analysis_date: "2026-08-28",
  });
  await waitFor(() => expect(date).toHaveValue("2026-08-28"));
  resolveNvda({ ...analysisCutoffContext, instrument: "NVDA" });
  await Promise.resolve();
  await Promise.resolve();

  expect(date).toHaveValue("2026-08-28");
  expect(screen.getByText(/Market date: 2026-08-28/)).toBeVisible();
});

test("keeps submission unavailable when the cutoff context fails", async () => {
  vi.mocked(api.analysisCutoffContext).mockRejectedValue(new Error("offline"));
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });

  expect(await screen.findByText(/market date could not be loaded/i)).toBeVisible();
  expect(screen.getByLabelText(/^Analysis date/)).toBeDisabled();
  expect(screen.getByRole("button", { name: /Queue research/ })).toBeDisabled();
  expect(api.baselineCandidates).not.toHaveBeenCalled();
});

test("refreshes the cutoff when the page becomes visible and at valid_until", async () => {
  vi.mocked(api.analysisCutoffContext)
    .mockResolvedValueOnce({
      ...analysisCutoffContext,
      valid_until: "2026-08-29T03:00:00.025Z",
    })
    .mockResolvedValue(analysisCutoffContext);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledTimes(2));

  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledTimes(3));
});

test("refreshes from the server validity window when the browser clock is wrong", async () => {
  vi.setSystemTime(new Date("2027-08-29T03:00:00Z"));
  vi.mocked(api.analysisCutoffContext).mockResolvedValue({
    ...analysisCutoffContext,
    observed_at: "2026-08-29T03:00:00Z",
    valid_until: "2026-08-29T03:00:00.100Z",
  });
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledTimes(1));

  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 30));
  });
  expect(api.analysisCutoffContext).toHaveBeenCalledTimes(1);
  await waitFor(
    () => expect(api.analysisCutoffContext).toHaveBeenCalledTimes(2),
    { timeout: 500 },
  );
});

test("rechecks the server cutoff immediately before submitting an automatic date", async () => {
  vi.mocked(api.analysisCutoffContext)
    .mockResolvedValueOnce({
      ...analysisCutoffContext,
      market_date: "2026-08-28",
      max_analysis_date: "2026-08-28",
    })
    .mockResolvedValueOnce(analysisCutoffContext);
  vi.mocked(api.createRun).mockResolvedValue({ id: "fresh-cutoff-run" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toHaveValue("2026-08-28"));
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0]).toMatchObject({
    ticker: "NVDA",
    analysis_date: "2026-08-29",
  });
});

test("keeps submission unavailable when the submit-time cutoff refresh fails", async () => {
  vi.mocked(api.analysisCutoffContext)
    .mockResolvedValueOnce(analysisCutoffContext)
    .mockRejectedValueOnce(new Error("offline"));
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  const date = screen.getByLabelText(/^Analysis date/);
  const submit = screen.getByRole("button", { name: /Queue research/ });
  await waitFor(() => expect(date).toBeEnabled());
  fireEvent.click(submit);

  expect(await screen.findByText(/market date could not be loaded/i)).toBeVisible();
  expect(date).toBeDisabled();
  expect(submit).toBeDisabled();
  expect(api.createRun).not.toHaveBeenCalled();
});

test("locks the instrument cutoff while the submit-time context is pending", async () => {
  let resolveSubmitContext!: (value: typeof analysisCutoffContext) => void;
  vi.mocked(api.analysisCutoffContext)
    .mockResolvedValueOnce(analysisCutoffContext)
    .mockImplementationOnce(() => new Promise((resolve) => {
      resolveSubmitContext = resolve;
    }));
  vi.mocked(api.createRun).mockResolvedValue({ id: "locked-cutoff-run" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  const ticker = screen.getByLabelText(/^Ticker/);
  const date = screen.getByLabelText(/^Analysis date/);
  fireEvent.change(ticker, { target: { value: "NVDA" } });
  await waitFor(() => expect(date).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.analysisCutoffContext).toHaveBeenCalledTimes(2));
  expect(ticker).toBeDisabled();
  expect(date).toBeDisabled();

  await act(async () => resolveSubmitContext(analysisCutoffContext));
  await waitFor(() => expect(api.createRun).toHaveBeenCalledTimes(1));
});

test("requires confirmation instead of submitting when a refreshed cutoff invalidates a manual date", async () => {
  vi.mocked(api.analysisCutoffContext)
    .mockResolvedValueOnce({
      ...analysisCutoffContext,
      market_date: "2026-08-30",
      max_analysis_date: "2026-08-30",
    })
    .mockResolvedValueOnce({
      ...analysisCutoffContext,
      market_date: "2026-08-28",
      max_analysis_date: "2026-08-28",
    });
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  const date = screen.getByLabelText(/^Analysis date/);
  await waitFor(() => expect(date).toHaveValue("2026-08-30"));
  fireEvent.change(date, { target: { value: "2026-08-29" } });
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(date).toHaveValue("2026-08-28"));
  expect(api.createRun).not.toHaveBeenCalled();
  expect(screen.getByText(/review it before submitting/i)).toBeVisible();
});

test("shows the latest market context when the server rejects a crossed-midnight date", async () => {
  const rejectedContext = {
    ...analysisCutoffContext,
    market_date: "2026-08-28",
    max_analysis_date: "2026-08-28",
  };
  vi.mocked(api.createRun).mockRejectedValue(new ApiError(
    422,
    "future_analysis_cutoff",
    "future cutoff",
    rejectedContext,
    "2026-08-29",
  ));
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toHaveValue("2026-08-28"));
  expect(screen.getByText(/later than the current market date/i)).toBeVisible();
  expect(api.createRun).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("Run opened")).not.toBeInTheDocument();
});

afterEach(() => {
  vi.useRealTimers();
});

test("lets a user choose an informative Full Baseline for Incremental research", async () => {
  vi.mocked(api.baselineCandidates).mockResolvedValue({
    instrument: "NVDA",
    before: "2026-08-29",
    items: [baseline("full-baseline")],
  });
  vi.mocked(api.createRun).mockResolvedValue({ id: "incremental-run" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  const incremental = screen.getByRole("radio", {
    name: /Incremental research/,
  });
  await waitFor(() => expect(incremental).not.toBeDisabled());
  fireEvent.click(incremental);

  await screen.findByRole("option", { name: /2026-07-20/ });
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() =>
    expect(vi.mocked(api.createRun)).toHaveBeenCalledWith(
      expect.objectContaining({
        research_kind: "incremental",
        full_baseline_run_id: "full-baseline",
      }),
      expect.any(String),
    ),
  );
});

test("defaults generic research to Full while selecting a baseline for optional updates", async () => {
  vi.mocked(api.baselineCandidates).mockResolvedValue({
    instrument: "NVDA",
    before: "2026-08-29",
    items: [baseline("full-baseline")],
  });

  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });

  const incremental = screen.getByRole("radio", {
    name: /Incremental research/,
  });
  await waitFor(() => expect(incremental).not.toBeChecked());
  fireEvent.click(incremental);
  await screen.findByRole("option", { name: /Primary Cycle · 2026-07-20/ });
  expect(
    screen.getByRole("option", {
      name: /Primary Cycle · 2026-07-20 · Overweight · High confidence/,
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "An active Full Baseline is available; Incremental research is recommended, while Full remains available.",
    ),
  ).toBeVisible();
});

test("recommends Full research for a warned primary cycle without disabling Incremental", async () => {
  vi.mocked(api.baselineCandidates).mockResolvedValue({
    instrument: "NVDA",
    before: "2026-08-29",
    items: [baseline("full-baseline", true)],
  });

  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });

  const full = screen.getByRole("radio", { name: /Full research/ });
  const incremental = screen.getByRole("radio", {
    name: /Incremental research/,
  });
  await waitFor(() => expect(incremental).toBeEnabled());
  expect(full).toBeChecked();
  expect(incremental).toBeEnabled();
  expect(
    screen.getByText(
      "A Primary Cycle warning recommends Full research; Incremental research remains available.",
    ),
  ).toBeVisible();
  fireEvent.click(incremental);
  expect(incremental).toBeChecked();
});

test("keeps the user's Full choice and ignores a stale baseline response", async () => {
  let resolveNvda: (value: never) => void;
  let resolveAapl: (value: never) => void;
  vi.mocked(api.baselineCandidates).mockImplementation(
    (instrument) =>
      new Promise((resolve) => {
        if (instrument === "NVDA") resolveNvda = resolve;
        if (instrument === "AAPL") resolveAapl = resolve;
      }) as never,
  );

  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  const ticker = screen.getByLabelText(/^Ticker/);
  fireEvent.change(ticker, { target: { value: "NVDA" } });
  await waitFor(() => expect(api.baselineCandidates).toHaveBeenCalledWith("NVDA", "2026-08-29"));
  fireEvent.change(ticker, { target: { value: "AAPL" } });
  await waitFor(() => expect(api.baselineCandidates).toHaveBeenCalledWith("AAPL", "2026-08-29"));

  resolveAapl!({ instrument: "AAPL", before: "2026-08-29", items: [] } as never);
  const incremental = screen.getByRole("radio", {
    name: /Incremental research/,
  });
  await waitFor(() => expect(incremental).toBeDisabled());
  resolveNvda!({ instrument: "NVDA", before: "2026-08-29", items: [baseline("stale-full-baseline")] } as never);

  await Promise.resolve();
  await Promise.resolve();
  expect(incremental).toBeDisabled();
  expect(screen.queryByText(/stale-full-baseline/)).not.toBeInTheDocument();
});

test("reuses the idempotency key when a browser submission is retried", async () => {
  vi.mocked(api.createRun)
    .mockRejectedValueOnce(new Error("temporary network error"))
    .mockResolvedValueOnce({ id: "run-2" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });

  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));
  await screen.findByText("temporary network error");
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await screen.findByText("Run opened");
  expect(api.createRun).toHaveBeenCalledTimes(2);
  const firstKey = vi.mocked(api.createRun).mock.calls[0][1];
  const secondKey = vi.mocked(api.createRun).mock.calls[1][1];
  expect(secondKey).toBe(firstKey);
  expect(vi.mocked(api.createRun).mock.calls[1][0]).toMatchObject({
    ticker: "NVDA",
    models: {quick: {reasoning_effort: "low"}, deep: {reasoning_effort: "high"}},
    output_language: "zh-CN",
  });
  expect(vi.mocked(api.createRun).mock.calls[1][0]).not.toHaveProperty(
    "provenance",
  );
  expect(vi.mocked(api.createRun).mock.calls[1][0]).toMatchObject({
    make_primary: true,
  });
});

test.each([
  ["unsupported_instrument", "This symbol is not a supported listed equity."],
  [
    "instrument_eligibility_unavailable",
    "This symbol could not be verified right now. Please retry later.",
  ],
])("renders the distinct admission message for %s", async (code, message) => {
  vi.mocked(api.createRun).mockRejectedValueOnce({ code });
  const { unmount } = render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await screen.findByText(message);
  unmount();
});

test("keeps UI locale and report output language independent", async () => {
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-3" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "7203.T" },
  });
  fireEvent.change(screen.getByLabelText(/^Report language/), {
    target: { value: "ja" },
  });
  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0].output_language).toBe("ja");
  expect(i18n.language).toBe("en");
});

test("offers recent instruments with stable browser-autocomplete metadata", async () => {
  vi.mocked(api.recentInstruments).mockResolvedValue([
    {
      ticker: "7203.T",
      instrument_name: "Toyota Motor Corporation",
      last_used_at: "2026-07-28T00:00:00Z",
    },
  ]);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );

  const ticker = await screen.findByLabelText(/^Ticker/);
  expect(ticker).toHaveAttribute("id", "new-run-ticker");
  expect(ticker).toHaveAttribute("name", "ticker");
  expect(ticker).toHaveAttribute("autocomplete", "on");
  expect(ticker).toHaveAttribute("list", "recent-instruments");
  await waitFor(() =>
    expect(
      document.querySelector(
        'datalist#recent-instruments option[value="7203.T"]',
      ),
    ).toHaveAttribute("label", "Toyota Motor Corporation"),
  );
});

test("loads a terminal run as an editable template and preserves custom values", async () => {
  vi.mocked(api.creationTemplate).mockResolvedValue({
      run_id: "source-run",
      status: "succeeded",
      research_kind: "full",
      request: {
        ticker: "7203.T",
        analysis_date: "2026-07-24",

        profile: "deep",
        analysts: ["market", "news"],
        models: { quick: { connection_id: "openai", model: "source-quick-model", reasoning_effort: "source-low" }, deep: { connection_id: "openai", model: "source-deep-model", reasoning_effort: "source-high" } },
        output_language: "Use concise Simplified Chinese",
      },
  });
  vi.mocked(api.createRun).mockResolvedValue({ id: "templated-run" } as RunView);

  render(
    <Router initialPath="/runs/new?from_run=source-run">
      <NewRunRoutes />
    </Router>,
  );

  expect(await screen.findByDisplayValue("7203.T")).toBeVisible();
  expect(within(screen.getByRole("group", { name: "Quick connection" })).getByLabelText("Model ID (manual input supported)")).toHaveValue(
    "source-quick-model",
  );
  expect(within(screen.getByRole("group", { name: "Deep connection" })).getByLabelText("Model ID (manual input supported)")).toHaveValue(
    "source-deep-model",
  );
  expect(within(screen.getByRole("group", { name: "Quick connection" })).getByLabelText("Reasoning effort")).toHaveValue("source-low");
  expect(within(screen.getByRole("group", { name: "Deep connection" })).getByLabelText("Reasoning effort")).toHaveValue("source-high");
  expect(screen.getByLabelText(/^Report language/)).toHaveValue(
    "Use concise Simplified Chinese",
  );
  expect(screen.getByRole("link", { name: "Execution details" })).toHaveAttribute(
    "href",
    "/runs/source-run",
  );

  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0]).toMatchObject({
    ticker: "7203.T",
    profile: "deep",
    analysts: ["market", "news"],
    models: { quick: { connection_id: "openai", model: "source-quick-model", reasoning_effort: "source-low" }, deep: { connection_id: "openai", model: "source-deep-model", reasoning_effort: "source-high" } },
    output_language: "Use concise Simplified Chinese",
    source_run_id: "source-run",
  });
});

test("locks the update intent to Incremental fields and keeps the root Full baseline", async () => {
  vi.mocked(api.creationTemplate).mockResolvedValue({
    run_id: "increment-source",
    status: "succeeded",
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    request: {
      ticker: "NVDA",
      analysis_date: "2026-07-24",

      profile: "deep",
      analysts: ["market", "news"],
      models: { quick: { connection_id: "openai", model: "legacy-quick", reasoning_effort: "low" }, deep: { connection_id: "openai", model: "gpt-5.5", reasoning_effort: "high" } },
      output_language: "en",
      research_kind: "incremental",
      full_baseline_run_id: "full-baseline",
    },
  });
  vi.mocked(api.baselineCandidates).mockResolvedValue({
    instrument: "NVDA",
    before: "2026-08-29",
    items: [baseline("full-baseline")],
  });
  vi.mocked(api.createRun).mockResolvedValue({ id: "next-increment" } as RunView);

  render(
    <Router initialPath="/runs/new?intent=update&from_run=increment-source&full_baseline_run_id=full-baseline">
      <NewRunRoutes />
    </Router>,
  );

  expect(
    await screen.findByText("This flow updates the selected Full Research baseline."),
  ).toBeVisible();
  expect(screen.queryByRole("radio", { name: /Full research/ })).not.toBeInTheDocument();
  expect(screen.queryByText("Research profile")).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^Quick model/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^Quick reasoning/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Advanced configuration"));
  expect(screen.getByText("Update scope")).toBeVisible();
  expect(
    await screen.findByRole("option", {
      name: /Primary Cycle · 2026-07-20 · Overweight · High confidence/,
    }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Full Baseline")).toBeDisabled();
  expect(screen.getByLabelText(/^Analysis date/)).toHaveValue("2026-08-29");

  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));
  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0]).toMatchObject({
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    source_run_id: "increment-source",
    analysis_date: "2026-08-29",
  });
});

test("blocks a locked update when its requested Full baseline is no longer eligible", async () => {
  vi.mocked(api.creationTemplate).mockResolvedValue({
    run_id: "increment-source",
    status: "succeeded",
    research_kind: "incremental",
    full_baseline_run_id: "expired-baseline",
    request: {
      ticker: "NVDA",
      analysis_date: "2026-07-24",

      profile: "deep",
      analysts: ["market"],
      models: { quick: { connection_id: "openai", model: "legacy-quick", reasoning_effort: "low" }, deep: { connection_id: "openai", model: "gpt-5.5", reasoning_effort: "high" } },
      output_language: "en",
      research_kind: "incremental",
      full_baseline_run_id: "expired-baseline",
    },
  });
  vi.mocked(api.baselineCandidates).mockResolvedValue({
    instrument: "NVDA",
    before: "2026-08-29",
    items: [baseline("different-baseline")],
  });

  render(
    <Router initialPath="/runs/new?intent=update&from_run=increment-source&full_baseline_run_id=expired-baseline">
      <NewRunRoutes />
    </Router>,
  );

  expect(
    await screen.findByText(
      "The requested Full Baseline is no longer active, compatible, or earlier than this update date.",
    ),
  ).toBeVisible();
  expect(screen.getByLabelText("Full Baseline")).toHaveValue("");
  expect(screen.getByLabelText("Full Baseline")).toBeDisabled();
  expect(screen.getByRole("button", { name: /Queue research/ })).toBeDisabled();
  expect(api.createRun).not.toHaveBeenCalled();
});

test("preserves unavailable source connections until a replacement is chosen", async () => {
  vi.mocked(api.creationTemplate).mockResolvedValue({
      run_id: "unavailable-source",
      status: "failed",
      research_kind: "full",
      request: {
        ticker: "NVDA",
        analysis_date: "2026-07-24",

        profile: "standard",
        analysts: ["market"],
        models: { quick: { connection_id: "anthropic", model: "claude-source-quick", reasoning_effort: "low" }, deep: { connection_id: "anthropic", model: "claude-source-deep", reasoning_effort: "high" } },
        output_language: "ja",
      },
  });

  render(
    <Router initialPath="/runs/new?from_run=unavailable-source">
      <NewRunRoutes />
    </Router>,
  );

  expect(await screen.findByDisplayValue("NVDA")).toBeVisible();
  expect(screen.getByLabelText("Shared connection")).toHaveValue("anthropic");
  expect(screen.getByDisplayValue("claude-source-quick")).toBeInTheDocument();
  expect(screen.getByDisplayValue("claude-source-deep")).toBeInTheDocument();
  expect(screen.getAllByText(/Unavailable connection/).length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: /Queue research/ })).toBeDisabled();
});

test("shows a concise Simplified Chinese label", async () => {
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );

  expect(
    await screen.findByRole("option", { name: "简体中文" }),
  ).toHaveValue("zh-CN");
  expect(
    screen.queryByRole("option", { name: /中国大陆/ }),
  ).not.toBeInTheDocument();
});

test("preserves a configured custom report language", async () => {
  const customLanguage = "Simplified Chinese (简体中文, zh-CN)";
  vi.mocked(api.capabilities).mockResolvedValue({
    ...capabilities,
    defaults: {
      ...capabilities.defaults,
      output_language: customLanguage,
    },
  });
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-custom" } as RunView);

  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByDisplayValue("gpt-5.4-mini");

  const language = screen.getByLabelText(/^Report language/);
  expect(language).toHaveValue(customLanguage);
  expect(
    screen.getByRole("option", {
      name: `Configured default: ${customLanguage}`,
    }),
  ).toHaveValue(customLanguage);

  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0].output_language).toBe(
    customLanguage,
  );
});

test("refreshes a connection catalog without blocking manual model entry", async () => {
  vi.mocked(api.connectionModels).mockRejectedValue(new Error("Catalog temporarily unavailable"));
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-custom" } as RunView);
  render(<Router initialPath="/runs/new"><NewRunRoutes /></Router>);
  await screen.findByDisplayValue("gpt-5.4-mini");
  expect(api.connectionModels).not.toHaveBeenCalled();
  expect(screen.queryByRole("option", {name: "Anthropic"})).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", {name: "Refresh models"})[0]);
  expect(await screen.findByText("Catalog temporarily unavailable")).toBeInTheDocument();
  expect(api.connectionModels).toHaveBeenCalledWith("openai", true);
  fireEvent.change(screen.getByLabelText("Shared connection"), {target: {value: "ollama"}});
  const quick = within(screen.getByRole("group", {name: "Quick connection"}));
  const deep = within(screen.getByRole("group", {name: "Deep connection"}));
  fireEvent.change(quick.getByLabelText("Model ID (manual input supported)"), {target: {value: "quick-local"}});
  fireEvent.change(deep.getByLabelText("Model ID (manual input supported)"), {target: {value: "deep-local"}});
  fireEvent.change(screen.getByLabelText(/^Ticker/), {target: {value: "NVDA"}});
  await waitFor(() => expect(screen.getByLabelText(/^Analysis date/)).toBeEnabled());
  fireEvent.click(screen.getByRole("button", {name: /Queue research/}));
  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0]).toMatchObject({models: {
    quick: {connection_id: "ollama", model: "quick-local", reasoning_effort: "provider_default"},
    deep: {connection_id: "ollama", model: "deep-local", reasoning_effort: "provider_default"},
  }});
});
