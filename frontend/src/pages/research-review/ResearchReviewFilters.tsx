import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { type RecentInstrument } from "../../api/client";
import {
  RecentInstrumentDatalist,
  recentInstrumentListId,
  useRecentInstruments,
} from "../../components/Instruments";

const statusGroups = [
  "all",
  "needs_attention",
  "in_progress",
  "feedback_available",
  "feedback_ineligible_or_retired",
] as const;

export type ResearchReviewFiltersViewModel = {
  instruments: RecentInstrument[];
  market: string;
  q: string;
  statusGroup: string;
  ticker: string;
};

type ResearchReviewFilterActions = {
  changeMarket: (value: string) => void;
  changeQuery: (value: string) => void;
  changeStatusGroup: (value: string) => void;
  changeTicker: (value: string) => void;
  submit: (event: FormEvent<HTMLFormElement>) => void;
};

export function useResearchReviewFilters(
  load: (query: string) => void,
): {
  actions: ResearchReviewFilterActions;
  model: ResearchReviewFiltersViewModel;
} {
  const initialParams = new URLSearchParams(window.location.search);
  const [q, setQ] = useState(() => initialParams.get("q") ?? "");
  const [ticker, setTicker] = useState(
    () => initialParams.get("ticker") ?? "",
  );
  const [market, setMarket] = useState(
    () => initialParams.get("market") ?? "",
  );
  const [statusGroup, setStatusGroup] = useState(
    () => initialParams.get("status_group") ?? "all",
  );
  const instruments = useRecentInstruments();

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (ticker.trim()) params.set("ticker", ticker.trim());
    if (market.trim()) params.set("market", market.trim());
    if (statusGroup !== "all") params.set("status_group", statusGroup);
    const query = params.size ? `?${params}` : "";
    window.history.replaceState(null, "", `/reviews${query}`);
    load(query);
  };

  return {
    actions: {
      changeMarket: setMarket,
      changeQuery: setQ,
      changeStatusGroup: setStatusGroup,
      changeTicker: setTicker,
      submit,
    },
    model: { instruments, market, q, statusGroup, ticker },
  };
}

export function ResearchReviewFilters({
  actions,
  model,
}: {
  actions: ResearchReviewFilterActions;
  model: ResearchReviewFiltersViewModel;
}) {
  const { t } = useTranslation();
  return (
    <form className="panel filter-bar" onSubmit={actions.submit}>
      <label>
        {t("reviewSearch")}
        <input
          id="review-search"
          name="q"
          autoComplete="on"
          value={model.q}
          onChange={(event) => actions.changeQuery(event.target.value)}
          placeholder={t("reviewSearchPlaceholder")}
        />
      </label>
      <label>
        {t("ticker")}
        <input
          id="review-ticker"
          name="ticker"
          autoComplete="on"
          list={recentInstrumentListId}
          spellCheck={false}
          value={model.ticker}
          onChange={(event) => actions.changeTicker(event.target.value)}
        />
        <RecentInstrumentDatalist instruments={model.instruments} />
      </label>
      <label>
        {t("market")}
        <input
          id="review-market"
          name="market"
          autoComplete="on"
          value={model.market}
          onChange={(event) => actions.changeMarket(event.target.value)}
          placeholder="Asia/Tokyo"
        />
      </label>
      <label>
        {t("reviewStatus")}
        <select
          id="review-status-group"
          name="status_group"
          value={model.statusGroup}
          onChange={(event) => actions.changeStatusGroup(event.target.value)}
        >
          {statusGroups.map((group) => (
            <option key={group} value={group}>
              {t(`reviewStatusGroup.${group}`)}
            </option>
          ))}
        </select>
      </label>
      <button className="button primary">{t("apply")}</button>
    </form>
  );
}
