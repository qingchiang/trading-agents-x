import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import i18n from "../i18n";
import JsonRecord from "./JsonRecord";

test("opens only the requested record and finds and copies content beyond the first 500 lines", async () => {
  await i18n.changeLanguage("en");
  const value = { entries: Array.from({ length: 520 }, (_, i) => ({ value: i === 519 ? '中文 <script>bad()</script> "quoted"' : i })) };
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
  const { container } = render(<JsonRecord label="Payload" value={value} />);
  expect(container.querySelector('pre')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: /Payload/ }));
  const search = await screen.findByRole('searchbox', { name: 'Search JSON' });
  expect(container.querySelectorAll('.json-line')).toHaveLength(500);
  fireEvent.change(search, { target: { value: '中文' } });
  await waitFor(() => expect(container.querySelector('mark')).toHaveTextContent('中文'));
  expect(container.querySelectorAll('script')).toHaveLength(0);
  expect(container.querySelector('.json-string')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Copy full JSON' }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(JSON.stringify(value, null, 2)));
});
