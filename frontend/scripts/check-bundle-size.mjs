import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";

const maximumInitialChunkBytes = 500 * 1024;
const outputDirectory = resolve(import.meta.dirname, "../../tradingagents/web/static");
const manifest = JSON.parse(
  await readFile(resolve(outputDirectory, ".vite/manifest.json"), "utf8"),
);
const entry = Object.values(manifest).find((item) => item.isEntry);

if (!entry) {
  throw new Error("Vite manifest does not contain an initial entry chunk.");
}

const initialFiles = new Set();
const visit = (item) => {
  if (item.file.endsWith(".js")) initialFiles.add(item.file);
  for (const key of item.imports ?? []) {
    const imported = manifest[key];
    if (imported && !initialFiles.has(imported.file)) visit(imported);
  }
};
visit(entry);

const oversized = [];
for (const file of initialFiles) {
  const size = (await stat(resolve(outputDirectory, file))).size;
  if (size > maximumInitialChunkBytes) oversized.push({ file, size });
}

if (oversized.length) {
  const details = oversized
    .map(({ file, size }) => `${file}: ${(size / 1024).toFixed(1)} KB`)
    .join("\n");
  throw new Error(`Initial JavaScript chunk exceeds 500 KB:\n${details}`);
}

console.log(
  `Bundle gate passed: ${initialFiles.size} initial JavaScript chunk(s), each <= 500 KB.`,
);
