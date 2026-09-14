import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export default function ExpandableText({ text }: { text: string }) {
  const { t } = useTranslation();
  const paragraph = useRef<HTMLParagraphElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(false);
  useLayoutEffect(() => { setExpanded(false); }, [text]);
  useLayoutEffect(() => {
    const element = paragraph.current;
    if (!element || expanded) return;
    let active = true;
    const measure = () => { if (active) setOverflow(element.scrollHeight > element.clientHeight + 1); };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener("resize", measure);
    void document.fonts?.ready.then(measure);
    measure();
    return () => { active = false; observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, [text, expanded]);
  return <div className="thesis-excerpt"><p ref={paragraph} className={expanded ? "expanded" : "clamped"}>{text}</p>{(overflow || expanded) && <button className="text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{t(expanded ? "collapseText" : "readMore")}</button>}</div>;
}
