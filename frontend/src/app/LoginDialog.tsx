import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useModal } from "../shared/Interaction";
import { api } from "../shared/api/client";

export default function LoginDialog({
  onAuthenticated,
}: {
  onAuthenticated: () => void;
}) {
  const { t } = useTranslation();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const ref = useModal<HTMLFormElement>(true, () => {}, true);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api.login(token);
      setToken("");
      onAuthenticated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    } finally { setBusy(false); }
  };
  return (
    <div className="modal-backdrop">
      <form className="login-card" onSubmit={submit} ref={ref} role="dialog" aria-modal="true" aria-label={t("loginTitle")}>
        <div className="brand-mark">TX</div>
        <h1>{t("loginTitle")}</h1>
        <p>{t("loginHint")}</p>
        <label>
          {t("token")}
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </label>
        {error && <p className="form-error">{error}</p>}
        <button className="primary" type="submit" disabled={busy}>
          {t("signIn")}
        </button>
      </form>
    </div>
  );
}
