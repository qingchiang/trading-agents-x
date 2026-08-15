import { readdir, stat } from "node:fs/promises";

const maxChunkBytes = 500_000;
const assetsDirectory = new URL(
  "../../tradingagents/web/static/assets/",
  import.meta.url,
);
const chunks = (await readdir(assetsDirectory))
  .filter((name) => name.endsWith(".js"))
  .sort();
const oversized = [];

for (const name of chunks) {
  const size = (await stat(new URL(name, assetsDirectory))).size;
  if (size > maxChunkBytes) oversized.push(`${name} (${size} bytes)`);
}

if (oversized.length > 0) {
  throw new Error(
    `Minified JavaScript chunks exceed 500 kB:\n${oversized.join("\n")}`,
  );
}
