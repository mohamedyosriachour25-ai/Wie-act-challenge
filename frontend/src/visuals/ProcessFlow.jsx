import { useT } from "../lib/i18n.jsx";
import { useEffect, useState } from "react";

import { wrap } from "./util.js";

export default function ProcessFlow({ params, focus }) {
  const t = useT();
  const steps = params.steps;
  const n = steps.length;
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (/^\d+$/.test(focus || "") && +focus < n) setActive(+focus);
  }, [focus, n]);

  const cycle = params.cycle;
  const BW = cycle ? 124 : 300, BH = 44, GAP = 26;
  const height = cycle ? 320 : n * (BH + GAP) - GAP + 20;
  const pos = steps.map((_, i) => {
    if (!cycle) return [210, 10 + BH / 2 + i * (BH + GAP)];
    const ang = -Math.PI / 2 + (2 * Math.PI * i) / n;
    return [210 + 128 * Math.cos(ang), 160 + 112 * Math.sin(ang)];
  });

  const arrows = steps.map((_, i) => {
    const next = i + 1 < n ? i + 1 : cycle ? 0 : null;
    if (next === null) return null;
    const [x1, y1] = pos[i], [x2, y2] = pos[next];
    const dx = x2 - x1, dy = y2 - y1, d = Math.hypot(dx, dy) || 1;
    const tx = (BW / 2) / (Math.abs(dx / d) || 1e-6), ty = (BH / 2) / (Math.abs(dy / d) || 1e-6);
    const trim = Math.min(tx, ty) + 4;
    return <line key={i} x1={x1 + (dx / d) * trim} y1={y1 + (dy / d) * trim} x2={x2 - (dx / d) * trim} y2={y2 - (dy / d) * trim} className="fig-ink" strokeWidth="1.8" markerEnd="url(#arrow)" />;
  });

  return (
    <div className="fig">
      <svg viewBox={`0 0 420 ${height}`} role="img" aria-label={cycle ? t("A cycle of steps") : t("A sequence of steps")}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" className="fig-ink-fill" /></marker>
        </defs>
        {arrows}
        {steps.map((s, i) => {
          const [x, y] = pos[i];
          const lines = wrap(s.label, cycle ? 17 : 38);
          return (
            <g key={i} onClick={() => setActive(i)} style={{ cursor: "pointer" }} role="button" tabIndex={0}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setActive(i)} aria-label={t("Step {n}: {label}", { n: i + 1, label: s.label })}>
              <rect x={x - BW / 2} y={y - BH / 2} width={BW} height={BH} rx="8" className={i === active ? "fig-node on" : "fig-node"} />
              {lines.map((l, j) => (
                <text key={j} x={x} y={y + 4 + (j - (lines.length - 1) / 2) * 15} textAnchor="middle" className="fig-label sm">{l}</text>
              ))}
            </g>
          );
        })}
      </svg>
      <div className="fig-callout" aria-live="polite">
        <p className="fig-callout-title"><mark>{t("Step {i} of {n}: {label}", { i: active + 1, n, label: steps[active].label })}</mark></p>
        {steps[active].detail && <p>{steps[active].detail}</p>}
        <div className="row">
          <button type="button" className="btn small" onClick={() => setActive((a) => (a - 1 + n) % n)} disabled={!cycle && active === 0}>{t("Previous step")}</button>
          <button type="button" className="btn small" onClick={() => setActive((a) => (a + 1) % n)} disabled={!cycle && active === n - 1}>{t("Next step")}</button>
        </div>
      </div>
    </div>
  );
}
