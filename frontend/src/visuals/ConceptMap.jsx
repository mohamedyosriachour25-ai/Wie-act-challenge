import { useT } from "../lib/i18n.jsx";
import { useEffect, useState } from "react";
import { wrap } from "./util.js";


export default function ConceptMap({ params, focus }) {
  const t = useT();
  const { nodes, edges } = params;
  const [sel, setSel] = useState(nodes[0].id);

  useEffect(() => {
    const hit = nodes.find((n) => n.id === focus);
    if (hit) setSel(hit.id);
  }, [focus, nodes]);

  const pos = {};
  nodes.forEach((n, i) => {
    if (i === 0) { pos[n.id] = [210, 150]; return; }
    const ang = -Math.PI / 2 + (2 * Math.PI * (i - 1)) / (nodes.length - 1);
    pos[n.id] = [210 + 148 * Math.cos(ang), 150 + 108 * Math.sin(ang)];
  });
  const active = nodes.find((n) => n.id === sel) || nodes[0];
  const linked = (e) => e.from === sel || e.to === sel;

  return (
    <div className="fig">
      <svg viewBox="0 0 420 300" role="img" aria-label={`Concept map centred on ${nodes[0].label}`}>
        {edges.map((e, i) => {
          const [x1, y1] = pos[e.from], [x2, y2] = pos[e.to];
          return (
            <g key={i} opacity={linked(e) ? 1 : 0.35}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} className={linked(e) ? "fig-accent" : "fig-ink"} strokeWidth={linked(e) ? 2.6 : 1.4} />
              {e.label && <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 4} textAnchor="middle" className="fig-small">{e.label}</text>}
            </g>
          );
        })}
        {nodes.map((n, i) => {
          const [x, y] = pos[n.id];
          const core = i === 0;
          const w = core ? 128 : 100, h = core ? 52 : 40;
          const lines = wrap(n.label, core ? 15 : 12);
          return (
            <g key={n.id} onClick={() => setSel(n.id)} style={{ cursor: "pointer" }} role="button" tabIndex={0}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setSel(n.id)} aria-label={n.label}>
              <title>{n.label}</title>
              <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx="12" className={n.id === sel ? "fig-node on" : core ? "fig-node core" : "fig-node"} />
              {lines.map((l, j) => (
                <text key={j} x={x} y={y + 4 + (j - (lines.length - 1) / 2) * 14} textAnchor="middle" className="fig-label sm">{l}</text>
              ))}
            </g>
          );
        })}
      </svg>
      <div className="fig-callout" aria-live="polite">
        <p className="fig-callout-title"><mark>{active.label}</mark></p>
        <p>{active.note || t("Select another idea on the map to read its note.")}</p>
      </div>
    </div>
  );
}
