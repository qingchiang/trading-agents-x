import {
  type FormEvent,
  type RefCallback,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type OutcomeFeedbackRetireRequest,
  type ResearchReview,
  type ResearchReviewAuditDetail,
} from "../../api/client";
import {
  type ResearchReviewFiltersViewModel,
  useResearchReviewFilters,
} from "./ResearchReviewFilters";

type Retirement = {
  feedbackId: number;
  outcomeId: number;
};

export type ResearchReviewPageViewModel = {
  actionAnnouncement: string;
  actionOutcomeId: number | null;
  auditDetails: Record<number, ResearchReviewAuditDetail>;
  error: string;
  filter: ResearchReviewFiltersViewModel;
  reflectionActionError: string;
  reflectionActionErrorOutcomeId: number | null;
  retirement: Retirement | null;
  retirementError: string;
  retirementNote: string;
  retirementReason: OutcomeFeedbackRetireRequest["reason"];
  reviews: ResearchReview[];
};

export type ResearchReviewPageActions = {
  changeRetirementNote: (value: string) => void;
  changeRetirementReason: (value: string) => void;
  closeRetirement: () => void;
  filter: ReturnType<typeof useResearchReviewFilters>["actions"];
  loadAuditDetail: (outcomeId: number) => Promise<void>;
  openRetirement: (
    feedbackId: number,
    outcomeId: number,
    trigger: HTMLButtonElement,
  ) => void;
  regenerateReflection: (outcomeId: number) => Promise<void>;
  registerRetirementReason: RefCallback<HTMLSelectElement>;
  registerReview: (outcomeId: number, element: HTMLElement | null) => void;
  retireFeedback: (event: FormEvent<HTMLFormElement>) => Promise<void>;
};

export type ResearchReviewPage = {
  actions: ResearchReviewPageActions;
  model: ResearchReviewPageViewModel;
};

