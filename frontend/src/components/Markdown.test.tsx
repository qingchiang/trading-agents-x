import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

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
