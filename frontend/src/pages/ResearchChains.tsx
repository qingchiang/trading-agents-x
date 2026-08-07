import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchChain } from "../api/client";
import { Link } from "../router";

export default function ResearchChains() {
  const { t } = useTranslation();
  const [chains, setChains] = useState<ResearchChain[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    void api.researchChains().then(setChains).catch((cause) => {
      setError(cause instanceof Error ? cause.message : t("error"));
    });
  }, [t]);

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("currentResearchState")}</p>
          <h1>{t("researchChains")}</h1>
        </div>
      </header>
      {error && <div className="alert">{error}</div>}
      <div className="card-grid">
        {chains.map((chain) => (
          <Link className="panel" key={chain.id} to={`/research/${chain.id}`}>
            <h2>{chain.instrument}</h2>
            <p>{chain.current_revision?.current_state.opinion.thesis}</p>
            <small>
              {chain.is_primary ? `${t("primaryChain")} · ` : ""}
              {chain.current_revision?.cutoff}
            </small>
          </Link>
        ))}
      </div>
    </section>
  );
}
