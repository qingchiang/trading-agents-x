import { resolveReadingAnchor } from "./readingAnchors";
import { useHistoryEntryKey } from "../../app/router";
import { useLayoutEffect, type RefObject } from "react";

export function useReadingPosition(key: string, ref?: RefObject<HTMLElement | null>, enabled = true) {
  const entryKey = useHistoryEntryKey() ?? key;
  useLayoutEffect(() => {
    if (!enabled) return;
    let frame = 0;
    let restored = false;
    const positionKey = `tradingagents-position:${entryKey}`;
    const restore = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        let anchor = "";
        try { anchor = decodeURIComponent(window.location.hash.slice(1)); } catch { /* Invalid fragments have no matching section. */ }
        const heading = anchor ? resolveReadingAnchor(ref?.current ?? document, anchor) : null;
        if (heading && (!ref || ref.current?.contains(heading))) {
          if (heading instanceof HTMLDetailsElement) heading.open = true;
          heading.scrollIntoView?.({ block: "start" });
        }
        else {
          const saved = sessionStorage.getItem(positionKey) ?? sessionStorage.getItem(key);
          window.scrollTo?.(0, Math.max(0, Number(saved) || 0));
        }
        restored = true;
      });
    };
    restore();
    const save = () => { if (restored) sessionStorage.setItem(positionKey, String(window.scrollY)); };
    window.addEventListener("scroll", save, { passive: true });
    window.addEventListener("popstate", restore);
    window.addEventListener("hashchange", restore);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", save);
      window.removeEventListener("popstate", restore);
      window.removeEventListener("hashchange", restore);
    };
  }, [key, entryKey, ref, enabled]);
}
