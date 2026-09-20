/** Three-way editing of JSON documents. Arrays and interface changes are atomic. */
export type Choices = Record<string, "local" | "server">;
export type FieldConflict = { path: string[]; base: unknown; local: unknown; server: unknown };
type Document = Record<string, unknown>;
const object = (v: unknown): v is Document => v !== null && typeof v === "object" && !Array.isArray(v);
export function equal(a: unknown, b: unknown): boolean {
  if (object(a) && object(b)) return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(k => equal(a[k], b[k]));
  return JSON.stringify(a) === JSON.stringify(b);
}
const keys = (...values: unknown[]) => [...new Set(values.flatMap(v => object(v) ? Object.keys(v) : []))];
function descend(base: unknown, local: unknown, server: unknown, path: string[]) {
  return object(base) && object(local) && object(server)
    && !(path.at(-1) === "transport" && (base.kind !== local.kind || base.kind !== server.kind));
}
export function mergeDraft<T>(base: T, local: T, server: T, choices: Choices = {}): { value: T; conflicts: FieldConflict[] } {
  const conflicts: FieldConflict[] = [];
  function visit(b: unknown, l: unknown, s: unknown, path: string[]): unknown {
    if (equal(b, l)) return s;
    if (equal(b, s) || equal(l, s)) return l;
    if (descend(b, l, s, path)) return Object.fromEntries(keys(b, l, s).map(k => [k, visit((b as Document)[k], (l as Document)[k], (s as Document)[k], [...path, k])]).filter(([, v]) => v !== undefined));
    conflicts.push({ path, base: b, local: l, server: s });
    return choices[JSON.stringify(path)] === "server" ? s : l;
  }
  return { value: visit(base, local, server, []) as T, conflicts };
}
export function refreshDraft<T>(base: T, local: T, server: T): { base: T; value: T } {
  function visit(b: unknown, l: unknown, s: unknown, path: string[]): [unknown, unknown] {
    if (equal(b, l) || equal(l, s)) return [s, s];
    if (!descend(b, l, s, path)) return [b, l];
    const entries = keys(b, l, s).map(k => [k, visit((b as Document)[k], (l as Document)[k], (s as Document)[k], [...path, k])] as const);
    return [Object.fromEntries(entries.map(([k, v]) => [k, v[0]])), Object.fromEntries(entries.map(([k, v]) => [k, v[1]]))];
  }
  const [nextBase, value] = visit(base, local, server, []);
  return { base: nextBase as T, value: value as T };
}
export function changedValues<T extends object>(base: T, value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([k, v]) => !equal((base as Document)[k], v))) as Partial<T>;
}
