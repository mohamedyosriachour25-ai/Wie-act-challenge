import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { useCycle, useFocusSignal, useRoomSocket, useVideoMesh } from "../lib/hooks.js";
import { tx, useT } from "../lib/i18n.jsx";
import { AuthGate, Dots, TimerRing } from "../components/Common.jsx";

const TRIVIA = [
  { q: tx("What is the powerhouse of the cell?"), o: [tx("Nucleus"), tx("Mitochondria"), tx("Ribosome"), tx("Membrane")], a: 1 },
  { q: tx("How many degrees do the angles of a triangle add up to?"), o: ["90", "180", "270", "360"], a: 1 },
  { q: tx("Which planet is closest to the Sun?"), o: [tx("Venus"), tx("Earth"), tx("Mercury"), tx("Mars")], a: 2 },
  { q: tx("What is 7 × 8?"), o: ["54", "56", "64", "48"], a: 1 },
  { q: tx("Which gas do plants take in for photosynthesis?"), o: [tx("Oxygen"), tx("Nitrogen"), tx("Carbon dioxide"), tx("Helium")], a: 2 },
  { q: tx("What is the square root of 144?"), o: ["10", "11", "12", "14"], a: 2 },
  { q: tx("In computing, what does CPU stand for?"), o: [tx("Central Processing Unit"), tx("Core Power Unit"), tx("Computer Primary Unit"), tx("Control Program Unit")], a: 0 },
  { q: tx("Which part of speech describes a noun?"), o: [tx("Verb"), tx("Adjective"), tx("Adverb"), tx("Preposition")], a: 1 },
];
// Names and blurbs of the seeded rooms come from the server in English; they are shown translated.
const LIVE_TEXT = [
  tx("Deep work"), tx("Quiet focus, no chat. Reactions only."),
  tx("Long sessions"), tx("Longer blocks for heavy reading and problem sets."),
  tx("Sprints"), tx("Short bursts when motivation is low."),
];
const SLOT = 25; // seconds per trivia question

function BreakGame({ cycle }) {
  const t = useT();
  const [answers, setAnswers] = useState({});
  const slot = Math.floor(cycle.into / SLOT);
  const key = `${cycle.cycles}-${slot}`;
  const item = TRIVIA[(cycle.cycles * 3 + slot) % TRIVIA.length];
  const reveal = cycle.into - slot * SLOT >= SLOT - 6;
  const score = Object.entries(answers).filter(([k, v]) => v.right).length;
  const mine = answers[key];
  return (
    <section className="panel game">
      <h3>{t("Break trivia")}</h3>
      <p className="muted small">{t("Same question for everyone in the room. Your score stays on your device.")}</p>
      <p className="question">{t(item.q)}</p>
      <ul className="options">
        {item.o.map((o, n) => {
          const cls = reveal ? (n === item.a ? "right" : mine?.pick === n ? "wrong" : "") : mine?.pick === n ? "pending" : "";
          return <li key={n}><button className={`option ${cls}`} disabled={!!mine || reveal} onClick={() => setAnswers((a) => ({ ...a, [key]: { pick: n, right: n === item.a } }))}>{t(o)}</button></li>;
        })}
      </ul>
      <p className="small muted" aria-live="polite">{reveal ? (mine ? (mine.right ? t("Correct.") : t("Not this time.")) : t("Time is up for this one.")) : t("Next question in a few seconds.")} {t("Score: {n}", { n: score })}</p>
    </section>
  );
}

