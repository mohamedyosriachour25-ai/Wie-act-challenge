import { useEffect, useRef } from "react";

// Decorative, interactive page background: soft violet orbs (one eases toward the pointer),
// a constellation of drifting nodes that link to each other and to the pointer, and a ripple on click.
// Purely visual: aria-hidden, pointer-events none (listens on window), paused when the tab is hidden,
// a single still frame when the user prefers reduced motion. Pixel ratio capped to keep it cheap.
const ORBS = [
  { x: 0.12, y: 0.18, r: 0.42, c: [128, 87, 255], a: 0.22, sx: 0.00011, sy: 0.00007 },
  { x: 0.88, y: 0.12, r: 0.36, c: [104, 57, 245], a: 0.16, sx: -0.00008, sy: 0.0001 },
  { x: 0.75, y: 0.85, r: 0.48, c: [183, 156, 255], a: 0.2, sx: 0.00006, sy: -0.00009 },
];
const LINK = 130; // px: max distance for a node-node link
const REACH = 190; // px: pointer influence radius

export default function Backdrop() {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!ctx) return undefined;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, dpr = 1, raf = 0, last = performance.now();
    let nodes = [];
    const ripples = [];
    const pointer = { x: -9999, y: -9999, sx: -9999, sy: -9999, active: false };

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.max(24, Math.min(70, Math.round((w * h) / 22000)));
      nodes = Array.from({ length: count }, (_, i) => nodes[i] || {
        x: Math.random() * w, y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
        r: 1 + Math.random() * 1.8,
      });
      for (const n of nodes) { n.x = Math.min(n.x, w); n.y = Math.min(n.y, h); }
    }

    function draw(t, dt) {
      ctx.clearRect(0, 0, w, h);
      // pointer position eases so the glow trails the cursor smoothly
      pointer.sx += (pointer.x - pointer.sx) * Math.min(1, dt * 0.004);
      pointer.sy += (pointer.y - pointer.sy) * Math.min(1, dt * 0.004);

      const big = Math.max(w, h);
      for (const o of ORBS) {
        const ox = (o.x + Math.sin(t * o.sx * 6) * 0.06) * w;
        const oy = (o.y + Math.cos(t * o.sy * 6) * 0.06) * h;
        const g = ctx.createRadialGradient(ox, oy, 0, ox, oy, o.r * big);
        g.addColorStop(0, `rgba(${o.c.join(",")},${o.a})`);
        g.addColorStop(1, `rgba(${o.c.join(",")},0)`);
        ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      }
      if (pointer.active) {
        const g = ctx.createRadialGradient(pointer.sx, pointer.sy, 0, pointer.sx, pointer.sy, 260);
        g.addColorStop(0, "rgba(128,87,255,0.16)");
        g.addColorStop(1, "rgba(128,87,255,0)");
        ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      }

      // nodes drift, wrap around the edges, and lean gently toward the pointer
      for (const n of nodes) {
        if (pointer.active) {
          const dx = pointer.sx - n.x, dy = pointer.sy - n.y, d = Math.hypot(dx, dy);
          if (d < REACH && d > 1) { n.vx += (dx / d) * 0.0025 * dt * 0.06; n.vy += (dy / d) * 0.0025 * dt * 0.06; }
        }
        n.vx *= 0.995; n.vy *= 0.995;
        const sp = Math.hypot(n.vx, n.vy);
        if (sp < 0.08) { n.vx += (Math.random() - 0.5) * 0.02; n.vy += (Math.random() - 0.5) * 0.02; }
        if (sp > 0.9) { n.vx *= 0.9 / sp; n.vy *= 0.9 / sp; }
        n.x += n.vx * dt * 0.06; n.y += n.vy * dt * 0.06;
        if (n.x < -10) n.x = w + 10; else if (n.x > w + 10) n.x = -10;
        if (n.y < -10) n.y = h + 10; else if (n.y > h + 10) n.y = -10;
      }
      ctx.lineWidth = 1;
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        for (let j = i + 1; j < nodes.length; j++) {
          const b = nodes[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          if (Math.abs(dx) > LINK || Math.abs(dy) > LINK) continue;
          const d = Math.hypot(dx, dy);
          if (d < LINK) {
            ctx.strokeStyle = `rgba(104,57,245,${0.14 * (1 - d / LINK)})`;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
        if (pointer.active) {
          const d = Math.hypot(a.x - pointer.sx, a.y - pointer.sy);
          if (d < REACH) {
            ctx.strokeStyle = `rgba(128,87,255,${0.35 * (1 - d / REACH)})`;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.sx, pointer.sy); ctx.stroke();
          }
        }
      }
      for (const n of nodes) {
        ctx.fillStyle = "rgba(82,35,224,0.42)";
        ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2); ctx.fill();
      }

      // click ripples
      for (let i = ripples.length - 1; i >= 0; i--) {
        const r = ripples[i];
        r.age += dt;
        const k = r.age / 900;
        if (k >= 1) { ripples.splice(i, 1); continue; }
        ctx.strokeStyle = `rgba(104,57,245,${0.4 * (1 - k)})`;
        ctx.lineWidth = 2 * (1 - k) + 0.5;
        ctx.beginPath(); ctx.arc(r.x, r.y, 12 + k * 140, 0, Math.PI * 2); ctx.stroke();
      }
    }

    function frame(now) {
      const dt = Math.min(50, now - last); last = now;
      draw(now, dt);
      raf = requestAnimationFrame(frame);
    }
    function start() { if (!raf && !still && !document.hidden) { last = performance.now(); raf = requestAnimationFrame(frame); } }
    function stop() { cancelAnimationFrame(raf); raf = 0; }

    const onMove = (e) => {
      pointer.x = e.clientX; pointer.y = e.clientY;
      if (!pointer.active) { pointer.sx = e.clientX; pointer.sy = e.clientY; pointer.active = true; }
    };
    const onLeave = () => { pointer.active = false; pointer.x = pointer.y = -9999; };
    const onDown = (e) => { if (ripples.length < 6) ripples.push({ x: e.clientX, y: e.clientY, age: 0 }); };
    const onUp = (e) => { if (e.pointerType === "touch") onLeave(); };
    const onVis = () => (document.hidden ? stop() : start());
    const onResize = () => { resize(); if (still) draw(0, 16); };

    resize();
    if (still) draw(0, 16);
    else {
      window.addEventListener("pointermove", onMove, { passive: true });
      window.addEventListener("pointerdown", onDown, { passive: true });
      window.addEventListener("pointerup", onUp, { passive: true });
      document.documentElement.addEventListener("pointerleave", onLeave);
      document.addEventListener("visibilitychange", onVis);
      start();
    }
    window.addEventListener("resize", onResize);
    return () => {
      stop();
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      document.documentElement.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return <canvas ref={ref} className="backdrop" aria-hidden="true" />;
}
