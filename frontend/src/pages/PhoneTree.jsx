import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { phoneTreeWsUrl } from "../lib/api.js";
import { tx, useT } from "../lib/i18n.jsx";
import Tree from "../components/Tree.jsx";
import { LogoMark } from "../components/Logo.jsx";

/** Best-effort: speak a short reminder right as the tab leaves the foreground. Phones suspend background
 * tabs within seconds, so this can only ever fire at the instant of leaving, never while away — the
 * reliable "come back" signal is the private alert sent to the owner's desktop session (see rooms.py /
 * phonetree.py), not this. Silently does nothing where speech synthesis is unavailable or blocked. */
function speak(text, lang) {
  try {
    if (!window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(text);
    if (lang) u.lang = lang;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  } catch { /* unsupported or blocked: the visual countdown still shows once the person returns */ }
}

// Said (and shown in the tab title) the moment the phone tab is left during a focus phase; one is picked at random.
const FOCUS_NUDGES = [
  tx("Don't let your focus wither like a tree — the break is in {time} in {room}."),
  tx("Your tree is still growing. Only {time} until the break in {room}."),
  tx("Stay with it! {time} of focus left in {room}, then you can rest."),
];

/** "12 min" / "45 s": the time left in the room's current phase, from the server's count at `at`. */
function useCountdown(view) {
  const [, tick] = useState(0);
  useEffect(() => { const id = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(id); }, []);
  if (!view || typeof view.remaining !== "number") return null;
  return Math.max(0, Math.round(view.remaining - (Date.now() - view.at) / 1000));
}

/** Standalone pocket-tree page, reached only by its QR code or link (the QR code in components/RoomForest.jsx).
 * No login, no app header/nav — see App.jsx, which renders this on its own for any /tree/:token path.
 * Keep this tab open and in front; closing or switching away withers the tree after a short grace. */
export function PhoneTreePage() {
  const { token } = useParams();
  const t = useT();
  const [view, setView] = useState(null);
  const [status, setStatus] = useState("connecting"); // connecting | open | closed | error
  const [errorMsg, setErrorMsg] = useState("");
  const wsRef = useRef(null);

  const connect = useCallback(() => {
    setStatus("connecting");
    setView(null);
    const ws = new WebSocket(phoneTreeWsUrl(token));
    wsRef.current = ws;
    ws.onopen = () => setStatus("open");
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type === "error") { setStatus("error"); setErrorMsg(msg.message || ""); return; }
      if (msg.type === "phone_tree") setView({ ...msg, at: Date.now() });
    };
    ws.onclose = () => setStatus((s) => (s === "error" ? s : "closed"));
  }, [token]);

  useEffect(() => {
    connect();
    return () => wsRef.current?.close();
  }, [connect]);

  const sendVisible = useCallback((visible) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify({ type: "visible", visible }));
  }, []);

  const left = useCountdown(view);
  const fmt = (s) => (s >= 60 ? t("{n} min", { n: Math.ceil(s / 60) }) : t("{n} s", { n: s }));
  const nudgeRef = useRef(() => t("Come back to your tree!"));
  nudgeRef.current = () => {
    if (!view?.room || left == null || view.phase !== "focus") return view?.phase === "break" ? null : t("Come back to your tree!");
    const pick = FOCUS_NUDGES[Math.floor(Math.random() * FOCUS_NUDGES.length)];
    return t(pick, { time: fmt(left), room: t(view.room) });
  };

  useEffect(() => {
    const title = document.title;
    const onVisibility = () => {
      const visible = document.visibilityState === "visible";
      sendVisible(visible);
      if (visible) { document.title = title; return; }
      const nudge = nudgeRef.current();          // nothing to say during a break: the phone is free then
      if (!nudge) return;
      document.title = nudge;                     // what the tab switcher shows while the person is away
      try { navigator.vibrate?.([120, 80, 120]); } catch { /* not supported */ }
      speak(nudge, document.documentElement.lang);
    };
    const onHide = () => sendVisible(false);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.title = title;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onHide);
    };
  }, [sendVisible, t]);

  if (status === "error") {
    return (
      <div className="narrow pad">
        <h1>{t("This pocket-tree link does not work")}</h1>
        <p className="muted">{errorMsg}</p>
      </div>
    );
  }
  if (!view) {
    return <p className="muted pad">{t("Planting your pocket tree…")}</p>;
  }

  const awayLeft = Math.max(0, Math.ceil(view.grace_s - view.away_for));
  const onBreak = view.phase === "break";
  let status_line;
  if (view.state === "wilted") status_line = t("Your pocket tree withered. You left it for too long.");
  else if (onBreak) status_line = t("Break time in {room}: {time} left. Your tree is resting, so your phone is free until focus starts again.", { room: t(view.room), time: fmt(left ?? 0) });
  else if (!view.visible) status_line = t("Come back! Your tree withers in {n}s.", { n: awayLeft });
  else if (view.state === "grown") status_line = t("Fully grown! Keep this tab open and off other apps to keep it that way.");
  else status_line = t("Growing: {n}%. Keep this tab open and off other apps.", { n: Math.round(view.p * 100) });

  return (
    <div className="narrow pad pocket-tree-page">
      <p className="phone-brand"><LogoMark size={30} /><span>Herwie<span className="brand-go">GO</span></span></p>
      <h1>{t("Your pocket tree")}</h1>
      {view.room && left != null && (
        <p className={`phone-clock ${onBreak ? "break" : ""}`}>
          <strong>{t(view.room)}</strong>
          <span>{onBreak ? t("Break · focus in {time}", { time: fmt(left) }) : t("Focus · break in {time}", { time: fmt(left) })}</span>
        </p>
      )}
      <Tree className="tree-big" p={view.p} state={view.state} label={t("Pocket tree")} />
      <p className={`small tree-status ${view.state === "wilted" || (!view.visible && !onBreak) ? "wilted" : ""}`} role="status" aria-live="polite">{status_line}</p>
      <p className="muted small">{t("Keep this tab open and in front while you study. Leaving it for more than {n} seconds withers the tree.", { n: view.grace_s })}</p>
      {view.state === "wilted" && <button className="btn" onClick={connect}>{t("Plant a new tree")}</button>}
      {status === "closed" && view.state !== "wilted" && (
        <p className="muted small">{t("The connection closed.")} <button className="link" onClick={connect}>{t("Reconnect")}</button></p>
      )}
    </div>
  );
}

export default PhoneTreePage;
