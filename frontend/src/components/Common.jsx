import { useEffect, useState } from "react";
import { useAuth } from "../lib/auth.jsx";
import { useAIStatus, mmss } from "../lib/hooks.js";
import { tx, useT } from "../lib/i18n.jsx";

/** Live gateway status in the reader's language. The server sends a code and numbers, never a sentence to display. */
export function statusText(t, status) {
  if (!status) return "";
  if (status.code === "quota") return t("The AI quota is refilling. Your request resumes in {n} s.", { n: status.retry_in });
  if (status.code === "embed_quota") return t("The embeddings quota is refilling. Resuming in {n} s.", { n: status.retry_in });
  if (status.code === "busy") return t(status.cls === "mid" ? tx("The mid-size model is busy. Retrying in {n} s.") : tx("The compact model is busy. Retrying in {n} s."), { n: status.retry_in });
  return "";
}

export function AuthCard({ note }) {
  const { start } = useAuth();
  const t = useT();
  const [mode, setMode] = useState("register");
  const [pseudo, setPseudo] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await start(mode, pseudo, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="auth-card" onSubmit={submit}>
      <h2>{mode === "register" ? t("Create your study account") : t("Sign in")}</h2>
      {note && <p className="muted">{note}</p>}
      <label>
        {t("Pseudo")}
        <input value={pseudo} onChange={(e) => setPseudo(e.target.value)} autoComplete="username" required minLength={3} maxLength={20} placeholder={t("e.g. quiet_owl")} />
      </label>
      {mode === "register" && <p className="muted small">{t("Other learners see this pseudo. Pick one that is not your real name.")}</p>}
      <label>
        {t("Password")}
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "register" ? "new-password" : "current-password"} required minLength={6} />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn primary" disabled={busy}>{busy ? t("One moment") : mode === "register" ? t("Create account") : t("Sign in")}</button>
      <button type="button" className="link" onClick={() => { setMode(mode === "register" ? "login" : "register"); setError(""); }}>
        {mode === "register" ? t("I already have an account") : t("Create a new account")}
      </button>
    </form>
  );
}

export function AuthGate({ children, note }) {
  const { user, ready } = useAuth();
  const t = useT();
  if (!ready) return <p className="muted pad">{t("Loading your account…")}</p>;
  if (!user) return <div className="gate"><AuthCard note={note} /></div>;
  return children;
}

/** Waiting is a design budget: show what is happening instead of a frozen screen. */
export function StageLoader({ stages }) {
  const [i, setI] = useState(0);
  const status = useAIStatus();
  const t = useT();
  useEffect(() => {
    const id = setInterval(() => setI((n) => Math.min(n + 1, stages.length - 1)), 2600);
    return () => clearInterval(id);
  }, [stages.length]);
  const waiting = status?.state === "queued";
  return (
    <div className="loader" role="status" aria-live="polite">
      <div className="bar"><span /></div>
      <ol className="stages">
        {stages.map((s, n) => (
          <li key={s} className={n < i ? "done" : n === i ? "now" : ""}>{s}</li>
        ))}
      </ol>
      {waiting && <p className="small wait-note">{statusText(t, status)}</p>}
    </div>
  );
}

export function TimerRing({ cycle, label }) {
  const t = useT();
  if (!cycle) return null;
  const R = 78, C = 2 * Math.PI * R;
  const frac = cycle.length ? cycle.into / cycle.length : 0;
  return (
    <div className={`ring ${cycle.phase}`} role="timer" aria-label={t("{phase} time left {time}", { phase: cycle.phase === "focus" ? t("Focus") : t("Break"), time: mmss(cycle.remaining) })}>
      <svg viewBox="0 0 200 200">
        <circle cx="100" cy="100" r={R} className="ring-track" />
        <circle cx="100" cy="100" r={R} className="ring-fill" strokeDasharray={C} strokeDashoffset={C * frac} transform="rotate(-90 100 100)" />
      </svg>
      <div className="ring-text">
        <strong>{mmss(cycle.remaining)}</strong>
        <span>{label || (cycle.phase === "focus" ? t("Focus") : t("Break"))}</span>
      </div>
    </div>
  );
}

export function Dots({ n, filled = n, max = 12, label }) {
  const shown = Math.min(n, max);
  return (
    <span className="dots" role="img" aria-label={label}>
      {Array.from({ length: shown }, (_, i) => <i key={i} className={i < filled ? "f" : ""} />)}
      {n > max && <b aria-hidden="true">+</b>}
    </span>
  );
}
