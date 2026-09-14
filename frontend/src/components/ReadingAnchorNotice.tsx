import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { resolveReadingAnchor } from "../readingAnchors";

/** Async readers may mount after the page-position hook; retry once content settles. */
export default function ReadingAnchorNotice({ identity }: { identity: string }) {
  const { t } = useTranslation();
  const marker = useRef<HTMLDivElement>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    const root = marker.current?.parentElement;
    if (!root) return;
    let handled = "";
    let timer = 0;
    const update = () => {
      clearTimeout(timer);
      timer = window.setTimeout(() => {
        let anchor = "";
        try { anchor = decodeURIComponent(window.location.hash.slice(1)); } catch { setMissing(true); return; }
        if (!anchor || anchor.startsWith("evidence-")) { setMissing(false); return; }
        if (handled === anchor || root.querySelector(".loading")) return;
        const target = resolveReadingAnchor(root, anchor);
        setMissing(!target);
        if (!target) return;
        handled = anchor;
        if (target instanceof HTMLDetailsElement) target.open = true;
        target.scrollIntoView?.({ block: "start" });
      }, 80);
    };
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { subtree: true, childList: true });
    const revisit = () => { handled = ""; update(); };
    window.addEventListener("hashchange", revisit);
    window.addEventListener("popstate", revisit);
    return () => { observer.disconnect(); clearTimeout(timer); window.removeEventListener("hashchange", revisit); window.removeEventListener("popstate", revisit); };
  }, [identity]);
  return <div ref={marker}>{missing && <p className="notice" role="status">{t("chapterUnavailable")}</p>}</div>;
}
