import { useT } from "../lib/i18n.jsx";
import { useEffect, useState } from "react";

/** Two or three things compared side by side, row by row (causes vs effects, pros vs cons, A vs B). */
export default function Comparison({ params, focus }) {
  const t = useT();
  const { columns, rows } = params;
  const n = rows.length;
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (/^\d+$/.test(focus || "") && +focus < n) setActive(+focus);
  }, [focus, n]);

  return (
    <div className="fig">
      <table className="fig-table" role="table" aria-label={t("A comparison across {n} aspects", { n })}>
        <thead>
          <tr>
            <th scope="col" />
            {columns.map((c, j) => <th key={j} scope="col">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={i === active ? "on" : ""} onClick={() => setActive(i)} style={{ cursor: "pointer" }}
              role="button" tabIndex={0} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setActive(i)} aria-label={r.label}>
              <th scope="row">{r.label}</th>
              {r.cells.map((cell, j) => <td key={j}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="fig-callout" aria-live="polite">
        <p className="fig-callout-title"><mark>{rows[active].label}</mark></p>
        <p>
          {columns.map((c, j) => `${c}: ${rows[active].cells[j]}`).join(" — ")}
        </p>
        <div className="row">
          <button type="button" className="btn small" onClick={() => setActive((a) => Math.max(0, a - 1))} disabled={active === 0}>{t("Previous row")}</button>
          <button type="button" className="btn small" onClick={() => setActive((a) => Math.min(n - 1, a + 1))} disabled={active === n - 1}>{t("Next row")}</button>
        </div>
      </div>
    </div>
  );
}
