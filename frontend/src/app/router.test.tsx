import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { Link, Router, useLocation } from "./router";

function Journey() {
  const location = useLocation();
  return location.pathname === "/timelines" ? <Link to="/timelines/NVDA">Open research</Link>
    : <><Link to="/timelines/NVDA?node=old">Read history</Link><Link to={location.sourceLibrary?.url ?? "/timelines"}>Return to library</Link></>;
}

test("retains the originating library URL through history selection", () => {
  render(<Router initialPath="/timelines?q=nvidia&offset=25&expanded=NVDA&cycle_offset=12"><Journey /></Router>);
  fireEvent.click(screen.getByRole("link", { name: "Open research" }));
  fireEvent.click(screen.getByRole("link", { name: "Read history" }));
  expect(screen.getByRole("link", { name: "Return to library" })).toHaveAttribute("href", "/timelines?q=nvidia&offset=25&expanded=NVDA&cycle_offset=12");
});
