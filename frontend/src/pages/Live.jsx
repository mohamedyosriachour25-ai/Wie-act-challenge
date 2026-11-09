import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { useAttention, useCycle, useFocusSignal, useRoomSocket, useVideoMesh } from "../lib/hooks.js";
import { tx, useT } from "../lib/i18n.jsx";
import { AuthGate, Dots, TimerRing } from "../components/Common.jsx";
import DrawBoard from "../components/DrawBoard.jsx";

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
  tx("Quick demo"), tx("A fast one-minute cycle to try focus, breaks and group games."),
];
const SLOT = 25; // seconds per trivia question
const FOREST_ROOM = "LIVE-FOREST"; // the Live room that has its own "study forest" entry (/forest)
const GAMES = ["trivia_race", "mime", "pictionary"];
const GAME_LABEL = { trivia_race: tx("Quiz"), mime: tx("Mime"), pictionary: tx("Pictionary") };
const MIME = [
  tx("a cat"), tx("playing football"), tx("a teacher writing on the board"), tx("swimming"),
  tx("a chef cooking"), tx("driving a car"), tx("an aeroplane taking off"), tx("brushing your teeth"),
];
const PICTIONARY = [
  tx("a house"), tx("a tree"), tx("the sun"), tx("a car"),
  tx("a boat"), tx("a fish"), tx("a flower"), tx("a star"),
];
const GUESS_BANK = { mime: MIME, pictionary: PICTIONARY };   // the word the performer keeps secret

// Loose match so a mime guess counts even with articles or extra words (the performer's own client judges).
const norm = (s) => (s || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9\s]/g, "")
  .replace(/\b(a|an|the|le|la|les|un|une|des|el)\b/g, "").replace(/\s+/g, " ").trim();
const matchGuess = (word, guess) => {
  const w = norm(word), g = norm(guess);
  return g.length >= 3 && (w === g || w.includes(g) || g.includes(w));
};

function RoomChat({ messages, members, me, send }) {
  const t = useT();
  const [text, setText] = useState("");
  const [to, setTo] = useState("");       // "" = everyone, else a pseudo (private message)
  const log = useRef(null);
  const peers = members.map((m) => m.pseudo).filter((p) => p && p !== me);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [messages.length]);
  const submit = (e) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    if (to && peers.includes(to)) send({ type: "dm", to, text: body });
    else send({ type: "chat", text: body });
    setText("");
  };
  return (
    <section className="panel chat-panel">
      <h3>{t("Chat")}</h3>
      <div className="chat-log" ref={log} aria-live="polite">
        {messages.length === 0 && <p className="muted small">{t("Say hello to the room.")}</p>}
        {messages.map((m) => m.kind === "sys" ? (
          <p key={m.id} className="chat-sys">{t("{pseudo} guessed it! 🎉", { pseudo: m.pseudo })}</p>
        ) : m.kind === "dm" ? (
          <p key={m.id} className="chat-line dm">
            <span className="lock" aria-hidden="true">🔒</span>
            <strong>{m.author === me ? t("You") : m.author}</strong>
            <span className="to">{t("to {pseudo}", { pseudo: m.to === me ? t("you") : m.to })}</span> {m.text}
          </p>
        ) : (
          <p key={m.id} className="chat-line"><strong>{m.author === me ? t("You") : m.author}</strong> {m.text}</p>
        ))}
      </div>
      <form className="chat-compose" onSubmit={submit}>
        {peers.length > 0 && (
          <select value={to} onChange={(e) => setTo(e.target.value)} aria-label={t("Send to")}>
            <option value="">{t("Everyone")}</option>
            {peers.map((p) => <option key={p} value={p}>{"🔒 " + p}</option>)}
          </select>
        )}
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={300}
               placeholder={to ? t("Private message to {pseudo}", { pseudo: to }) : t("Message the room")}
               aria-label={t("Chat message")} />
        <button className="btn small" type="submit">{t("Send")}</button>
      </form>
    </section>
  );
}

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

