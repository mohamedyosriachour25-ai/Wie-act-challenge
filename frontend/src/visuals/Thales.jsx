import { useT } from "../lib/i18n.jsx";
import { useRef, useState } from "react";

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const lerp = (p, q, t) => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);
const UNIT = 28; // pixels per displayed unit

export default function Thales({ params, focus }) {
  const tr = useT();
  const [t, setT] = useState(params.ratio ?? 0.5);
  const [hot, setHot] = useState(null); // which ratio chip is hovered
  const svg = useRef(null);
  const [L1, L2, L3] = params.labels || ["A", "B", "C"];

  const A = params.right_angle ? [80, 40] : [210, 40];
  const B = params.right_angle ? [80, 270] : [70, 270];
  const C = params.right_angle ? [360, 270] : [350, 270];
  const D = lerp(A, B, t);
  const E = lerp(A, C, t);

  const len = { AB: dist(A, B) / UNIT, AC: dist(A, C) / UNIT, BC: dist(B, C) / UNIT };
  const ratios = [
    { id: "AD", top: `${L1}D`, bottom: `${L1}${L2}`, a: t * len.AB, b: len.AB, seg: ["AD", "AB"] },
    { id: "AE", top: `${L1}E`, bottom: `${L1}${L3}`, a: t * len.AC, b: len.AC, seg: ["AE", "AC"] },
    { id: "DE", top: "DE", bottom: `${L2}${L3}`, a: t * len.BC, b: len.BC, seg: ["DE", "BC"] },
  ];
  const lit = (name) => (hot ? hot.seg.includes(name) : focus === name);
  const small = focus === "ADE";

  const move = (e) => {
    const pt = svg.current.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.current.getScreenCTM().inverse());
    const ab = [B[0] - A[0], B[1] - A[1]];
    const proj = ((p.x - A[0]) * ab[0] + (p.y - A[1]) * ab[1]) / (ab[0] ** 2 + ab[1] ** 2);
    setT(clamp(proj, 0.15, 0.9));
  };
  const key = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") setT((v) => clamp(v + 0.05, 0.15, 0.9));
    if (e.key === "ArrowUp" || e.key === "ArrowLeft") setT((v) => clamp(v - 0.05, 0.15, 0.9));
  };

  const seg = (p, q, name, base = 2.2) => (
    <>
      {lit(name) && <line x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} className="fig-hot" strokeWidth="11" />}
      <line x1={p[0]} y1={p[1]} x2={q[0]} y2={q[1]} className={name === "DE" ? "fig-accent" : "fig-ink"} strokeWidth={name === "DE" ? 3.2 : base} />
    </>
  );
  const tick = (p, q) => {
    const m = lerp(p, q, 0.5);
    return <path d={`M${m[0] - 6},${m[1] - 6} l8,6 l-8,6`} className="fig-accent" fill="none" strokeWidth="2" />;
  };

  return (
    <div className="fig">
      <svg ref={svg} viewBox="0 0 420 310" role="img" aria-label={tr("Triangle {abc} with a line DE parallel to {bc}", { abc: `${L1}${L2}${L3}`, bc: `${L2}${L3}` })}>
        <polygon points={[A, D, E].map((p) => p.join(",")).join(" ")} className={small ? "fig-hot-fill strong" : "fig-hot-fill"} />
        <polygon points={[A, B, C].map((p) => p.join(",")).join(" ")} fill="none" />
        {seg(A, B, "AB")}
        {seg(A, C, "AC")}
        {seg(B, C, "BC")}
        {lit("AD") && <line x1={A[0]} y1={A[1]} x2={D[0]} y2={D[1]} className="fig-accent" strokeWidth="4" />}
        {lit("AE") && <line x1={A[0]} y1={A[1]} x2={E[0]} y2={E[1]} className="fig-accent" strokeWidth="4" />}
        {seg(D, E, "DE")}
        {tick(D, E)}
        {tick(B, C)}
        {params.right_angle && <path d={`M${B[0]},${B[1] - 18} h18 v18`} className="fig-ink" fill="none" strokeWidth="1.6" transform={`translate(0,0)`} />}

        <text x={A[0]} y={A[1] - 12} textAnchor="middle" className="fig-label">{L1}</text>
        <text x={B[0] - 14} y={B[1] + 16} textAnchor="middle" className="fig-label">{L2}</text>
        <text x={C[0] + 14} y={C[1] + 16} textAnchor="middle" className="fig-label">{L3}</text>
        <text x={E[0] + 14} y={E[1] + 4} className="fig-label">E</text>

        <circle cx={E[0]} cy={E[1]} r="4.5" className="fig-dot" />
        <circle cx={D[0]} cy={D[1]} r="16" fill="transparent" style={{ cursor: "grab", touchAction: "none" }}
          onPointerDown={(e) => e.currentTarget.setPointerCapture(e.pointerId)}
          onPointerMove={(e) => e.buttons && move(e)} />
        <circle cx={D[0]} cy={D[1]} r="8" className="fig-handle" tabIndex={0} role="slider" aria-label={tr("Move D along {ab}", { ab: `${L1}${L2}` })}
          aria-valuemin={0.15} aria-valuemax={0.9} aria-valuenow={Number(t.toFixed(2))} onKeyDown={key} />
        <text x={D[0] - 16} y={D[1] + 4} textAnchor="end" className="fig-label">D</text>
      </svg>

      <div className="fig-callout" aria-live="polite">
        <p className="fig-callout-title">
          <mark>{ratios[0].top}/{ratios[0].bottom} = {ratios[1].top}/{ratios[1].bottom} = {ratios[2].top}/{ratios[2].bottom} = {t.toFixed(2)}</mark>
        </p>
        <ul className="chips">
          {ratios.map((r) => (
            <li key={r.id}>
              <button type="button" className={`chip${hot?.id === r.id ? " on" : ""}`}
                onMouseEnter={() => setHot(r)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(r)} onBlur={() => setHot(null)}>
                {r.top}/{r.bottom} = {r.a.toFixed(1)}/{r.b.toFixed(1)}
              </button>
            </li>
          ))}
        </ul>
        <p className="muted small">{tr("Drag the dot D along {ab} (or use the arrow keys). The line DE stays parallel to {bc} and the three ratios stay equal.", { ab: `${L1}${L2}`, bc: `${L2}${L3}` })}</p>
      </div>
    </div>
  );
}
