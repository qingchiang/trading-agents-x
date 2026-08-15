import { fireEvent, render, screen } from "@testing-library/react";
import App from "./App";
import { Router } from "./router";

vi.mock("./pages/Dashboard", () => ({
  default: () => <div>dashboard-page</div>,
}));
vi.mock("./pages/NewRun", () => ({ default: () => <div>new-run-page</div> }));
vi.mock("./pages/ResearchChainDetail", () => ({
  default: () => <div>research-chain-detail-page</div>,
}));
vi.mock("./pages/ResearchChains", () => ({
  default: () => <div>research-chains-page</div>,
}));
vi.mock("./pages/ResearchReview", () => ({
  default: () => <div>research-review-page</div>,
}));
vi.mock("./pages/RunDetail", () => ({
  default: () => <div>run-detail-page</div>,
}));
vi.mock("./pages/Runs", () => ({ default: () => <div>runs-page</div> }));
vi.mock("./pages/Settings", () => ({
  default: () => <div>settings-page</div>,
}));
vi.mock("./components/LoginDialog", () => ({
  default: () => <div role="dialog">login-dialog</div>,
}));

test.each([
  ["/", "dashboard-page"],
  ["/runs/new", "new-run-page"],
  ["/runs", "runs-page"],
  ["/runs/run%201?view=decision", "run-detail-page"],
  ["/research", "research-chains-page"],
  ["/research/chain%201", "research-chain-detail-page"],
  ["/reviews?q=6501.T", "research-review-page"],
  ["/settings", "settings-page"],
  ["/unknown", "dashboard-page"],
])("preserves the page selected by the %s deep link", async (path, label) => {
  render(
    <Router initialPath={path}>
      <App />
    </Router>,
  );

  expect(await screen.findByText(label)).toBeInTheDocument();
});

test("loads the login dialog only after authentication is requested", async () => {
  render(
    <Router initialPath="/">
      <App />
    </Router>,
  );

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent(window, new Event("tradingagents:auth-required"));
  expect(await screen.findByRole("dialog")).toHaveTextContent("login-dialog");
});
