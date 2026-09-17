import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { ConnectionView } from "../api/client";
import RoleConnections, { type RoleSelections } from "./RoleConnections";

const connections: Record<string, ConnectionView> = Object.fromEntries(["one", "two"].map(id => [id, {
  connection: { id, name: id, transport: { kind: "chat_completions", base_url: `https://${id}.example` } }, credentials: {}, missing_fields: [], selectable: true,
}]));
function Editor({ split = false }: { split?: boolean }) {
  const [roles, setRoles] = useState<RoleSelections>({ quick: { connection: "one", model: "quick-model", reasoning: "low" }, deep: { connection: split ? "two" : "one", model: "deep-model", reasoning: "high" } });
  return <RoleConnections connections={connections} language="en" value={roles} onChange={setRoles} />;
}
test("switching one role connection clears only its model and effort", () => {
  render(<Editor />);
  fireEvent.click(screen.getByLabelText("Use one connection"));
  fireEvent.change(screen.getByLabelText("Quick connection", { selector: "select" }), { target: { value: "two" } });
  const quick = screen.getByRole("group", { name: "Quick connection" });
  const deep = screen.getByRole("group", { name: "Deep connection" });
  expect(within(quick).getByLabelText("Model ID (manual input supported)")).toHaveValue("");
  expect(within(quick).getByLabelText("Reasoning effort")).toHaveValue("provider_default");
  expect(within(deep).getByLabelText("Model ID (manual input supported)")).toHaveValue("deep-model");
});
test("merging connections requires an explicit choice of which to keep", () => {
  render(<Editor split />);
  fireEvent.click(screen.getByLabelText("Use one connection"));
  const keep = screen.getByLabelText("Choose the connection to keep");
  expect(keep).toHaveValue("");
  fireEvent.change(keep, { target: { value: "two" } });
  expect(screen.getByLabelText("Shared connection")).toHaveValue("two");
  const quick = screen.getByRole("group", { name: "Quick connection" });
  expect(within(quick).getByLabelText("Model ID (manual input supported)")).toHaveValue("");
});
