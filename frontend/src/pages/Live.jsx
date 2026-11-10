import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { useAttention, useCycle, useFocusSignal, useRoomSocket, useVideoMesh } from "../lib/hooks.js";
import { tx, useT } from "../lib/i18n.jsx";
import { useStudyClock } from "../lib/studyclock.js";
import { AuthGate, Dots, TimerRing } from "../components/Common.jsx";
import DrawBoard from "../components/DrawBoard.jsx";
import Notepad from "../components/Notepad.jsx";
import RoomForest from "../components/RoomForest.jsx";

// Names and blurbs of the seeded rooms come from the server in English; they are shown translated.
const LIVE_TEXT = [
  tx("Deep work"), tx("Quiet focus, no chat. Reactions only."),
  tx("Long sessions"), tx("Longer blocks for heavy reading and problem sets."),
  tx("Sprints"), tx("Short bursts when motivation is low."),
  tx("Quick demo"), tx("A fast one-minute cycle to try focus, breaks and group games."),
];
const SLOT = 25; // seconds per trivia question
const GAMES = ["trivia_race", "mime", "pictionary"];
const GAME_LABEL = { trivia_race: tx("Quiz"), mime: tx("Mime"), pictionary: tx("Pictionary") };
// Every break's words and trivia come from the server (written by the AI for this break, or from its built-in
// bank when the AI does not answer in time), already in this player's language. Guesses are judged there too.

