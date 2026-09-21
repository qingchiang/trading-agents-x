import { createRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import "../../shared/i18n";
import WorkspaceOutline from "./WorkspaceOutline";

test("uses declared sections, excludes prose headings and opens a collapsed group", async () => {
  const container = createRef<HTMLDivElement>();
  const target = document.createElement("div"); document.body.append(target);
  const navigate = vi.fn();
  render(<div ref={container}><div className="workspace-reader">
    <h2 id="assessment-summary" data-outline="Summary">Summary</h2>
    <h3>Nested prose heading</h3>
    <details id="reassessment-risks" data-outline="Risks"><summary>Risk group</summary><p>Details</p></details>
  </div><WorkspaceOutline container={container} target={target} onNavigate={navigate} /></div>);
  await waitFor(() => expect(screen.getByRole("button", { name: "Summary" })).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Nested prose heading" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Risks" }));
  expect(document.getElementById("reassessment-risks")).toHaveAttribute("open");
  expect(window.location.hash).toBe("#reassessment-risks");
  expect(navigate).toHaveBeenCalled(); target.remove();
});
