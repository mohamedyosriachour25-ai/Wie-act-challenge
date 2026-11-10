import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useAIStatus } from "../lib/hooks.js";
import { useT } from "../lib/i18n.jsx";
import { statusText } from "./Common.jsx";

function Stat({ label, value, hint }) {
  return (
    <div className="stat" title={hint}>
      <dd>{value}</dd>
      <dt>{label}</dt>
    </div>
  );
}

function MetricsPanel() {
  const t = useT();
  const [m, setM] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api("/api/ai/metrics").then((d) => alive && setM(d)).catch(() => {});
    load();
    const id = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
  if (!m) return <p className="muted small">{t("Loading usage…")}</p>;
  const share = m.compact_share == null ? t("none yet") : t("{n}% compact", { n: Math.round(m.compact_share * 100) });
  return (
    <div className="metrics">
      <dl className="stats">
        <Stat label={t("Model calls")} value={m.calls.total} hint={t("Calls that reached the service")} />
        <Stat label={t("Compact / mid-size")} value={`${m.calls.compact} / ${m.calls.mid}`} hint={t("The platform serves about 60% compact and 40% mid-size")} />
        <Stat label={t("Split")} value={share} />
        <Stat label={t("Tokens in / out")} value={`${m.tokens_in.compact + m.tokens_in.mid} / ${m.tokens_out.compact + m.tokens_out.mid}`} />
        <Stat label={t("Answers reused")} value={m.cache_hits} hint={t("Served from cache, no call")} />
        <Stat label={t("Requests merged")} value={m.coalesced} hint={t("Identical requests at the same time share one call")} />
        <Stat label={t("Quota waits")} value={`${m.quota_handled}/${m.quota_refusals}`} hint={t("Refusals handled by waiting, out of all refusals")} />
        <Stat label={t("Fallbacks")} value={m.fallbacks} hint={t("Mid-size step answered by the compact model")} />
      </dl>
    </div>
  );
}

/** Header chip: what the AI is doing right now, with the metrics one click away. */
export default function AIStatus() {
  const t = useT();
  const status = useAIStatus();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);
  if (!status) return null;

  let label = t("AI ready");
  let tone = "ok";
  if (status.state === "working") { label = t("AI is working"); tone = "busy"; }
  if (status.state === "queued") { label = status.retry_in ? t("AI queued, {n} s", { n: status.retry_in }) : t("AI queued"); tone = "wait"; }

  return (
    <div className="ai-status">
      <button className={`ai-chip ${tone}`} onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="ai-panel">
        <span className="dot" aria-hidden="true" />
        {label}
      </button>
      {open && (
        <div id="ai-panel" className="popover" role="region" aria-label={t("AI usage")}>
          <h3>{t("AI usage")}</h3>
          {status.state === "queued" && <p className="small">{statusText(t, status)}</p>}
          <MetricsPanel />
        </div>
      )}
      {(status.state === "queued" || status.notice) && (
        <p className={`banner ${status.state === "queued" ? "wait" : "info"}`} role="status">
          {status.state === "queued" ? statusText(t, status) : t("The larger model is busy, so a compact model answered. The answer may be shorter.")}
        </p>
      )}
    </div>
  );
}
