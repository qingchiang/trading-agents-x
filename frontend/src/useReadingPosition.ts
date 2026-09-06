import { useLayoutEffect, type RefObject } from "react";

export function useReadingPosition(key: string, ref?: RefObject<HTMLElement | null>, enabled = true) {
  useLayoutEffect(() => {
    if (!enabled) return;
    let frame = 0;
    const restore = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        let anchor = "";
        try { anchor = decodeURIComponent(window.location.hash.slice(1)); } catch { /* Invalid fragments have no matching section. */ }
        const heading = anchor ? document.getElementById(`user-content-${anchor}`) ?? document.getElementById(anchor) : null;
        if (heading && (!ref || ref.current?.contains(heading))) heading.scrollIntoView?.({ block: "start" });
        else {
          const saved = sessionStorage.getItem(key);
          window.scrollTo?.(0, Math.max(0, Number(saved) || 0));
        }
      });
    };
    restore();
    const save = () => sessionStorage.setItem(key, String(window.scrollY));
    window.addEventListener("scroll", save, { passive: true });
    window.addEventListener("popstate", restore);
    window.addEventListener("hashchange", restore);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", save);
      window.removeEventListener("popstate", restore);
      window.removeEventListener("hashchange", restore);
    };
  }, [key, ref, enabled]);
}
