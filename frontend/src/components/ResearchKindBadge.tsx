import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

interface ResearchMethodRequest {
  profile?: string | null;
  analysts?: readonly string[] | null;
  llm_provider?: string | null;
  quick_model?: string | null;
  deep_model?: string | null;
  quick_reasoning_effort?: string | null;
  deep_reasoning_effort?: string | null;
}

export default function ResearchKindBadge({
  kind,
  request,
  methodSnapshot,
}: {
  kind?: "full" | "incremental" | null;
  request?: ResearchMethodRequest | null;
  methodSnapshot?: Record<string, unknown> | null;
}) {
  const { t } = useTranslation();
  const normalized = kind === "incremental" ? "incremental" : "full";
  const rootRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const hoverCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tooltipId = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  const open = hovered || focused || pinned;
  const method = methodDetails(request, methodSnapshot);
  const label = t(
    normalized === "incremental" ? "incrementalResearch" : "fullResearch",
  );

  const openFromHover = () => {
    if (hoverCloseTimer.current !== null) {
      clearTimeout(hoverCloseTimer.current);
      hoverCloseTimer.current = null;
    }
    setHovered(true);
  };

  const closeFromHover = () => {
    if (hoverCloseTimer.current !== null) {
      clearTimeout(hoverCloseTimer.current);
    }
    hoverCloseTimer.current = setTimeout(() => {
      setHovered(false);
      hoverCloseTimer.current = null;
    }, 120);
  };

  const placeTooltip = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(340, Math.max(240, window.innerWidth - 24));
    const left = Math.min(
      Math.max(12, rect.left),
      Math.max(12, window.innerWidth - width - 12),
    );
    const placeAbove = window.innerHeight - rect.bottom < 300 && rect.top > 300;
    setPosition({
      width,
      left,
      ...(placeAbove
        ? { bottom: window.innerHeight - rect.top + 8 }
        : { top: rect.bottom + 8 }),
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    placeTooltip();
  }, [open, placeTooltip]);

  useEffect(() => {
    return () => {
      if (hoverCloseTimer.current !== null) {
        clearTimeout(hoverCloseTimer.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", placeTooltip);
    window.addEventListener("scroll", placeTooltip, true);
    return () => {
      window.removeEventListener("resize", placeTooltip);
      window.removeEventListener("scroll", placeTooltip, true);
    };
  }, [open, placeTooltip]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent) {
        if (event.key !== "Escape") return;
      } else if (
        rootRef.current?.contains(event.target as Node) ||
        tooltipRef.current?.contains(event.target as Node)
      ) {
        return;
      }
      setPinned(false);
      setHovered(false);
      setFocused(false);
      if (event instanceof KeyboardEvent) {
        triggerRef.current?.blur();
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <span
      className={`research-kind-tooltip-root${pinned ? " pinned" : ""}`}
      ref={rootRef}
      onMouseEnter={openFromHover}
      onMouseLeave={closeFromHover}
    >
      <button
        type="button"
        className={`research-kind-badge ${normalized}`}
        aria-describedby={open ? tooltipId : undefined}
        aria-expanded={pinned}
        ref={triggerRef}
        onBlur={(event) => {
          if (!tooltipRef.current?.contains(event.relatedTarget as Node)) {
            setFocused(false);
          }
        }}
        onClick={() => setPinned((value) => !value)}
        onFocus={() => setFocused(true)}
      >
        {label}
      </button>
      {open && createPortal(
        <div
          id={tooltipId}
          className="research-kind-tooltip"
          role="tooltip"
          aria-label={t("researchConfiguration")}
          ref={tooltipRef}
          style={position}
          onMouseEnter={openFromHover}
          onMouseLeave={closeFromHover}
        >
          <strong>{label}</strong>
          <dl>
            {normalized === "full" && method.profile && (
              <div><dt>{t("profile")}</dt><dd>{t(method.profile)}</dd></div>
            )}
            <div>
              <dt>{t(normalized === "incremental" ? "updateScope" : "analysts")}</dt>
              <dd>
                {normalized === "incremental" && (
                  <span>{t("informationDomainCount", { count: method.analysts.length })} · </span>
                )}
                {analystLabels(method.analysts, t)}
              </dd>
            </div>
            <div><dt>{t("provider")}</dt><dd>{method.provider ?? t("notRecorded")}</dd></div>
            {normalized === "full" && (
              <>
                <div><dt>{t("quickModel")}</dt><dd>{method.quickModel ?? t("notRecorded")}</dd></div>
                <div><dt>{t("quickReasoning")}</dt><dd>{reasoningLabel(method.quickReasoning, t)}</dd></div>
              </>
            )}
            <div><dt>{t("deepModel")}</dt><dd>{method.deepModel ?? t("notRecorded")}</dd></div>
            <div><dt>{t("deepReasoning")}</dt><dd>{reasoningLabel(method.deepReasoning, t)}</dd></div>
          </dl>
        </div>,
        document.body,
      )}
    </span>
  );
}

function methodDetails(
  request?: ResearchMethodRequest | null,
  snapshot?: Record<string, unknown> | null,
) {
  const roles = request?.analysts ?? stringArray(snapshot?.enabled_roles);
  return {
    profile: request?.profile ?? null,
    analysts: roles ?? [],
    provider: request?.llm_provider ?? stringValue(snapshot?.llm_provider),
    quickModel: request?.quick_model ?? stringValue(snapshot?.quick_model),
    deepModel: request?.deep_model ?? stringValue(snapshot?.deep_model),
    quickReasoning:
      request?.quick_reasoning_effort ??
      stringValue(snapshot?.quick_reasoning_effort),
    deepReasoning:
      request?.deep_reasoning_effort ??
      stringValue(snapshot?.deep_reasoning_effort),
  };
}

function stringArray(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function analystLabels(analysts: readonly string[], t: TFunction) {
  return analysts.length
    ? analysts.map((analyst) => t(`${analyst}Analyst`)).join(", ")
    : t("notRecorded");
}

function reasoningLabel(value: string | null, t: TFunction) {
  return !value || value === "provider_default" ? t("providerDefault") : value;
}