function GroupGame({ cycle, send, leaderboard, you }) {
  const t = useT();
  const [answered, setAnswered] = useState({});
  const slot = Math.floor(cycle.into / SLOT);
  const key = `${cycle.cycles}-${slot}`;
  const item = TRIVIA[(cycle.cycles * 3 + slot) % TRIVIA.length];
  const reveal = cycle.into - slot * SLOT >= SLOT - 6;
  const mine = answered[key];
  const pick = (n) => {
    if (mine || reveal) return;
    const ms = Math.max(0, (cycle.into - slot * SLOT) * 1000);
    setAnswered((a) => ({ ...a, [key]: { pick: n } }));
    send({ type: "game_result", cycle: cycle.cycles, game: "trivia_race", round: slot, correct: n === item.a, ms });
  };
  return (
    <section className="panel game">
      <h3>{t("Group trivia race")}</h3>
      <p className="question">{t(item.q)}</p>
      <ul className="options">
        {item.o.map((o, n) => {
          const cls = reveal ? (n === item.a ? "right" : mine?.pick === n ? "wrong" : "") : mine?.pick === n ? "pending" : "";
          return <li key={n}><button className={`option ${cls}`} disabled={!!mine || reveal} onClick={() => pick(n)}>{t(o)}</button></li>;
        })}
      </ul>
      <strong className="small">{t("Leaderboard")}</strong>
      <ol className="leaderboard">
        {leaderboard.length === 0 && <li className="muted small">{t("Waiting for players…")}</li>}
        {leaderboard.map((r, i) => (
          <li key={r.pseudo} className={r.pseudo === you ? "me" : ""}>
            <span className="rank">{i + 1}</span>
            <span className="who">{r.pseudo}{r.pseudo === you && ` ${t("(you)")}`}</span>
            <span className="pts">{r.score}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TurnGame({ game, cur, next, total, mine, mesh, now, send, drawRef }) {
  const t = useT();
  const bank = GUESS_BANK[game] || MIME;
  const left = mine ? Math.max(0, Math.ceil(mine.seconds - (now() - mine.at))) : 0;
  const isPic = game === "pictionary";
  return (
    <section className="panel game turn-game">
      <h3>{t(GAME_LABEL[game] || game)}</h3>
      {mine ? (
        <div className="your-turn">
          <strong>{t("Your turn!")} {left}s</strong>
          <p className="prompt-big">{isPic ? t("Draw: {x}", { x: t(bank[mine.prompt_index % bank.length]) }) : t("Act out: {x}", { x: t(bank[mine.prompt_index % bank.length]) })}</p>
          {isPic ? (
            <>
              <DrawBoard editable send={send} drawRef={drawRef} />
              <p className="muted small">{t("Draw it with the tools below — don't write the word!")}</p>
            </>
          ) : (
            <>
              {!mesh.cameraOn
                ? <button className="btn primary" onClick={mesh.perform}>{t("Go on stage (camera + mic)")}</button>
                : <button className="btn ghost small" onClick={mesh.leaveStage}>{t("Leave the stage")}</button>}
              <p className="muted small">{t("Act it out — don't say the word!")}</p>
            </>
          )}
        </div>
      ) : (
        <div className="watching">
          {cur ? <p><span className="tag">{t("On stage")}</span> <strong>{cur.name}</strong></p>
               : <p className="muted">{t("Get ready…")}</p>}
          {next && <p className="muted small">{t("Next: {pseudo}", { pseudo: next })}</p>}
          {isPic && <DrawBoard editable={false} send={send} drawRef={drawRef} />}
          <p className="muted small">{t("Type your guess in the chat!")}</p>
          {!isPic && <p className="muted small">{t("Open the camera wall to watch and hear the players.")}</p>}
        </div>
      )}
    </section>
  );
}

function VideoTile({ stream, label, mirror = false, muted = true }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.srcObject = stream || null;
    if (stream) el.play?.().catch(() => {}); // some browsers need an explicit play() for a fresh srcObject
  }, [stream]);
  return (
    <figure className="vtile">
      <video ref={ref} autoPlay playsInline muted={muted} className={mirror ? "mirror" : ""} />
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
        <div className={`vgrid n${Math.min(1 + tiles.length, 9)}`}>
          {localStream && <VideoTile stream={localStream} label={t("You")} mirror muted />}
          {tiles.map((tl) => <VideoTile key={tl.id} stream={tl.stream} label={tl.pseudo} muted={false} />)}
        </div>
      ) : (
        <p className="muted small">{t("Turn your camera on to share the wall with others studying here. You will see only the people who also share.")}</p>
      )}
    </section>
  );
}

/** Opt-in camera attention check. Off by default; the status line tells the learner exactly what is happening. */
function AttentionCheck({ watch, setWatch, share, attention }) {
  const t = useT();
  const { status, attentive, reason } = attention;
  let line = null;
  if (watch && share) {
    if (status === "loading") line = t("Loading the on-device model…");
    else if (status === "paused") line = t("Paused during the break. Your camera is off.");
    else if (status === "denied") line = t("EdNova could not use your camera. Check the camera permission in your browser.");
    else if (status === "unsupported") line = t("This browser cannot use the camera on this page. The camera needs a secure (HTTPS) page.");
    else if (status === "error") line = t("The on-device model could not be loaded. The usual focus signal is used instead.");
    else if (status === "running") {
      if (attentive === null) line = t("Getting ready…");
      else if (attentive) line = t("You are facing the screen.");
      else if (reason === "no_face") line = t("No face in view. You show as away until you are back.");
      else line = t("You are looking away. You show as away until you are back.");
    }
  }
  return (
    <div className="attention">
      <label className="check">
        <input type="checkbox" checked={watch && share} disabled={!share} onChange={(e) => setWatch(e.target.checked)} /> {t("Also use my camera to check that I am facing the screen")}
      </label>
      {watch && share
        ? <p className="muted small">{t("Your camera is analysed on this device only. No image is saved or sent; only the same yes/no focus signal as above is shared. Looking down at your notes is fine.")}</p>
        : <p className="muted small">{t("The focus signal is computed on this device from this tab and your activity. It uses no camera or microphone.")}</p>}
      {line && <p className={`small attention-status ${status === "running" && attentive === false ? "away" : ""}`} role="status" aria-live="polite">{line}</p>}
    </div>
  );
}

function LiveRoomView({ code }) {
  const t = useT();
  const [room, setRoom] = useState(null);
  const [presence, setPresence] = useState({ online: 0, focused: 0, tracking: 0, members: [] });
  const [timer, setTimer] = useState(null);
  const [share, setShare] = useState(true);
  const [watch, setWatch] = useState(false);            // opt-in camera attention check, off by default
  const [floats, setFloats] = useState([]);
  const [myId, setMyId] = useState(null);
  const [me, setMe] = useState("");
  const [event, setEvent] = useState(null);
  const [joinedCycle, setJoinedCycle] = useState(-1);
  const [leaderboard, setLeaderboard] = useState([]);
  const [chosenGame, setChosenGame] = useState(null);   // the game THIS player picked for the break
  const [counts, setCounts] = useState({});             // how many players picked each game
  const [cur, setCur] = useState(null);       // the current performer (turn games)
  const [next, setNext] = useState(null);     // only the next player's name is ever revealed
  const [myTurn, setMyTurn] = useState(null);
  const [gameOver, setGameOver] = useState(false);
  const [messages, setMessages] = useState([]);
  const nextId = useRef(0);
  const prevNext = useRef(null);
  const startSent = useRef(-1);
  const signalRef = useRef(null);
  const drawRef = useRef(null);     // the pictionary board registers here to receive strokes
  const solvedRef = useRef(-1);
  const chosenRef = useRef(null);
  useEffect(() => { chosenRef.current = chosenGame; }, [chosenGame]);

  const onEvent = useCallback((m) => {
    if (m.type === "snapshot") {
      setRoom(m.room); setPresence(m.presence); setTimer(m.timer); setMyId(m.you_id); setMe(m.you);
      if (m.event) { setEvent(m.event); if (m.event.counts) setCounts(m.event.counts); }
    }
    if (m.type === "presence") setPresence(m.presence);
    if (m.type === "event_participants") { if (m.counts) setCounts(m.counts); }
    if (m.type === "event_scores") { if (!m.game || m.game === chosenRef.current) setLeaderboard(m.leaderboard); }
    if (m.type === "game_start") { setGameOver(false); setMyTurn(null); setCur(null); setNext(null); prevNext.current = null; }
    if (m.type === "game_next") {
      const prev = prevNext.current;
      setCur(prev ? { name: prev } : null);
      setNext(m.pseudo || null);
      prevNext.current = m.pseudo || null;
      if (typeof m.round === "number") setMyTurn((mt) => (mt && m.round > mt.round ? null : mt));
    }
    if (m.type === "game_your_turn") setMyTurn({ game: m.game, prompt_index: m.prompt_index, round: m.round, total: m.total, seconds: m.seconds, at: performance.now() / 1000 });
    if (m.type === "game_over") { setGameOver(true); setMyTurn(null); setCur(null); setNext(null); prevNext.current = null; }
    if (m.type === "chat") setMessages((x) => [...x.slice(-80), { ...m.msg, kind: "chat" }]);
    if (m.type === "dm") setMessages((x) => [...x.slice(-80), { id: `d${m.ts}-${m.from}`, author: m.from, to: m.to, text: m.text, ts: m.ts, kind: "dm" }]);
    if (m.type === "game_guessed") {
      setMessages((x) => [...x.slice(-80), { id: `g${m.round}-${m.pseudo}`, kind: "sys", pseudo: m.pseudo }]);
      if (m.leaderboard) setLeaderboard(m.leaderboard);
    }
    if (m.type === "draw") drawRef.current?.(m);
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
  const attention = useAttention(watch && share, cycle?.phase === "focus"); // camera only runs during focus
  const focused = useFocusSignal(share, attention.status === "running" ? attention.attentive : null);

  // In a guess game (mime / pictionary) the performer's own client knows the word, so it judges chat guesses.
  useEffect(() => {
    const bank = myTurn && GUESS_BANK[myTurn.game];
    if (!bank || solvedRef.current === myTurn.round) return;
    const last = messages[messages.length - 1];
    if (!last || last.kind !== "chat" || last.author === me) return;
    const key = bank[myTurn.prompt_index % bank.length];
    if (matchGuess(key, last.text) || matchGuess(t(key), last.text)) {
      solvedRef.current = myTurn.round;
      send({ type: "turn_guess", pseudo: last.author });
    }
  }, [messages, myTurn, me, send, t]);

  useEffect(() => { setLeaderboard([]); setChosenGame(null); setCounts({}); setCur(null); setNext(null); setMyTurn(null); setGameOver(false); prevNext.current = null; }, [cycle?.cycles]);

  // Each chosen turn game is driven by the server; a participant reaching the break tells it to start.
  useEffect(() => {
    if (cycle?.phase === "break" && joinedCycle === cycle.cycles && (chosenGame === "mime" || chosenGame === "pictionary") && startSent.current !== cycle.cycles) {
      startSent.current = cycle.cycles;
      send({ type: "event_start", cycle: cycle.cycles });
    }
  }, [cycle?.phase, cycle?.cycles, joinedCycle, chosenGame, send]);

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
          <AttentionCheck watch={watch} setWatch={setWatch} share={share} attention={attention} />
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

        {event && cycle.phase === "focus" && cycle.remaining <= event.lead_s && (
          <section className="panel invite">
            <h3>{t("Group game")}</h3>
            <p className="muted small">{chosenGame
              ? t("You picked {game}. Everyone who picks the same game plays together — change it any time before the break.", { game: t(GAME_LABEL[chosenGame]) })
              : t("A group game starts in {n}s. Pick the one you want — everyone who picks the same game plays together:", { n: cycle.remaining })}</p>
            <div className="game-picks">
              {GAMES.map((g) => (
                <button key={g} className={`btn small${chosenGame === g ? " primary" : ""}`}
                        onClick={() => { send({ type: "event_optin", cycle: cycle.cycles, game: g }); setJoinedCycle(cycle.cycles); setChosenGame(g); }}>
                  {t(GAME_LABEL[g])}{counts[g] ? ` · ${counts[g]}` : ""}
                </button>
              ))}
              <button className={`btn small ghost${joinedCycle === cycle.cycles && !chosenGame ? " primary" : ""}`}
                      onClick={() => { send({ type: "event_optout", cycle: cycle.cycles }); setJoinedCycle(cycle.cycles); setChosenGame(null); }}>
                {t("Skip — just rest")}
              </button>
            </div>
            {chosenGame
              ? <p className="muted small">{t("Starts in {n}s.", { n: cycle.remaining })}</p>
              : joinedCycle === cycle.cycles && <p className="muted small">{t("You are sitting this one out. Pick a game above any time before the break to join in.")}</p>}
          </section>
        )}
        {cycle.phase === "break" && (joinedCycle !== cycle.cycles || !chosenGame
          ? <BreakGame cycle={cycle} />
          : gameOver
            ? <section className="panel game"><h3>{t("Game over")}</h3><p className="muted small">{t("Nice! Back to focus soon.")}</p></section>
            : chosenGame === "mime" || chosenGame === "pictionary"
              ? <TurnGame game={chosenGame} cur={cur} next={next} total={myTurn?.total} mine={myTurn} mesh={mesh} now={now} send={send} drawRef={drawRef} />
              : <GroupGame cycle={cycle} send={send} leaderboard={leaderboard} you={me} />)}
        {cycle.phase === "break" && chosenGame && joinedCycle === cycle.cycles && !gameOver && (
          <button className="btn small ghost leave-game"
                  onClick={() => { send({ type: "event_optout", cycle: cycle.cycles }); setChosenGame(null); }}>
            {t("Leave the game and rest")}
          </button>
        )}

        <RoomChat messages={messages} members={presence.members} me={me} send={send} />
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
      <section className="panel forest-card">
        <div>
          <h3>{t("Study forest")}</h3>
          <p className="muted small">{t("Grow a tree while you study. Keep the tab open and in front; leave for too long and it wilts. Friends can join with a QR code.")}</p>
        </div>
        <Link className="btn primary small" to={`/forest/${FOREST_ROOM}`}>{t("Enter the forest")}</Link>
      </section>
      {rooms === null && <p className="muted">{t("Loading rooms…")}</p>}
      <ul className="room-list live-list">
        {rooms?.filter((r) => r.code !== FOREST_ROOM).map((r) => (
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
