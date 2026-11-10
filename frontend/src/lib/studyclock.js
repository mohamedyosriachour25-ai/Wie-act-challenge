import { useEffect, useRef } from "react";
import { api, getToken } from "./api.js";

const ACTIVITY = ["pointerdown", "keydown", "scroll", "touchstart", "mousemove"];

/**
 * Study-time heartbeat (scoring v2): once a minute, while this page is visible AND the learner has used it
 * recently, tell the server one minute was spent in `context` ("solo", "live" = a live room's focus phase,
 * "room" = a study room with friends). The server credits at most one minute per 55 s, so several open tabs
 * never count twice. `context` null = not counting (e.g. a live room's break).
 */
export function useStudyClock(context, { idleMs = 3 * 60 * 1000 } = {}) {
  const lastUse = useRef(Date.now());
  useEffect(() => {
    const touch = () => { lastUse.current = Date.now(); };
    ACTIVITY.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    return () => ACTIVITY.forEach((e) => window.removeEventListener(e, touch));
  }, []);
  useEffect(() => {
    if (!context || !getToken()) return undefined;
    const id = setInterval(() => {
      if (document.visibilityState !== "visible" || Date.now() - lastUse.current > idleMs) return;
      api("/api/score/heartbeat", { method: "POST", body: { context } }).catch(() => {});
    }, 60_000);
    return () => clearInterval(id);
  }, [context, idleMs]);
}