function VideoTile({ stream, label, mirror = false }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.srcObject = stream || null;
    if (stream) el.play?.().catch(() => {}); // some browsers need an explicit play() for a fresh srcObject
  }, [stream]);
  // Muted because the stream carries no audio yet; a future audio track would unmute the remote tiles.
  return (
    <figure className="vtile">
      <video ref={ref} autoPlay playsInline muted className={mirror ? "mirror" : ""} />
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function CameraWall({ mesh }) {
  const t = useT();
  const { cameraOn, starting, error, tiles, localStream, toggle } = mesh;
  return (
    <section className="panel camera">
      <div className="row between">
        <h3>{t("Camera")}</h3>
        <button className={`btn small ${cameraOn ? "ghost" : "primary"}`} disabled={starting} onClick={toggle}>
          {starting ? t("Starting…") : cameraOn ? t("Turn camera off") : t("Turn camera on")}
        </button>
      </div>
      <p className="muted small">{t("Your video goes straight to the other cameras here, peer to peer. It never reaches our servers and is not recorded.")}</p>
      {error?.message === "denied" && <p className="error-box small">{t("EdNova could not use your camera. Check the camera permission in your browser.")}</p>}
      {error?.message === "full" && <p className="error-box small">{t("The camera wall is full ({n} cameras). Try again when someone turns theirs off.", { n: error.retryAfter })}</p>}
      {(cameraOn || tiles.length > 0) ? (
        <div className={`vgrid n${Math.min(1 + tiles.length, 6)}`}>
          {localStream && <VideoTile stream={localStream} label={t("You")} mirror />}
          {tiles.map((tl) => <VideoTile key={tl.id} stream={tl.stream} label={tl.pseudo} />)}
        </div>
      ) : (
        <p className="muted small">{t("Turn your camera on to share the wall with others studying here. You will see only the people who also share.")}</p>
      )}
    </section>
  );
}

function LiveRoomView({ code }) {
  const t = useT();
  const [room, setRoom] = useState(null);
  const [presence, setPresence] = useState({ online: 0, focused: 0, tracking: 0, members: [] });
  const [timer, setTimer] = useState(null);
  const [share, setShare] = useState(true);
  const [floats, setFloats] = useState([]);
  const [myId, setMyId] = useState(null);
  const nextId = useRef(0);
  const signalRef = useRef(null);

  const onEvent = useCallback((m) => {
    if (m.type === "snapshot") { setRoom(m.room); setPresence(m.presence); setTimer(m.timer); setMyId(m.you_id); }
    if (m.type === "presence") setPresence(m.presence);
    if (typeof m.type === "string" && m.type.startsWith("rtc")) signalRef.current?.(m);
    if (m.type === "reaction") {
      const id = nextId.current++;
      setFloats((f) => [...f.slice(-12), { id, emoji: m.emoji, x: 10 + Math.random() * 80 }]);
      setTimeout(() => setFloats((f) => f.filter((x) => x.id !== id)), 2600);
    }
  }, []);
  const { conn, error, send, now } = useRoomSocket(code, onEvent);
  const mesh = useVideoMesh(send, myId);
  signalRef.current = mesh.signal;
  const cycle = useCycle(timer, now);
  const focused = useFocusSignal(share);

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
  if (!room || !cycle) return <p className="muted pad">{conn === "closed" ? t("The connection closed. Reload to rejoin.") : t("Joining the room…")}</p>;

  const pct = presence.tracking ? Math.round((presence.focused / presence.tracking) * 100) : null;
  return (
    <>
      <div className="page-head">
        <h1>{t(room.name)}</h1>
        <p className="muted">{t("{focus} minutes of focus, then {brk} minutes of break, on repeat. Everyone sees the same clock.", { focus: room.focus_min, brk: room.break_min })}</p>
      </div>
      <div className="live">
        <section className="panel center-panel">
          <TimerRing cycle={cycle} label={cycle.phase === "focus" ? t("Focus. Work on your own thing.") : t("Break. Stretch, drink water.")} />
          <div className="react-bar">
            <div className="floats" aria-hidden="true">{floats.map((f) => <span key={f.id} style={{ left: `${f.x}%` }}>{f.emoji}</span>)}</div>
            <div className="row">
              {["👏", "🔥", "💡", "☕"].map((e) => <button key={e} className="chip emoji" aria-label={t("Send {emoji}", { emoji: e })} onClick={() => send({ type: "reaction", emoji: e })}>{e}</button>)}
            </div>
          </div>
          <label className="check"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> {t("Show whether I am focused")}</label>
          <p className="muted small">{t("The focus signal is computed on this device from this tab and your activity. It uses no camera or microphone.")}</p>
        </section>

        <CameraWall mesh={mesh} />

        <section className="panel">
          <h3>{t("In the room")}</h3>
          <p className="big-num">{presence.online}</p>
          <p className="muted">{presence.online === 1 ? t("person here") : t("people here")}{pct != null && <>{t(", {n}% focused right now", { n: pct })}</>}</p>
          {pct != null && <div className="meter wide" role="img" aria-label={t("{n} percent focused", { n: pct })}><i style={{ width: `${pct}%` }} /></div>}
          <ul className="chips-people">
            {presence.members.map((m) => (
              <li key={m.pseudo}><span className={`pip ${m.focused === true ? "on" : m.focused === false ? "off" : ""}`} />{m.pseudo}</li>
            ))}
          </ul>
          {presence.online > presence.members.length && <p className="muted small">{t("And {n} more.", { n: presence.online - presence.members.length })}</p>}
        </section>

        {cycle.phase === "break" && <BreakGame cycle={cycle} />}
      </div>
    </>
  );
}

function LiveList() {
  const t = useT();
  const nav = useNavigate();
  const [rooms, setRooms] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => api("/api/rooms?kind=live").then((d) => alive && setRooms(d.rooms)).catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return (
    <>
      <div className="page-head">
        <h1>{t("Live focus rooms")}</h1>
        <p className="muted">{t("Public rooms on a shared timer. Pick a rhythm and work alongside others. There is no chat, only quiet company.")}</p>
      </div>
      {rooms === null && <p className="muted">{t("Loading rooms…")}</p>}
      <ul className="room-list live-list">
        {rooms?.map((r) => (
          <li key={r.code}>
            <div>
              <h3>{t(r.name)}</h3>
              <p className="muted small">{t(r.topic)}</p>
              <p className="small">{t("{focus} min focus, {brk} min break", { focus: r.focus_min, brk: r.break_min })}</p>
            </div>
            <div className="where">
              <Dots n={Math.max(r.online, 3)} filled={r.online} max={10} label={t("{n} people here", { n: r.online })} />
              <span className="small muted">{t("{n} here", { n: r.online })}</span>
            </div>
            <button className="btn primary small" onClick={() => nav(`/live/${r.code}`)}>{t("Enter room")}</button>
          </li>
        ))}
      </ul>
    </>
  );
}

export function Live() {
  const t = useT();
  return (
    <AuthGate note={t("Other people in a room see your pseudo only.")}>
      <LiveList />
    </AuthGate>
  );
}

export function LiveRoom() {
  const { code } = useParams();
  const t = useT();
  return (
    <AuthGate note={t("Other people in a room see your pseudo only.")}>
      <LiveRoomView key={code} code={code} />
    </AuthGate>
  );
}
