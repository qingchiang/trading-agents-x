import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api, ApiError } from "../../shared/api/client";
import i18n from "../../shared/i18n";
import { Router } from "../../app/router";
import Settings from "./Settings";
vi.mock("../../shared/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../shared/api/client")>()),
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
  connections: { main: { connection: { id: "main", name: "OpenAI", enabled: true, transport: { kind: "responses", base_url: "https://api.openai.com/v1" } }, credentials: { api_key: true }, missing_fields: [], selectable: true } },
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
test("retired interface category opens connections without duplicate browser preferences", async () => {
  render(<Router initialPath="/settings/interface"><Settings /></Router>);
  expect(await screen.findByRole("button", { name: "Edit" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Model connections" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("link", { name: "Interface" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Interface language")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Collapse sidebar")).not.toBeInTheDocument();
});
test("edits one group and reveals credentials only on demand", async () => {
  vi.mocked(api.saveSettings).mockResolvedValue({
    ...view,
    revision: 2,
  } as never);
  vi.mocked(api.revealCredential).mockResolvedValue({ value: "private-key" });
  render(<Router initialPath="/settings/research"><Settings /></Router>);
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
  fireEvent.click(screen.getByRole("link", { name: "Model connections" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Show key" }));
  expect(await screen.findByDisplayValue("private-key")).toBeVisible();
});
test("retains unsaved values after a failed save and can search by environment name", async () => {
  vi.mocked(api.saveSettings).mockRejectedValue(
    new ApiError(409, "configuration_revision_conflict", "Conflict"),
  );
  render(<Router initialPath="/settings/research"><Settings /></Router>);
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
  expect(screen.queryByRole("heading", { name: "OpenAI" })).not.toBeInTheDocument();
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
  render(<Router initialPath="/settings/research"><Settings /></Router>);
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
  render(<Router initialPath="/settings/research"><Settings /></Router>);
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

test("category navigation preserves drafts and does not expand every section", async () => {
  render(<Router initialPath="/settings"><Settings /></Router>);
  expect(await screen.findByRole("heading", { name: "OpenAI" })).toBeVisible();
  expect(screen.queryByLabelText("Report language")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Research defaults" }));
  fireEvent.change(await screen.findByLabelText("Report language"), { target: { value: "ja" } });
  fireEvent.click(screen.getByRole("link", { name: "Model connections" }));
  expect(screen.queryByLabelText("Report language")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Research defaults" }));
  expect(screen.getByLabelText("Report language")).toHaveValue("ja");
  expect(api.saveSettings).not.toHaveBeenCalled();
});

test("creates and deletes a custom connection with scoped credentials", async () => {
  const preset = { id: "preset", name: "Compatible endpoint", preset: "openai_compatible", enabled: true, compatibility: "openai_compatible", discovery: "openai_compatible", key_required: false, transport: { kind: "chat_completions", base_url: null }, template: {}, reasoning_defaults: {} };
  vi.mocked(api.settingsSchema).mockResolvedValue({ ...schema, presets: { openai_compatible: preset } } as never);
  let current = structuredClone(view);
  vi.mocked(api.saveSettings).mockImplementation(async patch => {
    const change = patch.connection_changes![0];
    const entries = current.connections as Record<string, unknown>;
    if (change.action === "delete") delete entries[change.id];
    else entries[change.id] = { connection: { ...preset, ...change, credentials: undefined }, credentials: { api_key: true }, missing_fields: [], selectable: true };
    current = { ...current, revision: current.revision + 1 };
    return structuredClone(current) as never;
  });
  const confirmation = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<Router initialPath="/settings"><Settings /></Router>);
  fireEvent.click(await screen.findByRole("button", { name: "Add connection" }));
  fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "My relay" } });
  fireEvent.change(screen.getByLabelText("API base URL"), { target: { value: "https://relay.example/v1" } });
  fireEvent.change(screen.getByPlaceholderText("New credential"), { target: { value: "custom-private-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ connection_changes: [expect.objectContaining({ action: "create", name: "My relay", credentials: { api_key: "custom-private-key" } })] })));
  await screen.findByRole("button", { name: "Delete connection" });
  expect(JSON.stringify(localStorage)).not.toContain("custom-private-key");
  fireEvent.click(screen.getByRole("button", { name: "Delete connection" }));
  await waitFor(() => expect(screen.queryByRole("heading", { name: "My relay" })).not.toBeInTheDocument());
  expect(confirmation).toHaveBeenCalled();
  confirmation.mockRestore();
});

test("409 merges independent fields and submits only local changes", async () => {
  const initial = { ...view, values: { ...view.values, temperature: 0.5 } };
  const latest = { ...initial, revision: 2, values: { ...initial.values, temperature: 0.9 } };
  vi.mocked(api.settings).mockResolvedValueOnce(initial as never).mockResolvedValue(latest as never);
  vi.mocked(api.settingsSchema).mockResolvedValue({ ...schema, fields: [...schema.fields, { ...schema.fields[0], key: "temperature", kind: "number", label: { en: "Temperature" } }] } as never);
  vi.mocked(api.saveSettings).mockRejectedValueOnce(new ApiError(409, "configuration_revision_conflict", "Conflict")).mockResolvedValue({ ...latest, revision: 3, values: { ...latest.values, output_language: "ja" } } as never);
  render(<Router initialPath="/settings/research"><Settings /></Router>);
  fireEvent.change(await screen.findByLabelText("Report language"), { target: { value: "ja" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  fireEvent.click(await screen.findByRole("button", { name: "Apply choices to draft" }));
  expect(screen.getByLabelText("Temperature")).toHaveValue(0.9);
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenLastCalledWith({ revision: 2, values: { output_language: "ja" }, credentials: {} }));
});

test("same-field conflicts need a choice on every changed revision", async () => {
  vi.mocked(api.settings).mockResolvedValueOnce(view as never).mockResolvedValueOnce({ ...view, revision: 2, values: { ...view.values, output_language: "zh-CN" } } as never).mockResolvedValue({ ...view, revision: 3, values: { ...view.values, output_language: "fr" } } as never);
  vi.mocked(api.saveSettings).mockRejectedValue(new ApiError(409, "configuration_revision_conflict", "Conflict"));
  render(<Router initialPath="/settings/research"><Settings /></Router>);
  fireEvent.change(await screen.findByLabelText("Report language"), { target: { value: "ja" } });
  for (const remote of ["zh-CN", "fr"]) {
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("button", { name: "Apply choices to draft" })).toBeDisabled();
    expect(screen.getByText(remote)).toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "Keep my change" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply choices to draft" }));
    expect(screen.getByLabelText("Report language")).toHaveValue("ja");
  }
});

test("connection saves refresh unedited fields in another category's draft", async () => {
  const initial = { ...view, values: { ...view.values, temperature: 0.5 } };
  vi.mocked(api.settings).mockResolvedValue(initial as never);
  vi.mocked(api.settingsSchema).mockResolvedValue({ ...schema, fields: [...schema.fields, { ...schema.fields[0], key: "temperature", kind: "number", label: { en: "Temperature" } }] } as never);
  vi.mocked(api.saveSettings).mockResolvedValue({ ...initial, revision: 2, values: { ...initial.values, temperature: 0.9 } } as never);
  render(<Router initialPath="/settings/research"><Settings /></Router>);
  fireEvent.change(await screen.findByLabelText("Report language"), { target: { value: "ja" } });
  fireEvent.click(screen.getByRole("link", { name: "Model connections" }));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "Renamed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Saved");
  fireEvent.click(screen.getByRole("link", { name: "Research defaults" }));
  expect(screen.getByLabelText("Temperature")).toHaveValue(0.9);
  expect(screen.getByLabelText("Report language")).toHaveValue("ja");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenLastCalledWith({ revision: 2, values: { output_language: "ja" }, credentials: {} }));
});

test("connection conflicts merge nested transport and explicitly confirm secret operations", async () => {
  const connection = { ...view.connections.main.connection, transport: { kind: "azure", base_url: "https://azure.example", deployment: "a", api_version: "2024-10-21" } };
  const initial = { ...view, connections: { main: { ...view.connections.main, connection } } };
  const latest = { ...initial, revision: 2, connections: { main: { ...initial.connections.main, connection: { ...connection, transport: { ...connection.transport, deployment: "b" } } } } };
  vi.mocked(api.settings).mockResolvedValueOnce(initial as never).mockResolvedValue(latest as never);
  vi.mocked(api.saveSettings).mockRejectedValueOnce(new ApiError(409, "configuration_revision_conflict", "Conflict")).mockResolvedValue({ ...latest, revision: 3 } as never);
  render(<Router initialPath="/settings"><Settings /></Router>);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("API base URL"), { target: { value: "https://mine.example" } });
  fireEvent.change(screen.getByPlaceholderText("New credential"), { target: { value: "private-conflict-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("button", { name: "Apply choices to draft" })).toBeDisabled();
  expect(screen.getByRole("alert")).not.toHaveTextContent("private-conflict-key");
  fireEvent.click(screen.getByRole("radio", { name: "Keep pending operation" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply choices to draft" }));
  expect(screen.getByLabelText(/Azure deployment/)).toHaveValue("b");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenLastCalledWith({ revision: 2, connection_changes: [{ action: "update", id: "main", transport: { ...connection.transport, base_url: "https://mine.example", deployment: "b" }, credentials: { api_key: "private-conflict-key" } }] }));
});

test("deleted connections keep drafts without recreating the identity", async () => {
  vi.mocked(api.settings).mockResolvedValueOnce(view as never).mockResolvedValue({ ...view, revision: 2, connections: {} } as never);
  vi.mocked(api.saveSettings).mockRejectedValue(new ApiError(409, "configuration_revision_conflict", "Conflict"));
  render(<Router initialPath="/settings"><Settings /></Router>);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "My draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This connection was deleted");
  expect(screen.getByLabelText("Connection name")).toHaveValue("My draft");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test("upgrade baseline restoration is previewed and preserves name, state and secrets", async () => {
  const current = view.connections.main.connection;
  vi.mocked(api.settings).mockResolvedValue({ ...view, connections: { main: { ...view.connections.main, connection: { ...current, template_origin: "upgrade", template: { transport: { ...current.transport, base_url: "https://upgrade.example" } } } } } } as never);
  render(<Router initialPath="/settings"><Settings /></Router>);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "My name" } });
  fireEvent.change(screen.getByPlaceholderText("New credential"), { target: { value: "retained-private" } });
  fireEvent.click(screen.getByRole("button", { name: "Restore upgrade settings" }));
  expect(screen.getByRole("region", { name: "Review settings to restore" })).not.toHaveTextContent("retained-private");
  expect(api.saveSettings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Restore these settings" }));
  expect(screen.getByLabelText("API base URL")).toHaveValue("https://upgrade.example");
  expect(screen.getByLabelText("Connection name")).toHaveValue("My name");
  expect(screen.getByPlaceholderText("New credential")).toHaveValue("retained-private");
});
