import { useT } from "../lib/i18n.jsx";
import { useMemo, useState } from "react";

const W = 420, H = 300, CX = 215, CY = 150, SX = 22, SY = 16; // x in [-9,9], y in [-9,9]
const KINDS = {
  linear: { f: (x, a, b) => a * x + b, vars: ["a", "b"], text: (a, b) => `y = ${poly([[a, "x"], [b, ""]])}` },
  quadratic: { f: (x, a, b, c) => a * x * x + b * x + c, vars: ["a", "b", "c"], text: (a, b, c) => `y = ${poly([[a, "x²"], [b, "x"], [c, ""]])}` },
  sine: { f: (x, a, b, c) => a * Math.sin(b * x + c), vars: ["a", "b", "c"], text: (a, b, c) => `y = ${a}·sin(${b}x ${sgn(c)})` },
  exponential: { f: (x, a, b, c) => a * Math.pow(2, b * x) + c, vars: ["a", "b", "c"], text: (a, b, c) => `y = ${a}·2^(${b}x) ${sgn(c)}` },
};
const RANGE = { exponential: { b: [-1, 1, 0.1] }, sine: { b: [0.2, 3, 0.1], c: [-3, 3, 0.1] } };
// Writes a polynomial the way a teacher would: no "1x", no "+ 0x".
const poly = (terms) => {
  let out = "";
  terms.forEach(([coef, sym]) => {
    const c = +coef.toFixed(2);
    if (c === 0) return;
    const abs = Math.abs(c);
    const body = abs === 1 && sym ? sym : `${abs}${sym}`;
    out += out ? (c < 0 ? " − " : " + ") + body : (c < 0 ? "−" : "") + body;
  });
  return out || "0";
};
const sgn = (v) => (v < 0 ? `− ${Math.abs(+v.toFixed(2))}` : `+ ${+v.toFixed(2)}`);
const px = (x) => CX + x * SX;
const py = (y) => CY - y * SY;

export default function FunctionPlot({ params, focus }) {
  const t = useT();
  const kind = KINDS[params.kind] ? params.kind : "quadratic";
  const spec = KINDS[kind];
  const [v, setV] = useState({ a: params.a, b: params.b, c: params.c });

  const path = useMemo(() => {
    let d = "", pen = false;
    for (let i = 0; i <= 360; i++) {
      const x = -9 + (18 * i) / 360;
      const y = spec.f(x, v.a, v.b, v.c);
      if (!Number.isFinite(y) || Math.abs(y) > 11) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${px(x).toFixed(1)},${py(y).toFixed(1)} `;
      pen = true;
    }
    return d;
  }, [kind, v.a, v.b, v.c]);

  const marks = [];
  if (kind === "quadratic" && v.a !== 0) {
    const disc = v.b * v.b - 4 * v.a * v.c;
    marks.push({ x: -v.b / (2 * v.a), y: v.c - (v.b * v.b) / (4 * v.a), name: "vertex" });
    if (disc >= 0) [(-v.b + Math.sqrt(disc)) / (2 * v.a), (-v.b - Math.sqrt(disc)) / (2 * v.a)].forEach((x) => marks.push({ x, y: 0, name: "root" }));
  }
  const rootCount = marks.filter((m) => m.name === "root").length;
  const shown = spec.vars.map((n) => (n === "b" && kind === "linear" ? "b" : n));

  return (
    <div className="fig">
      <svg className="plot" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("Graph of {formula}", { formula: spec.text(v.a, v.b, v.c) })}>
        <defs><clipPath id="plot-clip"><rect x="0" y="0" width={W} height={H} /></clipPath></defs>
        {[-8, -6, -4, -2, 2, 4, 6, 8].map((n) => (
          <g key={n}>
            <line x1={px(n)} y1="0" x2={px(n)} y2={H} className="fig-grid" />
            <line x1="0" y1={py(n)} x2={W} y2={py(n)} className="fig-grid" />
          </g>
        ))}
        <line x1="0" y1={CY} x2={W} y2={CY} className="fig-axis" />
        <line x1={CX} y1="0" x2={CX} y2={H} className="fig-axis" />
        <g clipPath="url(#plot-clip)"><path d={path} className="fig-curve" fill="none" /></g>
        {marks.map((m, i) => (
          <g key={i} className={m.name === "root" && focus === "roots" ? "pulse" : ""}>
            <circle cx={px(m.x)} cy={py(m.y)} r={m.name === "root" ? 5.5 : 4.5} className={m.name === "root" ? "fig-dot-hot" : "fig-dot"} />
            <text x={px(m.x) + 8} y={py(m.y) + (m.name === "root" ? -8 : 18)} className="fig-small">{m.name === "root" ? `x = ${+m.x.toFixed(2)}` : t("vertex")}</text>
          </g>
        ))}
      </svg>

      <div className="fig-callout">
        <p className="fig-callout-title"><mark>{spec.text(+v.a.toFixed(2), +v.b.toFixed(2), +v.c.toFixed(2))}</mark></p>
        <div className="sliders">
          {shown.map((n) => {
            const [lo, hi, step] = RANGE[kind]?.[n] || [-5, 5, 0.5];
            return (
              <label key={n} className={focus === n ? "lit" : ""}>
                <span>{n} = {+v[n].toFixed(2)}</span>
                <input type="range" min={lo} max={hi} step={step} value={Math.min(hi, Math.max(lo, v[n]))} onChange={(e) => setV({ ...v, [n]: +e.target.value })} />
              </label>
            );
          })}
        </div>
        {kind === "quadratic" && (
          <p className="muted small">{rootCount === 0 ? t("No real roots: the curve never crosses the horizontal axis.") : rootCount === 1 ? t("One root: where the curve crosses the horizontal axis.") : t("Two roots: where the curve crosses the horizontal axis.")}</p>
        )}
      </div>
    </div>
  );
}
