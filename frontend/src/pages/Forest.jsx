import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCycle, useFocusSignal, useRoomSocket } from "../lib/hooks.js";
import { useT } from "../lib/i18n.jsx";
import { AuthGate, TimerRing } from "../components/Common.jsx";
import QrCode from "../components/QrCode.jsx";
import Tree from "../components/Tree.jsx";

// These mirror the server rules in app/rooms.py (TREE_GRACE_S, TREE_MIN_S); the server is the judge.
const GRACE_S = 30;
const MIN_S = 120;
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

/** Progress to draw now: the server value, advanced locally while the member is focused (ticks arrive every few seconds). */
function shownProgress(member, at, nowS) {
  const tree = member.tree;
  if (!tree) return 0;
  if (tree.state !== "growing" || member.focused !== true) return tree.p;
  return Math.min(1, tree.p + Math.min(12, Math.max(0, nowS - at)) / tree.avail);
}

function ForestView({ code }) {
  const t = useT();
  const [room, setRoom] = useState(null);
  const [presence, setPresence] = useState(null);
  const [timer, setTimer] = useState(null);
  const [log, setLog] = useState([]);
  const [me, setMe] = useState("");
  const [copied, setCopied] = useState(false);

  const onEvent = useCallback((m) => {
    if (m.type === "snapshot") { setRoom(m.room); setPresence(m.presence); setTimer(m.timer); setMe(m.you); setLog(m.forest || []); }
    if (m.type === "presence") setPresence(m.presence);
    if (m.type === "tree") setLog((l) => [...l.slice(-199), m.entry]);
  }, []);
  const { conn, error, send, now } = useRoomSocket(code, onEvent);
  const cycle = useCycle(timer, now);
  const focused = useFocusSignal(true, null, { idleMs: Infinity }); // in the forest only the tab matters

  useEffect(() => { if (conn === "open") send({ type: "forest_join" }); }, [conn, send]);
  useEffect(() => { send({ type: "focus", focused }); }, [focused, conn]);
  useEffect(() => {
    const id = setInterval(() => send({ type: "focus", focused }), 20000);
    return () => clearInterval(id);
  }, [focused, send]);

  if (error) {
    return (
      <div className="narrow">
        <h1>{error.code === "room_full" ? t("This room is full") : t("Cannot join this room")}</h1>
        <p>{error.message}</p>
        <Link className="btn" to="/live">{t("Back to live rooms")}</Link>
      </div>
    );
  }
  if (!room || !cycle || !presence) return <p className="muted pad">{conn === "closed" ? t("The connection closed. Reload to rejoin.") : t("Joining the room…")}</p>;

  const nowS = now();
  const members = presence.members || [];
  const mine = members.find((m) => m.pseudo === me);
  const myTree = mine?.tree || null;
  const myP = mine && myTree ? shownProgress(mine, presence.at, nowS) : 0;
  const focusLen = room.focus_min * 60;
  const grace = Math.min(GRACE_S, Math.round(focusLen * 0.25));
  const canStillPlant = cycle.remaining > Math.min(MIN_S, focusLen / 2); // only meaningful during the focus phase

  let status;
  if (myTree?.state === "wilted") status = t("Your tree wilted because you were away for too long. A new tree starts with the next focus session.");
  else if (myTree?.state === "grown") status = t("Your tree is planted in the forest. Enjoy the break!");
  else if (myTree) status = focused === false
    ? t("Come back to this tab, your tree is waiting!")
    : myP >= 1
      ? t("Fully grown! Keep this tab open until the break and it is planted in the forest.")
      : t("Growing: {n}%. Keep this tab open and in front. Being away for more than {s} seconds in total makes it wilt.", { n: Math.round(myP * 100), s: grace });
  else if (cycle.phase === "break") status = t("Break time. A new tree starts with the next focus session.");
  else if (!canStillPlant) status = t("This focus session is almost over, so a new tree cannot start. The next one begins after the break.");
  else status = t("Planting your tree…");

  const growing = members.filter((m) => m.tree);
  const planted = log.filter((e) => e.state === "grown").length;
  const wilted = log.filter((e) => e.state === "wilted").length;
  const url = `${window.location.origin}/forest/${code}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable: the link is on screen anyway */ }
  };
  const label = (name, tree, p) => (tree.state === "wilted" ? t("{pseudo}: wilted", { pseudo: name }) : tree.state === "grown" ? t("{pseudo}: planted", { pseudo: name }) : t("{pseudo}: {n}% grown", { pseudo: name, n: Math.round(p * 100) }));

  return (
    <>
      <div className="page-head">
        <h1>{t("Study forest")}</h1>
        <p className="muted">{t("{focus} minutes of focus, then {brk} minutes of break, on repeat. Everyone sees the same clock.", { focus: room.focus_min, brk: room.break_min })}</p>
      </div>
      <div className="live forest">
        <section className="panel center-panel">
          <TimerRing cycle={cycle} label={cycle.phase === "focus" ? t("Focus. Work on your own thing.") : t("Break. Stretch, drink water.")} />
          <h3>{t("Your tree")}</h3>
          <Tree className="tree-big" p={myP} state={myTree?.state || "growing"} label={myTree ? label(me, myTree, myP) : t("Planting your tree…")} />
          <p className={`small tree-status ${myTree?.state === "wilted" ? "wilted" : ""}`} role="status" aria-live="polite" data-tree-state={myTree?.state || "none"}>{status}</p>
          <p className="muted small">{t("Only a yes/no focus signal from this tab is shared with the room. Nothing else leaves your device.")}</p>
        </section>

        <section className="panel">
          <h3>{t("The forest")}</h3>
          {growing.length === 0
            ? <p className="muted small">{t("No trees are growing right now.")}</p>
            : (
              <ul className="tree-grid">
                {growing.map((m) => {
                  const p = shownProgress(m, presence.at, nowS);
                  return (
                    <li key={m.pseudo} className={m.pseudo === me ? "me" : ""}>
                      <Tree p={p} state={m.tree.state} label={label(m.pseudo, m.tree, p)} />
                      <span className="who">{m.pseudo}{m.pseudo === me && ` ${t("(you)")}`}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          <p className="small forest-counts"><span className="tag">{t("Trees planted here: {n}", { n: planted })}</span><span className="muted">{t("Trees that wilted: {n}", { n: wilted })}</span></p>
          {log.length > 0 && (
            <div className="forest-strip" aria-hidden="true">
              {log.slice(-30).map((e, i) => <span key={`${e.cycle}-${e.pseudo}-${i}`} title={e.pseudo}><Tree p={e.state === "grown" ? 1 : 0.7} state={e.state} /></span>)}
            </div>
          )}
        </section>

        <section className="panel invite-qr">
          <h3>{t("Invite friends")}</h3>
          <p className="muted small">{t("Scan with a phone camera to join this forest.")}</p>
          <QrCode value={url} label={t("QR code to join this forest")} />
          <p className="small join-url"><code>{url}</code></p>
          <button className="btn small" onClick={copy}>{copied ? t("Copied") : t("Copy link")}</button>
          {LOCAL_HOSTS.includes(window.location.hostname) && (
            <p className="muted small">{t("This address only works on this computer. Open EdNova through your network address or its public address so phones can join.")}</p>
          )}
          <p className="small"><Link to={`/live/${code}`}>{t("Open as a plain focus room")}</Link></p>
        </section>
      </div>
    </>
  );
}

export function ForestRoom() {
  const { code } = useParams();
  const t = useT();
  return (
    <AuthGate note={t("Other people in a room see your pseudo only.")}>
      <ForestView key={code} code={code} />
    </AuthGate>
  );
}
