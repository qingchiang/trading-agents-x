import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

const PAGE_LINES = 500;
type Match = { line: number; start: number; end: number };

export default function JsonViewer({ value, label }: { value: unknown; label: string }) {
  const { t } = useTranslation();
  const text = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const lines = useMemo(() => (text ?? "").split("\n"), [text]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [matchIndex, setMatchIndex] = useState(0);
  const [wrap, setWrap] = useState(false);
  const [notice, setNotice] = useState("");
  const code = useRef<HTMLPreElement>(null);
  const matches = useMemo(() => {
    if (!query) return [];
    const needle = query.toLowerCase();
    return lines.flatMap((line, index) => {
      const found: Match[] = [];
      const haystack = line.toLowerCase();
      let start = haystack.indexOf(needle);
      while (start >= 0) { found.push({ line: index, start, end: start + query.length }); start = haystack.indexOf(needle, start + Math.max(1, query.length)); }
      return found;
    });
  }, [lines, query]);
  useEffect(() => { setMatchIndex(0); setPage(matches.length ? Math.floor(matches[0].line / PAGE_LINES) : 0); }, [matches]);
  const active = matches[matchIndex];
  const lastPage = Math.max(0, Math.ceil(lines.length / PAGE_LINES) - 1);
  const currentPage = Math.min(page, lastPage);
  const startLine = currentPage * PAGE_LINES;
  useEffect(() => {
    const container = code.current;
    const target = container?.querySelector<HTMLElement>(".json-line-current");
    if (container && target) {
      container.scrollTop += target.getBoundingClientRect().top - container.getBoundingClientRect().top - 12;
      (target.querySelector("mark") ?? target).scrollIntoView?.({ block: "nearest", inline: "nearest" });
    }
    else if (container) container.scrollTop = 0;
  }, [active, currentPage]);
  const seek = (direction: number) => {
    if (!matches.length) return;
    const next = (matchIndex + direction + matches.length) % matches.length;
    setMatchIndex(next); setPage(Math.floor(matches[next].line / PAGE_LINES));
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(text ?? ""); setNotice(t("jsonCopied")); }
    catch { setNotice(t("jsonCopyFailed")); }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text ?? ""], { type: "application/json;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "research-record.json"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  if (text === undefined) return <p>{t("notRecorded")}</p>;
  return <section className="json-viewer" aria-label={`${t("rawRecord")}: ${label}`}>
    <div className="json-tools">
      <label>{t("jsonSearch")}<input type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <button className="button" disabled={!matches.length} onClick={() => seek(-1)}>{t("previousMatch")}</button>
      <button className="button" disabled={!matches.length} onClick={() => seek(1)}>{t("nextMatch")}</button>
      <span role="status">{query && t("jsonMatches", { current: matches.length ? matchIndex + 1 : 0, total: matches.length })}</span>
      <label className="checkbox-label"><input type="checkbox" checked={wrap} onChange={event => setWrap(event.target.checked)} />{t("wrapLines")}</label>
      <button className="button" onClick={() => void copy()}>{t("copyFullJson")}</button>
      <button className="button" onClick={download}>{t("downloadJson")}</button>
    </div>
    {notice && <p role="status">{notice}</p>}
    <div className="json-pagination"><span>{t("jsonLineRange", { start: startLine + 1, end: Math.min(startLine + PAGE_LINES, lines.length), total: lines.length })}</span>
      <button className="button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>{t("previous")}</button>
      <button className="button" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>{t("next")}</button>
    </div>
    <pre className={wrap ? "json-code wrap" : "json-code"} ref={code} tabIndex={0} aria-label={t("jsonContent")}><code>{lines.slice(startLine, startLine + PAGE_LINES).map((line, index) => {
      const number = startLine + index;
      const match = active?.line === number ? active : undefined;
      return <span className={`json-line${match ? " json-line-current" : ""}`} key={number}><span className="json-line-number" aria-hidden="true">{number + 1}</span><span>{colorLine(line, match)}</span></span>;
    })}</code></pre>
  </section>;
}

/** Tokenize serialized JSON as text; never interpret payloads as markup. */
function colorLine(line: string, match?: Match): ReactNode[] {
  const tokens = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
  const output: ReactNode[] = [];
  let position = 0;
  const add = (text: string, start: number, kind: string) => {
    const from = Math.max(0, (match?.start ?? Infinity) - start);
    const to = Math.min(text.length, (match?.end ?? -1) - start);
    output.push(<span className={kind} key={start}>{from < to ? <>{text.slice(0, from)}<mark>{text.slice(from, to)}</mark>{text.slice(to)}</> : text}</span>);
  };
  for (const token of line.matchAll(tokens)) {
    const start = token.index!;
    if (start > position) add(line.slice(position, start), position, "json-punctuation");
    const raw = token[0];
    const kind = raw.startsWith('"') ? (/^\s*:/.test(line.slice(start + raw.length)) ? "key" : "string") : raw === "null" ? "null" : ["true", "false"].includes(raw) ? "boolean" : "number";
    add(raw, start, `json-${kind}`); position = start + raw.length;
  }
  if (position < line.length) add(line.slice(position), position, "json-punctuation");
  return output;
}
