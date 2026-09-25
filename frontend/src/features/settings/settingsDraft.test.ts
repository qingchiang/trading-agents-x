import { expect, test } from "vitest";
import { mergeDraft, refreshDraft, changedValues } from "./settingsDraft";

test("merges independent nested edits and submits only changed fields", () => {
  const base = { name: "Relay", transport: { kind: "azure", base_url: "old", deployment: "a" } };
  const local = { ...base, transport: { ...base.transport, base_url: "mine" } };
  const remote = { ...base, name: "Renamed", transport: { ...base.transport, deployment: "b" } };
  const result = mergeDraft(base, local, remote);
  expect(result.conflicts).toEqual([]);
  expect(result.value).toEqual({ name: "Renamed", transport: { kind: "azure", base_url: "mine", deployment: "b" } });
  expect(changedValues(remote, result.value)).toEqual({ transport: result.value.transport });
});

test("requires a choice for the same path and treats interface changes atomically", () => {
  const base = { transport: { kind: "chat_completions", base_url: "old" } };
  const local = { transport: { kind: "responses", base_url: "old" } };
  const remote = { transport: { kind: "chat_completions", base_url: "remote" } };
  const result = mergeDraft(base, local, remote);
  expect(result.conflicts.map(c => c.path)).toEqual([["transport"]]);
  expect(mergeDraft(base, local, remote, { '["transport"]': "server" }).value).toEqual(remote);
});

test("advances unedited baselines without losing unresolved conflicts or list order", () => {
  const base = { language: "en", limit: 5, routes: { ".T": ["one", "two"] } };
  const local = { ...base, language: "ja" };
  const remote = { language: "zh", limit: 10, routes: { ".T": ["two", "one"] } };
  const refreshed = refreshDraft(base, local, remote);
  expect(refreshed.base).toEqual({ ...remote, language: "en" });
  expect(refreshed.value).toEqual({ ...remote, language: "ja" });
  expect(mergeDraft(refreshed.base, refreshed.value, remote).conflicts).toHaveLength(1);
});
