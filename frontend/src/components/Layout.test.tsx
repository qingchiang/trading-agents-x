import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { Router } from "../router";
import Layout from "./Layout";

test("shows the concise Simplified Chinese locale label", () => {
  render(
    <Router initialPath="/">
      <Layout>
        <div>content</div>
      </Layout>
    </Router>,
  );

  expect(screen.getByRole("option", { name: "简体中文" })).toHaveValue(
    "zh-CN",
  );
  expect(
    screen.queryByRole("option", { name: /中国大陆/ }),
  ).not.toBeInTheDocument();
});
