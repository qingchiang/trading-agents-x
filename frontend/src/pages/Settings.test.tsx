import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api, ApiError } from "../api/client";
import i18n from "../i18n";
import Settings from "./Settings";
vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  api: {
    settings: vi.fn(),
    settingsSchema: vi.fn(),
    capabilities: vi.fn(),
    saveSettings: vi.fn(),
    revealCredential: vi.fn(),
    applySettingsImport: vi.fn(),
    previewSettingsImport: vi.fn(),
  },
}));
const view = {
  initialized: true,
  revision: 1,
  values: { output_language: "en", providers: {} },
  sources: { output_language: "default" },
  credentials: { OPENAI_API_KEY: true },
  deployment: { host: "127.0.0.1" },
};
const schema = {
  fields: [
    {
      key: "output_language",
      group: "research",
      kind: "text",
      label: { en: "Report language" },
      description: { en: "Language of new research" },
      default: "en",
      options: [],
      env_names: ["TRADINGAGENTS_OUTPUT_LANGUAGE"],
    },
    {
      key: "providers",
      group: "providers",
      kind: "providers",
      label: { en: "Model services" },
      description: { en: "Connections" },
      default: {},
      options: [],
      env_names: [],
    },
  ],
  providers: { openai: "OpenAI" },
  provider_defaults: { openai: { base_url: "https://api.openai.com/v1" } },
  credential_owners: { OPENAI_API_KEY: "openai" },
  route_options: {},
  tool_options: {},
};
beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.settings).mockResolvedValue(view as never);
  vi.mocked(api.settingsSchema).mockResolvedValue(schema as never);
  vi.mocked(api.capabilities).mockResolvedValue({
    providers: { openai: { configured: true } },
  } as never);
});
test("edits one group and reveals credentials only on demand", async () => {
  vi.mocked(api.saveSettings).mockResolvedValue({
    ...view,
    revision: 2,
  } as never);
  vi.mocked(api.revealCredential).mockResolvedValue({ value: "private-key" });
  render(<Settings />);
  fireEvent.change(await screen.findByLabelText("Report language"), {
    target: { value: "ja" },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
  await waitFor(() =>
    expect(api.saveSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        revision: 1,
        values: { output_language: "ja" },
      }),
    ),
  );
  expect(screen.queryByDisplayValue("private-key")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show key" }));
  expect(await screen.findByDisplayValue("private-key")).toBeVisible();
});
test("retains unsaved values after a failed save and can search by environment name", async () => {
  vi.mocked(api.saveSettings).mockRejectedValue(
    new ApiError(409, "configuration_revision_conflict", "Conflict"),
  );
  render(<Settings />);
  const input = await screen.findByLabelText("Report language");
  fireEvent.change(input, { target: { value: "ja" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Your edits are retained",
  );
  expect(input).toHaveValue("ja");
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "TRADINGAGENTS_OUTPUT_LANGUAGE" },
  });
  expect(screen.getByLabelText("Report language")).toBeVisible();
  expect(screen.queryByText("OpenAI")).not.toBeInTheDocument();
});

test("shows field validation beside the setting without losing edits", async () => {
  vi.mocked(api.saveSettings).mockRejectedValue(
    new ApiError(
      422,
      "validation_error",
      "Invalid configuration",
      undefined,
      undefined,
      [
        {
          location: ["body", "values", "output_language"],
          message: "Use a report language",
        },
      ],
    ),
  );
  render(<Settings />);
  fireEvent.change(await screen.findByLabelText("Report language"), {
    target: { value: "" },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
  expect(await screen.findByText("Use a report language")).toBeVisible();
  expect(screen.getByLabelText("Report language")).toHaveValue("");
});

test("requires an import preview before applying and never displays credential values", async () => {
  vi.mocked(api.settings).mockResolvedValue({
    ...view,
    initialized: false,
  } as never);
  vi.mocked(api.previewSettingsImport).mockResolvedValue({
    revision: 1,
    fingerprint: "reviewed-source",
    values: { output_language: "ja" },
    credentials: { OPENAI_API_KEY: true },
    conflicts: [],
    issues: [],
  });
  vi.mocked(api.applySettingsImport).mockResolvedValue({
    ...view,
    initialized: true,
    revision: 2,
  } as never);
  render(<Settings />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Preview import" }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Apply reviewed import" }),
  );
  await waitFor(() =>
    expect(api.applySettingsImport).toHaveBeenCalledWith({
      revision: 1,
      fingerprint: "reviewed-source",
    }),
  );
  expect(api.revealCredential).not.toHaveBeenCalled();
});
