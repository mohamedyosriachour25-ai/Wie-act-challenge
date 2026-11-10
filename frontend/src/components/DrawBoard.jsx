import { useEffect, useRef, useState } from "react";
import { useT } from "../lib/i18n.jsx";

/**
 * A small shared drawing board for Pictionary. The performer draws; strokes are sent to the room's game players
 * (op "stroke", normalised 0..1 points) and echoed back so watchers see the same board. Everything is plain canvas
 * 2D — no eval, no external library — so it runs under the strict CSP. Colours are an index into PALETTE, shared by
 * every client, so only the index travels. The last entry is white and doubles as the eraser (the board is white).
 */
const PALETTE = ["#1f2430", "#e23b3b", "#f08a24", "#f4c20d", "#2fa84f", "#2f6df0", "#8b46c6", "#7a5230", "#e86aa6", "#8a94a6", "#ffffff"];
const ERASER = PALETTE.length - 1;           // white
const SIZES = [0.006, 0.013, 0.022];          // pen widths, as a fraction of the board
const ERASER_W = 0.05;
const SIDE = 360;                              // backing-store pixels (CSS scales it responsively)

export default function DrawBoard({ editable, send, drawRef }) {
  const t = useT();
  const canvas = useRef(null);
  const ctx = useRef(null);
  const [color, setColor] = useState(0);
  const [size, setSize] = useState(1);
  const [erase, setErase] = useState(false);
  const drawing = useRef(false);
  const pts = useRef([]);        // current stroke, normalised
  const sentTo = useRef(0);      // index in pts already sent
  const flush = useRef(null);

  // ---- rendering helpers (operate on normalised points)
  const line = (points, c, w) => {
    const g = ctx.current;
    if (!g || points.length === 0) return;
    g.strokeStyle = PALETTE[c] || "#1f2430";
    g.lineWidth = Math.max(1, w * SIDE);
    g.lineCap = "round";
    g.lineJoin = "round";
    g.beginPath();
    g.moveTo(points[0][0] * SIDE, points[0][1] * SIDE);
    for (let i = 1; i < points.length; i++) g.lineTo(points[i][0] * SIDE, points[i][1] * SIDE);
    if (points.length === 1) g.lineTo(points[0][0] * SIDE + 0.01, points[0][1] * SIDE); // a dot
    g.stroke();
  };
  const clear = () => { const g = ctx.current; if (g) g.clearRect(0, 0, SIDE, SIDE); };

  useEffect(() => {
    ctx.current = canvas.current?.getContext("2d") || null;
    clear();
    // incoming messages from the room: apply to the board
    const apply = (m) => {
      if (m.op === "clear") clear();
      else if (m.op === "stroke") line(m.pts, m.c, m.w);
      else if (m.op === "replay") { clear(); (m.strokes || []).forEach((s) => line(s.pts, s.c, s.w)); }
    };
    if (drawRef) drawRef.current = apply;
    if (!editable) send({ type: "draw", op: "sync" });  // a watcher: ask for the strokes drawn so far
    return () => { if (drawRef && drawRef.current === apply) drawRef.current = null; };
  }, [editable]);

  // ---- performer input
  const at = (e) => {
    const r = canvas.current.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
            Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const curColor = () => (erase ? ERASER : color);
  const curWidth = () => (erase ? ERASER_W : SIZES[size]);
  const sendSeg = () => {
    if (pts.current.length > sentTo.current) {
      const seg = pts.current.slice(Math.max(0, sentTo.current - 1));  // overlap one point for a continuous line
      send({ type: "draw", op: "stroke", pts: seg, c: curColor(), w: curWidth() });
      sentTo.current = pts.current.length;
    }
  };
  const down = (e) => {
    if (!editable) return;
    e.preventDefault();
    try { canvas.current.setPointerCapture?.(e.pointerId); } catch { /* pointer already gone: draw without capture */ }
    drawing.current = true;
    pts.current = [at(e)];
    sentTo.current = 0;
    line(pts.current, curColor(), curWidth());
    flush.current = setInterval(sendSeg, 60);
  };
  const move = (e) => {
    if (!drawing.current) return;
    const p = at(e);
    const prev = pts.current[pts.current.length - 1];
    pts.current.push(p);
    line([prev, p], curColor(), curWidth());
  };
  const up = () => {
    if (!drawing.current) return;
    drawing.current = false;
    clearInterval(flush.current);
    sendSeg();
    pts.current = [];
    sentTo.current = 0;
  };
  const clearAll = () => { clear(); send({ type: "draw", op: "clear" }); };

  return (
    <div className="draw-board">
      <canvas
        ref={canvas} width={SIDE} height={SIDE}
        className={`draw-canvas${editable ? " editable" : ""}`}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
        role="img" aria-label={editable ? t("Drawing area — draw the word") : t("The drawing appears here")}
      />
      {editable && (
        <div className="draw-tools">
          <div className="swatches">
            {PALETTE.slice(0, ERASER).map((hex, i) => (
              <button key={hex} type="button" className={`swatch${!erase && color === i ? " on" : ""}`}
                      style={{ background: hex }} aria-label={t("Colour {n}", { n: i + 1 })}
                      onClick={() => { setColor(i); setErase(false); }} />
            ))}
          </div>
          <div className="draw-sizes">
            {SIZES.map((_, i) => (
              <button key={i} type="button" className={`btn small${!erase && size === i ? " primary" : ""}`}
                      onClick={() => { setSize(i); setErase(false); }} aria-label={t("Pen size {n}", { n: i + 1 })}>
                <span className="dot" style={{ width: 4 + i * 5, height: 4 + i * 5 }} />
              </button>
            ))}
            <button type="button" className={`btn small${erase ? " primary" : ""}`} onClick={() => setErase((v) => !v)}>{t("Eraser")}</button>
            <button type="button" className="btn small ghost" onClick={clearAll}>{t("Clear")}</button>
          </div>
        </div>
      )}
    </div>
  );
}
