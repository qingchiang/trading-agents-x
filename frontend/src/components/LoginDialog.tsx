import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";

export default function LoginDialog({
  onAuthenticated,
}: {
  onAuthenticated: () => void;
}) {
  const { t } = useTranslation();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    try {
      await api.login(token);
      setToken("");
      onAuthenticated();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };
  return (
    <div className="modal-backdrop">
      <form className="login-card" onSubmit={submit}>
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
        <button className="primary" type="submit">
          {t("signIn")}
        </button>
      </form>
    </div>
  );
}