export function useResearchReviewPage(): ResearchReviewPage {
  const { t } = useTranslation();
  const [reviews, setReviews] = useState<ResearchReview[]>([]);
  const [error, setError] = useState("");
  const [reflectionActionError, setReflectionActionError] = useState("");
  const [reflectionActionErrorOutcomeId, setReflectionActionErrorOutcomeId] =
    useState<number | null>(null);
  const [actionOutcomeId, setActionOutcomeId] = useState<number | null>(null);
  const [retirement, setRetirement] = useState<Retirement | null>(null);
  const [retirementReason, setRetirementReason] = useState<
    OutcomeFeedbackRetireRequest["reason"]
  >("not_useful");
  const [retirementNote, setRetirementNote] = useState("");
  const [retirementError, setRetirementError] = useState("");
  const [actionAnnouncement, setActionAnnouncement] = useState<{
    outcomeId: number;
    message: string;
  } | null>(null);
  const [auditDetails, setAuditDetails] = useState<
    Record<number, ResearchReviewAuditDetail>
  >({});
  const reviewRefs = useRef<Record<number, HTMLElement | null>>({});
  const auditRequestVersionsRef = useRef<Record<number, number>>({});
  const auditLoadingRef = useRef<Record<number, boolean>>({});
  const retirementTriggerRef = useRef<HTMLButtonElement | null>(null);
  const retirementReasonRef = useRef<HTMLSelectElement | null>(null);

  const focusReview = (outcomeId: number) => {
    reviewRefs.current[outcomeId]?.focus();
  };

  const load = useCallback(
    async (query = "") => {
      try {
        setReviews(await api.reviews(query));
        setError("");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : t("error"));
      }
    },
    [t],
  );
  const { actions: filterActions, model: filterModel } =
    useResearchReviewFilters((query) => void load(query));

  useEffect(() => {
    void load(window.location.search);
  }, []);

  useEffect(() => {
    if (!reviews.length || !window.location.hash) return;
    const target = document.getElementById(
      decodeURIComponent(window.location.hash.slice(1)),
    );
    target?.focus();
    target?.scrollIntoView?.({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "center",
    });
  }, [reviews]);

  const loadAuditDetail = async (outcomeId: number, force = false) => {
    if (!force && auditDetails[outcomeId]) return;
    if (!force && auditLoadingRef.current[outcomeId]) return;
    const requestVersion = (auditRequestVersionsRef.current[outcomeId] ?? 0) + 1;
    auditRequestVersionsRef.current[outcomeId] = requestVersion;
    auditLoadingRef.current[outcomeId] = true;
    try {
      const detail = await api.reviewAuditDetail(outcomeId);
      if (auditRequestVersionsRef.current[outcomeId] !== requestVersion) return;
      setAuditDetails((current) => ({ ...current, [outcomeId]: detail }));
    } catch (cause) {
      if (auditRequestVersionsRef.current[outcomeId] !== requestVersion) return;
      setError(cause instanceof Error ? cause.message : t("error"));
    } finally {
      if (auditRequestVersionsRef.current[outcomeId] === requestVersion) {
        auditLoadingRef.current[outcomeId] = false;
      }
    }
  };

  const refreshRequestedAuditDetail = (outcomeId: number) => {
    if (auditRequestVersionsRef.current[outcomeId] === undefined) return;
    auditRequestVersionsRef.current[outcomeId] += 1;
    setAuditDetails((current) => {
      const next = { ...current };
      delete next[outcomeId];
      return next;
    });
    void loadAuditDetail(outcomeId, true);
  };

  const regenerateReflection = async (outcomeId: number) => {
    setActionOutcomeId(outcomeId);
    setReflectionActionError("");
    setReflectionActionErrorOutcomeId(null);
    try {
      const accepted = await api.regenerateOutcomeReflection(
        outcomeId,
        crypto.randomUUID(),
      );
      setReviews((current) =>
        current.map((review) =>
          review.outcome_id === outcomeId && review.outcome_reflection
            ? {
                ...review,
                review_status: accepted.review_status,
                outcome_reflection: {
                  ...review.outcome_reflection,
                  status:
                    accepted.reflection_status ??
                    review.outcome_reflection.status,
                  generation_cycle: accepted.cycle,
                },
              }
            : review,
        ),
      );
      refreshRequestedAuditDetail(outcomeId);
      setActionAnnouncement({ outcomeId, message: t("reflectionQueued") });
      focusReview(outcomeId);
    } catch (cause) {
      setReflectionActionError(
        cause instanceof Error ? cause.message : t("error"),
      );
      setReflectionActionErrorOutcomeId(outcomeId);
      focusReview(outcomeId);
    } finally {
      setActionOutcomeId(null);
    }
  };

  const openRetirement = (
    feedbackId: number,
    outcomeId: number,
    trigger: HTMLButtonElement,
  ) => {
    retirementTriggerRef.current = trigger;
    setRetirement({ feedbackId, outcomeId });
    setRetirementReason("not_useful");
    setRetirementNote("");
    setRetirementError("");
  };

  const closeRetirement = () => {
    setRetirement(null);
    retirementTriggerRef.current?.focus();
  };

  useEffect(() => {
    if (retirement) retirementReasonRef.current?.focus();
  }, [retirement]);

  const retireFeedback = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!retirement) return;
    setActionOutcomeId(retirement.outcomeId);
    setRetirementError("");
    try {
      const retired = await api.retireOutcomeFeedback(retirement.feedbackId, {
        reason: retirementReason,
        note: retirementNote.trim() || null,
      });
      setRetirement(null);
      setReviews((current) =>
        current.map((review) =>
          review.outcome_id === retirement.outcomeId && review.outcome_feedback
            ? {
                ...review,
                review_status:
                  retired.review_status as ResearchReview["review_status"],
                outcome_feedback: {
                  ...review.outcome_feedback,
                  status: retired.status as NonNullable<
                    ResearchReview["outcome_feedback"]
                  >["status"],
                  retirement_reason: retired.retirement_reason,
                  retirement_note: retired.retirement_note,
                  retired_at: retired.retired_at,
                },
              }
            : review,
        ),
      );
      refreshRequestedAuditDetail(retirement.outcomeId);
      setActionAnnouncement({
        outcomeId: retirement.outcomeId,
        message: t("retirementSuccess"),
      });
      focusReview(retirement.outcomeId);
    } catch (cause) {
      setRetirementError(cause instanceof Error ? cause.message : t("error"));
    } finally {
      setActionOutcomeId(null);
    }
  };

  return {
    actions: {
      changeRetirementNote: setRetirementNote,
      changeRetirementReason: (value) =>
        setRetirementReason(
          value as OutcomeFeedbackRetireRequest["reason"],
        ),
      closeRetirement,
      filter: filterActions,
      loadAuditDetail: (outcomeId) => loadAuditDetail(outcomeId),
      openRetirement,
      regenerateReflection,
      registerRetirementReason: (element) => {
        retirementReasonRef.current = element;
      },
      registerReview: (outcomeId, element) => {
        reviewRefs.current[outcomeId] = element;
      },
      retireFeedback,
    },
    model: {
      actionAnnouncement: actionAnnouncement?.message ?? "",
      actionOutcomeId,
      auditDetails,
      error,
      filter: filterModel,
      reflectionActionError,
      reflectionActionErrorOutcomeId,
      retirement,
      retirementError,
      retirementNote,
      retirementReason,
      reviews,
    },
  };
}
