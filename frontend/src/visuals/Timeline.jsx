import { useT } from "../lib/i18n.jsx";
import { useEffect, useState } from "react";
import { wrap } from "./util.js";

/** A chronological sequence of events, drawn along a single line. */
export default function Timeline({ params, focus }) {
  const t = useT();
  const events = params.events;
  const n = events.length;
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (/^\d+$/.test(focus || "") && +focus < n) setActive(+focus);
  }, [focus, n]);

  const W = 420, PAD = 40;
  const step = n > 1 ? (W - PAD * 2) / (n - 1) : 0;
  const y = 70;

  return (
    <div className="fig">
      <svg viewBox={`0 0 ${W} 180`} role="img" aria-label={t("A timeline of {n} events", { n })}>
        <line x1={PAD} y1={y} x2={W - PAD} y2={y} className="fig-axis" />
        {events.map((e, i) => {
          const x = PAD + step * i;
          const lines = wrap(e.label, 16);
          const below = i % 2 === 1;
          return (
            <g key={i} onClick={() => setActive(i)} style={{ cursor: "pointer" }} role="button" tabIndex={0}
              onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && setActive(i)}
              aria-label={t("Event {n}: {date}, {label}", { n: i + 1, date: e.date, label: e.label })}>
              <circle cx={x} cy={y} r={i === active ? 8 : 6} className={i === active ? "fig-dot-hot" : "fig-dot"} />
              <text x={x} y={y - 16} textAnchor="middle" className="fig-small">{e.date}</text>
              {lines.map((l, j) => (
                <text key={j} x={x} y={below ? y + 24 + j * 14 : y - 32 - (lines.length - 1 - j) * 14} textAnchor="middle" className="fig-label sm">{l}</text>
              ))}
            </g>
          );
        })}
      </svg>
      <div className="fig-callout" aria-live="polite">
        <p className="fig-callout-title"><mark>{t("{date}: {label}", { date: events[active].date, label: events[active].label })}</mark></p>
        {events[active].detail && <p>{events[active].detail}</p>}
        <div className="row">
          <button type="button" className="btn small" onClick={() => setActive((a) => Math.max(0, a - 1))} disabled={active === 0}>{t("Previous event")}</button>
          <button type="button" className="btn small" onClick={() => setActive((a) => Math.min(n - 1, a + 1))} disabled={active === n - 1}>{t("Next event")}</button>
        </div>
      </div>
    </div>
  );
}