function useTick(ms = 500) {
  const [, setN] = useState(0);
  useEffect(() => { const id = setInterval(() => setN((n) => n + 1), ms); return () => clearInterval(id); }, [ms]);
}
const secsLeft = (since, seconds) => Math.max(0, Math.ceil(seconds - (performance.now() - since) / 1000));

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
        ) : m.kind === "close" ? (
          <p key={m.id} className="chat-sys chat-close" role="status">{t("Close answer!")}</p>
        ) : m.kind === "dm" ? (
          <p key={m.id} className="chat-line dm">
            <span className="lock" aria-hidden="true">🔒</span>
            <strong>{m.author === me ? t("You") : m.author}</strong>
            <span className="to">{t("to {pseudo}", { pseudo: m.to === me ? t("you") : m.to })}</span> {m.text}
          </p>
        ) : m.blur ? (
          <p key={m.id} className="chat-line blurred">
            <strong>{m.author === me ? t("You") : m.author}</strong>{" "}
            <span className="blur-bar" style={{ width: `${Math.max(3, Math.min(18, m.len || 6))}ch` }} />
            <span className="sr-only">{t("A close guess, hidden")}</span>
          </p>
        ) : (
          <p key={m.id} className="chat-line">
            <strong>{m.author === me ? t("You") : m.author}</strong> {m.text}
            {m.close && <span className="close-tag">{t("Close answer")}</span>}
          </p>
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

function TriviaItem({ item, mine, reveal, onPick }) {
  const t = useT();
  return (
    <>
      <p className="question">{item.q}</p>
      <ul className="options">
        {item.o.map((o, n) => {
          const cls = reveal ? (n === item.a ? "right" : mine?.pick === n ? "wrong" : "") : mine?.pick === n ? "pending" : "";
          return <li key={n}><button className={`option ${cls}`} disabled={!!mine || reveal} onClick={() => onPick(n)}>{o}</button></li>;
        })}
      </ul>
    </>
  );
}

function BreakGame({ cycle, content }) {
  const t = useT();
  const [answers, setAnswers] = useState({});
  const trivia = content?.trivia || [];
  if (!trivia.length) return <section className="panel game"><h3>{t("Break trivia")}</h3><p className="muted small" role="status">{t("Loading questions…")}</p></section>;
  const slot = Math.floor(cycle.into / SLOT);
  const key = `${cycle.cycles}-${slot}`;
  const item = trivia[slot % trivia.length];
  const reveal = cycle.into - slot * SLOT >= SLOT - 6;
  const score = Object.values(answers).filter((v) => v.right).length;
  const mine = answers[key];
  return (
    <section className="panel game">
      <h3>{t("Break trivia")}</h3>
      <p className="muted small">{t("Same question for everyone in the room. Your score stays on your device.")}</p>
      <TriviaItem item={item} mine={mine} reveal={reveal} onPick={(n) => setAnswers((a) => ({ ...a, [key]: { pick: n, right: n === item.a } }))} />
      <p className="small muted" aria-live="polite">{reveal ? (mine ? (mine.right ? t("Correct.") : t("Not this time.")) : t("Time is up for this one.")) : t("Next question in a few seconds.")} {t("Score: {n}", { n: score })}</p>
    </section>
  );
}

function GroupGame({ cycle, content, send, leaderboard, you }) {
  const t = useT();
  const [answered, setAnswered] = useState({});
  const trivia = content?.trivia || [];
  if (!trivia.length) return <section className="panel game"><h3>{t("Group trivia race")}</h3><p className="muted small" role="status">{t("Loading questions…")}</p></section>;
  const slot = Math.floor(cycle.into / SLOT);
  const key = `${cycle.cycles}-${slot}`;
  const item = trivia[slot % trivia.length];
  const reveal = cycle.into - slot * SLOT >= SLOT - 6;
  const mine = answered[key];
  const pick = (n) => {
    if (mine || reveal) return;
    const ms = Math.max(0, (cycle.into - slot * SLOT) * 1000);
    setAnswered((a) => ({ ...a, [key]: { pick: n } }));
    send({ type: "game_result", cycle: cycle.cycles, game: "trivia_race", round: slot, pick: n, correct: n === item.a, ms });
  };
  return (
    <section className="panel game">
      <h3>{t("Group trivia race")}</h3>
      <TriviaItem item={item} mine={mine} reveal={reveal} onPick={pick} />
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

/** Mime / Pictionary, played in rounds until the break ends. While it runs, the game takes the middle of the room:
 * the performer's camera (mime) or the live board (pictionary) sits next to the chat. Everyone holds their own
 * secret word (distinct from everyone else's) and can swap it a few times per round. A turn ends on time, when the
 * performer passes, or when every other player has guessed. */
function GameStage({ tg, me, mesh, send, drawRef, prepSend, prepKey, chat }) {
  const t = useT();
  useTick(500);
  const isPic = tg.game === "pictionary";
  const done = tg.done || [];
  const idx = tg.order.indexOf(me);
  const after = idx > 0 ? tg.order[idx - 1] : null;
  const reason = { pass: t("They passed."), all: t("Everyone got it!"), time: t("Time is up."), away: t("They left.") };
  const turnKey = tg.current ? `${tg.current.round}.${tg.current.index}` : "idle";

  let clock;
  if (tg.myTurn) clock = t("Your turn · {n}s", { n: secsLeft(tg.myTurn.at, tg.myTurn.seconds) });
  else if (tg.current) clock = t("{pseudo} is playing · {n}s", { pseudo: tg.current.pseudo, n: secsLeft(tg.current.at, tg.current.seconds) });
  else if (!done.length && !tg.round) clock = t("Starts in {n}s", { n: secsLeft(tg.startAt, tg.prep) });
  else clock = t("Next turn in a moment…");

  const swapBtn = tg.word && (
    <button className="btn small ghost" disabled={!tg.swapsLeft} onClick={() => send({ type: "word_swap" })}
            title={t("{n} changes left this round", { n: tg.swapsLeft ?? 0 })}>
      {t("Too hard? Change my word")} · {tg.swapsLeft ?? 0}
    </button>
  );

  let focus;
  if (tg.myTurn) {
    focus = isPic
      ? <DrawBoard key={`me-${tg.myTurn.round}.${tg.myTurn.index}`} editable send={send} drawRef={drawRef} />
      : mesh.localStream
        ? <VideoTile stream={mesh.localStream} label={t("You")} mirror />
        : <div className="stage-empty"><button className="btn primary" onClick={mesh.perform}>{t("Go on stage (camera + mic)")}</button></div>;
  } else if (tg.current) {
    const tile = mesh.tiles.find((x) => x.pseudo === tg.current.pseudo);
    focus = isPic
      ? <DrawBoard key={turnKey} editable={false} send={send} drawRef={drawRef} />
      : tile
        ? <VideoTile stream={tile.stream} label={tg.current.pseudo} muted={false} />
        : <div className="stage-empty"><p className="muted">{t("Waiting for {pseudo} to turn their camera on…", { pseudo: tg.current.pseudo })}</p></div>;
  } else {
    focus = (
      <div className="stage-empty">
        {tg.lastEnd
          ? <p className="turn-reveal">{tg.lastEnd.pseudo === me ? t("Your word was: {word}", { word: tg.lastEnd.word }) : t("{pseudo}'s word was: {word}", { pseudo: tg.lastEnd.pseudo, word: tg.lastEnd.word })} {reason[tg.lastEnd.reason] || ""}</p>
          : <p className="muted">{t("Get ready — the first player starts soon.")}</p>}
      </div>
    );
  }

  return (
    <section className="panel game game-stage" aria-label={t(GAME_LABEL[tg.game] || tg.game)}>
      <div className="turn-head">
        <h3>{t(GAME_LABEL[tg.game] || tg.game)}{tg.round ? <span className="muted small"> · {t("Round {n}", { n: tg.round + 1 })}</span> : null}</h3>
        <ol className="turn-order" aria-label={t("Playing order")}>
          {tg.order.map((p) => {
            const state = tg.current?.pseudo === p ? "now" : done.includes(p) ? "done" : tg.current?.next === p ? "next" : "";
            return <li key={p} className={`${state} ${p === me ? "me" : ""}`}>{p === me ? t("You") : p}</li>;
          })}
        </ol>
        <span className="turn-clock" aria-live="polite">{clock}</span>
      </div>

      <div className="game-stage-body">
        <div className={`game-focus ${isPic ? "board" : "video"}`}>
          {tg.myTurn && <p className="prompt-big">{isPic ? t("Draw: {x}", { x: tg.myTurn.word }) : t("Act out: {x}", { x: tg.myTurn.word })}</p>}
          {focus}
          {tg.myTurn && (
            <div className="row your-turn-actions">
              <p className="muted small">{isPic ? t("Draw it with the tools below — don't write the word!") : t("Act it out — don't say the word!")}</p>
              {!isPic && mesh.cameraOn && <button className="btn ghost small" onClick={mesh.leaveStage}>{t("Leave the stage")}</button>}
              {swapBtn}
              <button className="btn small ghost" onClick={() => send({ type: "turn_pass" })}>{t("Pass my turn")}</button>
            </div>
          )}
          {tg.current && !tg.myTurn && <p className="muted small">{t("Type your guess in the chat!")}</p>}
        </div>
        <div className="game-chat">{chat}</div>
      </div>

      {!tg.myTurn && tg.word && (
        <div className="game-side">
          <div className="my-word">
            <span className="my-word-label">{t("Your word")}</span>
            <strong>{tg.word}</strong>
            <p className="small muted">{tg.current?.next === me
              ? t("You're next — get ready!")
              : after ? t("Keep it secret. You play right after {pseudo}.", { pseudo: after }) : t("Keep it secret. You play first.")}</p>
            {swapBtn}
            {tg.swapDenied && <p className="small muted" role="status">{tg.swapDenied === "limit" ? t("No more word changes this round.") : t("Too late to change — someone already guessed it.")}</p>}
          </div>
          {isPic && (
            <div className="prep-board">
              <p className="small"><strong>{t("Your practice board")}</strong> — {t("only you see it. It goes live when your turn starts.")}</p>
              <DrawBoard key={prepKey} editable send={prepSend} drawRef={null} />
            </div>
          )}
        </div>
      )}
      {tg.myTurn && tg.swapDenied && <p className="small muted" role="status">{tg.swapDenied === "limit" ? t("No more word changes this round.") : t("Too late to change — someone already guessed it.")}</p>}
    </section>
  );
}

function VideoTile({ stream, label, mirror = false, muted = true, badge = null }) {
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
      <figcaption>{label}{badge}</figcaption>
    </figure>
  );
}

const TREE_ICON = (x) => (!x ? null : x.state === "wilted" ? "🥀" : x.state === "grown" ? "🌳" : x.p >= 0.5 ? "🌿" : "🌱");

/** One seat on the camera wall: live video when that person shares their camera, otherwise their initial.
 * Only seats with video use `.vtile`, so the wall's video count stays exact. */
function Seat({ pseudo, self, stream, focused, tree, mesh, video = false }) {
  const t = useT();
  const label = self ? t("You") : pseudo;
  const icon = TREE_ICON(tree);
  const badge = (
    <>
      {focused != null && <span className={`pip ${focused ? "on" : "off"}`} title={focused ? t("Focused") : t("Away")} />}
      {icon && <span className="seat-tree" title={t("Pocket tree")} aria-hidden="true">{icon}</span>}
    </>
  );
  if (stream) return <VideoTile stream={stream} label={label} mirror={self} muted={self} badge={badge} />;
  return (
    <figure className={`seat ${self ? "self" : ""}`}>
      <div className="seat-avatar" aria-hidden="true">{(pseudo || "?")[0].toUpperCase()}</div>
      {self ? (
        <button className="btn small primary seat-cam" disabled={mesh.starting} onClick={mesh.toggle}>
          {mesh.starting ? t("Starting…") : t("Turn camera on")}
        </button>
      ) : <span className="seat-note small">{!video ? t("Camera off")
          : mesh.cameraOn ? t("Connecting…")
          : t("Camera on — turn yours on to see it")}</span>}
      <figcaption>{label}{badge}</figcaption>
    </figure>
  );
}

/** The heart of a live room: everyone studying here, camera or not, so the room always feels like company. */
function Stage({ mesh, presence, me, trees, floats, send }) {
  const t = useT();
  const { cameraOn, starting, error, tiles, localStream, toggle } = mesh;
  const treeOf = Object.fromEntries(trees.map((x) => [x.pseudo, x]));
  const others = presence.members.filter((m) => m.pseudo !== me);
  const self = presence.members.find((m) => m.pseudo === me);
  const tileOf = new Map(tiles.map((tl) => [tl.pseudo, tl]));
  const strays = tiles.filter((tl) => !others.some((m) => m.pseudo === tl.pseudo));   // sharing but not in the presence list
  const seats = 1 + others.length + strays.length;
  const share = presence.tracking ? Math.round((presence.focused / presence.tracking) * 100) : null;
  return (
    <section className="panel camera stage" aria-labelledby="stage-title">
      <div className="stage-head">
        <div className="stage-count">
          <p className="big-num">{presence.online}</p>
          <div>
            <h2 id="stage-title">{t("Studying together")}</h2>
            <p className="muted small">{presence.online === 1 ? t("person here") : t("people here")}{share != null && <>{t(", {n}% focused right now", { n: share })}</>}</p>
          </div>
        </div>
        <div className="stage-actions">
          <div className="react-bar">
            <div className="floats" aria-hidden="true">{floats.map((f) => <span key={f.id} style={{ left: `${f.x}%` }}>{f.emoji}</span>)}</div>
            <div className="row">
              {["👏", "🔥", "💡", "☕"].map((e) => <button key={e} className="chip emoji" aria-label={t("Send {emoji}", { emoji: e })} onClick={() => send({ type: "reaction", emoji: e })}>{e}</button>)}
            </div>
          </div>
          {/* turning the camera on happens on your own seat; once it is on, it is turned off up here */}
          {cameraOn && <button className="btn small ghost" disabled={starting} onClick={toggle}>{t("Turn camera off")}</button>}
        </div>
      </div>
      {error?.message === "denied" && <p className="error-box small">{t("HerwieGO could not use your camera. Check the camera permission in your browser.")}</p>}
      {error?.message === "full" && <p className="error-box small">{t("The camera wall is full ({n} cameras). Try again when someone turns theirs off.", { n: error.retryAfter })}</p>}
      <div className={`stage-grid n${Math.min(seats, 9)}`}>
        <Seat pseudo={me} self stream={localStream} focused={self?.focused ?? null} tree={treeOf[me]} mesh={mesh} />
        {others.map((m) => <Seat key={m.pseudo} pseudo={m.pseudo} stream={tileOf.get(m.pseudo)?.stream} video={!!m.video} focused={m.focused ?? null} tree={treeOf[m.pseudo]} mesh={mesh} />)}
        {strays.map((tl) => <VideoTile key={tl.id} stream={tl.stream} label={tl.pseudo} muted={false} />)}
      </div>
      {presence.online > presence.members.length && <p className="muted small">{t("And {n} more.", { n: presence.online - presence.members.length })}</p>}
      <p className="muted small">{t("Your video goes straight to the other cameras here, peer to peer. It never reaches our servers and is not recorded.")}</p>
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
    else if (status === "denied") line = t("HerwieGO could not use your camera. Check the camera permission in your browser.");
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
  const [tg, setTg] = useState(null);         // the turn game this player is in (order, own word, current turn…)
  const [content, setContent] = useState(null); // this break's trivia (from the server, in this player's language)
  const [prepKey, setPrepKey] = useState(0);
  const [messages, setMessages] = useState([]);
  const [phoneAlertAt, setPhoneAlertAt] = useState(0);
  const [pocketTrees, setPocketTrees] = useState([]);   // everyone's live pocket tree in this room
  const [forestLog, setForestLog] = useState([]);       // trees that finished (grew or withered) here
  const nextId = useRef(0);
  const prepStrokes = useRef([]);   // pictionary: the private practice sketch, sent when the turn starts
  const contentAsked = useRef(-1);
  const startSent = useRef(-1);
  const signalRef = useRef(null);
  const drawRef = useRef(null);     // the pictionary board registers here to receive strokes
  const chosenRef = useRef(null);
  useEffect(() => { chosenRef.current = chosenGame; }, [chosenGame]);

  const onEvent = useCallback((m) => {
    if (m.type === "snapshot") {
      setRoom(m.room); setPresence(m.presence); setTimer(m.timer); setMyId(m.you_id); setMe(m.you);
      if (m.event) { setEvent(m.event); if (m.event.counts) setCounts(m.event.counts); }
      setPocketTrees(m.pocket_trees || []); setForestLog(m.forest || []);
    }
    if (m.type === "phone_trees") setPocketTrees(m.trees || []);
    if (m.type === "tree" && m.entry) setForestLog((l) => [...l.slice(-199), m.entry]);
    if (m.type === "presence") setPresence(m.presence);
    if (m.type === "event_participants") { if (m.counts) setCounts(m.counts); }
    if (m.type === "event_scores") { if (!m.game || m.game === chosenRef.current) setLeaderboard(m.leaderboard); }
    if (m.type === "game_start") {
      prepStrokes.current = []; setPrepKey((k) => k + 1);
      setTg({ game: m.game, order: m.order || [], perTurn: m.per_turn, prep: m.prep, startAt: performance.now(),
              word: null, swapsLeft: 0, round: 0, current: null, myTurn: null, lastEnd: null, done: [], over: false });
    }
    if (m.type === "game_word") {
      if (!m.now) { prepStrokes.current = []; setPrepKey((k) => k + 1); }   // a new word: a fresh practice board
      setTg((g) => g && { ...g, word: m.word, swapsLeft: m.swaps_left, swapDenied: null,
                          myTurn: m.now && g.myTurn ? { ...g.myTurn, word: m.word } : g.myTurn });
    }
    if (m.type === "game_swap_denied") setTg((g) => g && { ...g, swapDenied: m.reason });
    if (m.type === "game_round") setTg((g) => g && { ...g, order: m.order || g.order, round: m.round, done: [] });
    if (m.type === "game_turn") setTg((g) => g && { ...g, current: { pseudo: m.pseudo, round: m.round, index: m.index, seconds: m.seconds, next: m.next, at: performance.now() } });
    if (m.type === "game_your_turn") {
      setTg((g) => g && { ...g, swapDenied: null, myTurn: { round: m.round, index: m.index, seconds: m.seconds, word: m.word, at: performance.now() } });
      if (m.game === "pictionary") {        // the practice sketch goes live (after the live board has mounted)
        const sketch = prepStrokes.current; prepStrokes.current = [];
        setTimeout(() => sketch.forEach((st) => sendRef.current?.(st)), 200);
      }
    }
    if (m.type === "game_turn_end") {
      setTg((g) => g && { ...g, current: null, myTurn: null, lastEnd: { pseudo: m.pseudo, word: m.word, reason: m.reason }, done: [...g.done, m.pseudo] });
      if (m.leaderboard) setLeaderboard(m.leaderboard);
    }
    if (m.type === "game_over") { setTg((g) => g && { ...g, over: true, current: null, myTurn: null }); if (m.leaderboard) setLeaderboard(m.leaderboard); }
    if (m.type === "game_close") setMessages((x) => [...x.slice(-80), { id: `close${performance.now()}`, kind: "close" }]);
    if (m.type === "game_content") setContent(m);
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
    if (m.type === "phone_tree_alert") setPhoneAlertAt(Date.now());
  }, []);
  useEffect(() => {
    if (!phoneAlertAt) return;
    const id = setTimeout(() => setPhoneAlertAt(0), 6000);
    return () => clearTimeout(id);
  }, [phoneAlertAt]);
  const { conn, error, send, now } = useRoomSocket(code, onEvent);
  const mesh = useVideoMesh(send, myId);
  signalRef.current = mesh.signal;
  const cycle = useCycle(timer, now);
  useStudyClock(cycle?.phase === "focus" ? "live" : null, { idleMs: 15 * 60 * 1000 });   // focus minutes count; a break does not (reading on paper is fine: 15 min idle allowed)
  const attention = useAttention(watch && share, cycle?.phase === "focus"); // camera only runs during focus
  const focused = useFocusSignal(share, attention.status === "running" ? attention.attentive : null);

  useEffect(() => { setLeaderboard([]); setChosenGame(null); setCounts({}); setTg(null); }, [cycle?.cycles]);
  const sendRef = useRef(null);
  sendRef.current = send;
  const prepSend = useCallback((msg) => {       // the practice board records instead of sending
    if (msg.op === "stroke") prepStrokes.current.push(msg);
    else if (msg.op === "clear") prepStrokes.current = [];
  }, []);

  // Ask for this break's trivia once the game invite is up or the break has started.
  const wantContent = !!cycle && (cycle.phase === "break" || (!!event && cycle.remaining <= event.lead_s));
  useEffect(() => {
    if (wantContent && conn === "open" && contentAsked.current !== cycle.cycles) {
      contentAsked.current = cycle.cycles;
      send({ type: "game_content", cycle: cycle.cycles });
    }
  }, [wantContent, conn, cycle?.cycles, send]);
  const breakContent = content && cycle && content.cycle === cycle.cycles ? content : null;

  // A running mime / pictionary game takes over the middle of the room until it ends.
  const turnGameOn = !!cycle && cycle.phase === "break" && joinedCycle === cycle.cycles && !!tg && !tg.over
    && (chosenGame === "mime" || chosenGame === "pictionary");
  const mimeOn = turnGameOn && tg.game === "mime";
  const { watch: meshWatch, unwatch: meshUnwatch } = mesh;
  useEffect(() => {                  // mime players receive the performer's video even with their own camera off
    if (mimeOn && conn === "open") meshWatch();
    else if (!mimeOn) meshUnwatch();
  }, [mimeOn, conn, mesh.cameraOn, meshWatch, meshUnwatch]);

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

  return (
    <div className="live-room">
      <div className="page-head room-head">
        <h1>{t(room.name)}</h1>
        <p className="muted">{t("{focus} minutes of focus, then {brk} minutes of break, on repeat. Everyone sees the same clock.", { focus: room.focus_min, brk: room.break_min })}</p>
      </div>
      <div className="live live-layout">
        <div className="live-main">
          {turnGameOn
            ? <GameStage tg={tg} me={me} mesh={mesh} send={send} drawRef={drawRef} prepSend={prepSend} prepKey={prepKey}
                         chat={<RoomChat messages={messages} members={presence.members} me={me} send={send} />} />
            : <Stage mesh={mesh} presence={presence} me={me} trees={pocketTrees} floats={floats} send={send} />}

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
            ? <BreakGame cycle={cycle} content={breakContent} />
            : tg?.over
              ? <section className="panel game"><h3>{t("Game over")}</h3>
                  {tg.lastEnd && <p className="small turn-reveal">{tg.lastEnd.pseudo === me ? t("Your word was: {word}", { word: tg.lastEnd.word }) : t("{pseudo}'s word was: {word}", { pseudo: tg.lastEnd.pseudo, word: tg.lastEnd.word })}</p>}
                  <p className="muted small">{t("Nice! Back to focus soon.")}</p></section>
              : chosenGame === "mime" || chosenGame === "pictionary"
                ? (tg ? null
                      : <section className="panel game"><h3>{t(GAME_LABEL[chosenGame])}</h3><p className="muted small" role="status">{t("Getting the game ready…")}</p></section>)
                : <GroupGame cycle={cycle} content={breakContent} send={send} leaderboard={leaderboard} you={me} />)}
          {cycle.phase === "break" && chosenGame && joinedCycle === cycle.cycles && !tg?.over && (
            <button className="btn small ghost leave-game"
                    onClick={() => { send({ type: "event_optout", cycle: cycle.cycles }); setChosenGame(null); }}>
              {t("Leave the game and rest")}
            </button>
          )}

          {!turnGameOn && <RoomChat messages={messages} members={presence.members} me={me} send={send} />}
        </div>

        <aside className="live-rail">
          <section className="panel center-panel">
            <TimerRing cycle={cycle} label={cycle.phase === "focus" ? t("Focus. Work on your own thing.") : t("Break. Stretch, drink water.")} />
            <label className="check"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> {t("Show whether I am focused")}</label>
            <AttentionCheck watch={watch} setWatch={setWatch} share={share} attention={attention} />
            {phoneAlertAt > 0 && <p className="small tree-status wilted" role="status" aria-live="assertive">{t("Your pocket tree withered — come back and stay focused!")}</p>}
          </section>
          <RoomForest code={code} trees={pocketTrees} log={forestLog} me={me} />
        </aside>

        <aside className="live-notes">
          <Notepad />
        </aside>
      </div>
    </div>
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
