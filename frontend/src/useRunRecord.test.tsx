import { act, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { api, type RunDetail } from "./api/client";
import { useRunRecord } from "./useRunRecord";
vi.mock("./api/client", () => ({ api: { run: vi.fn(), artifacts: vi.fn() } }));

function Probe({ id }: { id: string }) {
  const { detail } = useRunRecord(id, "decision");
  return <p>{detail?.run.instrument_name ?? "Loading"}</p>;
}
test("ignores a late first A response after switching A to B to A", async () => {
  const requests: Array<(value: RunDetail) => void> = [];
  vi.mocked(api.run).mockImplementation(() => new Promise(resolve => requests.push(resolve)));
  const { rerender } = render(<Probe id="A" />);
  rerender(<Probe id="B" />); rerender(<Probe id="A" />);
  const record = (name: string) => ({ run: { id: "A", status: "succeeded", is_research_node: true, instrument_name: name }, result: null }) as RunDetail;
  await act(async () => requests[2](record("Current A")));
  await waitFor(() => expect(screen.getByText("Current A")).toBeVisible());
  await act(async () => requests[0](record("Stale A")));
  expect(screen.getByText("Current A")).toBeVisible();
  expect(screen.queryByText("Stale A")).toBeNull();
});
