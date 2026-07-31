import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import Markdown from "./Markdown";

test("renders markdown while dropping raw HTML and script content", () => {
  const { container } = render(
    <Markdown>
      {"# Safe heading\n\n<script>alert('xss')</script>\n\n<img src=x onerror=alert(1)>"}
    </Markdown>,
  );
  expect(screen.getByRole("heading", { name: "Safe heading" })).toBeVisible();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("img")).toBeNull();
});

test("links evidence refs only in ordinary markdown text", () => {
  const openEvidence = vi.fn();
  render(
    <Markdown
      evidenceAliases={{
        ev_0123456789ab: "E01",
        ev_fedcba987654: "E02",
      }}
      onEvidence={openEvidence}
    >
      {
        "Use ev_0123456789ab.\n\n`ev_fedcba987654`\n\n[existing ev_fedcba987654](https://example.com)\n\n```\nev_0123456789ab\n```"
      }
    </Markdown>,
  );

  const marker = screen.getByRole("button", {
    name: "Open evidence ev_0123456789ab",
  });
  expect(marker).toHaveTextContent("E01");
  expect(marker).toHaveAttribute("title", "ev_0123456789ab");
  fireEvent.click(marker);
  expect(openEvidence).toHaveBeenCalledWith("ev_0123456789ab");
  expect(
    screen.queryByRole("button", {
      name: "Open evidence ev_fedcba987654",
    }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("ev_fedcba987654", { selector: "code" })).toBeVisible();
  expect(
    screen.getByRole("link", { name: "existing ev_fedcba987654" }),
  ).toHaveAttribute("href", "https://example.com");
});

test("replaces evidence footnote AST nodes without native footer or backrefs", () => {
  const firstRef = "ev_0123456789ab";
  const secondRef = "ev_fedcba987654";
  const openEvidence = vi.fn();
  const { container } = render(
    <Markdown
      evidenceAliases={{
        [firstRef]: "E01",
        [secondRef]: "E02",
      }}
      onEvidence={openEvidence}
    >
      {`Adjacent references[^${firstRef}][^${secondRef}] and repeated[^${firstRef}].

[^${firstRef}]: Model-authored source definition.
[^${secondRef}]: Another source definition.`}
    </Markdown>,
  );

  const markers = screen.getAllByRole("button", { name: /Open evidence/ });
  expect(markers.map((marker) => marker.textContent)).toEqual([
    "E01",
    "E02",
    "E01",
  ]);
  expect(screen.queryByText(/Model-authored source definition/)).toBeNull();
  expect(container.querySelector("[data-footnotes]")).toBeNull();
  expect(container).not.toHaveTextContent("↩");
  fireEvent.click(markers[1]);
  expect(openEvidence).toHaveBeenCalledWith(secondRef);
});
