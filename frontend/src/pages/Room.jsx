import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../lib/auth.jsx";
import { useT } from "../lib/i18n.jsx";
import { useStudyClock } from "../lib/studyclock.js";
import { useCycle, useFocusSignal, useRoomSocket } from "../lib/hooks.js";
import { AuthGate, StageLoader, TimerRing } from "../components/Common.jsx";
import { Answer, StageTrail } from "../components/Tutor.jsx";
import { MaterialsPanel } from "../components/Materials.jsx";
import { CompetitionPanel, compStep } from "../components/Competition.jsx";
import { MicButton } from "../components/Voice.jsx";

function RoomQuiz({ quiz, picks, onPick, onReveal, members }) {
  const t = useT();
  const results = quiz.results;
  const answeredAll = Object.values(quiz.answered || {});
  return (
    <section className="panel">
      <h3>{t("Shared quiz")}</h3>
      <p className="muted small">
        {t("Everyone answers on their own. Answers stay hidden until someone reveals them.")}
        {members.length > 0 && ` ${t("{done} of {total} finished.", { done: answeredAll.filter((n) => n === quiz.questions.length).length, total: members.length })}`}
      </p>
      <ol className="rq">
        {quiz.questions.map((q, i) => (
          <li key={i}>
            <p className="question">{q.q}</p>
            <ul className="options">
              {q.options.map((o, n) => {
                const r = results?.questions[i];
                const cls = r ? (n === r.answer ? "right" : picks[i] === n ? "wrong" : "") : picks[i] === n ? "pending" : "";
                return (
                  <li key={n}>
                    <button className={`option ${cls}`} disabled={picks[i] !== undefined || !!results} onClick={() => onPick(i, n)}>
                      <span>{o}</span>
                      {r && <span className="small count">{t("{n} picked", { n: r.picks[n] })}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
            {results && <p className="small feedback right">{results.questions[i].why}</p>}
          </li>
        ))}
      </ol>
      {!results ? (
        <button className="btn" onClick={onReveal}>{t("Reveal answers for everyone")}</button>
      ) : (
        <div>
          <h4>{t("Scores")}</h4>
          <ul className="scores">
            {results.scores.map((s) => <li key={s.pseudo}><span>{s.pseudo}</span><strong>{t("{score} of {total}", { score: s.correct, total: s.total })}</strong></li>)}
          </ul>
        </div>
      )}
    </section>
  );
}

/** Fold one staged-tutor event into the live view (same shape as Solo study's). */
function liveStep(l, ev) {
  const cur = l || { stages: [], startedAt: Date.now(), done: false, wantsFigure: true };
  if (ev.type === "stage") return { ...cur, stages: [...cur.stages, ev.stage] };
  if (ev.type === "intent") return { ...cur, intent: ev };
  if (ev.type === "sources") return { ...cur, sources: ev.sources };
  if (ev.type === "draft") return { ...cur, draft: ev.text };
  if (ev.type === "final") return { ...cur, final: ev.text };
  if (ev.type === "visual") return { ...cur, visual: ev.visual, visualFallback: ev.fallback, figureNote: ev.note, stepFocus: ev.step_focus };
  return cur;
}

const titleOf = (text) => ((text || "").split("\n").find((ln) => ln.trim()) || "").replace(/^#+\s*/, "");

function RoomView({ code }) {
  const { user } = useAuth();
  const t = useT();
  useStudyClock("room");          // studying with friends: counts, a bit less than alone (scoring v2)
  const [room, setRoom] = useState(null);
  const [presence, setPresence] = useState({ members: [] });
  const [chat, setChat] = useState([]);
  const [timer, setTimer] = useState(null);
  const [topic, setTopic] = useState("");
  const [explanation, setExplanation] = useState(null);
  const [live, setLive] = useState(null);   // the shared explanation being generated, stage by stage
  const [liveAsk, setLiveAsk] = useState(null);  // a member's question to the material, stage by stage
  const [comp, setComp] = useState(null);    // the room competition (client view)
  const [you, setYou] = useState("");
  const [quiz, setQuiz] = useState(null);
  const [picks, setPicks] = useState({});
  const [busy, setBusy] = useState(null);
  const [aiError, setAiError] = useState(null);
  const [nudge, setNudge] = useState(null);
  const [draft, setDraft] = useState("");
  const [anon, setAnon] = useState(false);
  const [topicDraft, setTopicDraft] = useState("");
  const [share, setShare] = useState(true);
  const [qa, setQa] = useState([]);
  const [history, setHistory] = useState([]);
  const [summary, setSummary] = useState({ text: "", at: 0 });
  const [matKey, setMatKey] = useState(0);
  const [question, setQuestion] = useState("");
  const [mins, setMins] = useState({ focus: 25, brk: 5 });
  const log = useRef(null);

  const onEvent = useCallback((m) => {
    switch (m.type) {
      case "snapshot":
        setRoom(m.room); setPresence(m.presence); setChat(m.chat); setTimer(m.timer); setTopic(m.topic); setTopicDraft(m.topic);
        setExplanation(m.explanation); setQuiz(m.quiz); setBusy(m.busy); setQa([...(m.qa || [])].reverse()); setYou(m.you);
        setComp(m.competition ? { ...m.competition, qAt: Date.now(), leaderboard: m.competition.leaderboard } : null);
        setHistory(m.history || []); setSummary(m.summary || { text: "", at: 0 });
        // work still being generated when you (re)joined: replay what is already done, the rest keeps arriving
        setLive(m.live_explain ? m.live_explain.reduce(liveStep, null) : null);
        setLiveAsk(m.live_ask ? { ...m.live_ask.events.reduce(liveStep, null), asker: m.live_ask.asker, question: m.live_ask.question } : null);
        break;
      case "presence": setPresence(m.presence); break;
      case "chat": setChat((c) => [...c.slice(-99), m.msg]); setHistory((h) => [...h, { id: m.msg.id, kind: "chat", author: m.msg.anon ? "" : m.msg.author, text: m.msg.text, ts: m.msg.ts, anon: m.msg.anon }]); break;
      case "timer": setTimer(m.timer); break;
      case "topic": setTopic(m.topic); setTopicDraft(m.topic); setExplanation(null); setLive(null); setQuiz(null); setPicks({}); setAiError(null); setHistory((h) => [...h, { id: `top${Date.now()}`, kind: "topic", text: m.topic, ts: Date.now() / 1000 }]); break;
      case "tutor": setLive((l) => liveStep(l, m.event)); setExplanation(null); setAiError(null); break;
      case "explanation": setExplanation(m.data); setLive(null); setAiError(null); setHistory((h) => [...h, { id: `exp${Date.now()}`, kind: "explanation", text: titleOf(m.data.text), ts: Date.now() / 1000 }]); break;
      case "busy": setBusy(m.what); if (m.what) setAiError(null); if (!m.what) { setLive(null); setLiveAsk(null); } break;
      case "tutor_ask": setLiveAsk((l) => ({ ...liveStep(l, m.event), asker: m.asker, question: m.question })); setAiError(null); break;
      case "comp_preparing": case "comp_ready": case "comp_question": case "comp_ack": case "comp_progress": case "comp_reveal": case "comp_over":
        setComp((c) => compStep(c, m)); break;
      case "comp_cancelled": setComp(null); break;
      case "quiz": setQuiz(m.quiz); setPicks({}); break;
      case "quiz_progress": setQuiz((q) => (q ? { ...q, answered: m.answered } : q)); break;
      case "quiz_results": setQuiz((q) => (q ? { ...q, results: m.results } : q)); break;
      case "answer": setQa((a) => [m.data, ...a].slice(0, 6)); setLiveAsk(null); setAiError(null); setHistory((h) => [...h, { id: `ans${Date.now()}`, kind: "answer", author: m.data.asker, text: `${m.data.question} → ${titleOf(m.data.text || m.data.answer)}`, ts: Date.now() / 1000 }]); break;
      case "materials_changed": setMatKey((n) => n + 1); break;
      case "ai_error": setAiError(m); break;
      case "summary": setSummary(m.summary); break;
      case "nudge": setNudge(m.text); break;
      default:
    }
  }, []);
  const { conn, error, send, now } = useRoomSocket(code, onEvent);
  const cycle = useCycle(timer, now);
  const focused = useFocusSignal(share);

  useEffect(() => { send({ type: "focus", focused }); }, [focused, conn]);
  useEffect(() => {
    const id = setInterval(() => send({ type: "focus", focused }), 20000);
    return () => clearInterval(id);
  }, [focused, send]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [history, chat]);

  if (error) {
    return (
      <div className="narrow">
        <h1>{error.code === "room_full" ? t("This room is full") : error.code === "replaced" ? t("Opened in another tab") : t("Cannot join this room")}</h1>
        <p>{error.message}</p>
        <Link className="btn" to="/rooms">{t("Back to study rooms")}</Link>
      </div>
    );
  }
  if (!room) return <p className="muted pad">{conn === "closed" ? t("The connection closed. Reload to rejoin.") : t("Joining the room…")}</p>;

  const maxMsgs = Math.max(1, ...presence.members.map((m) => m.msgs || 0));
  const submitChat = (e) => {
    e.preventDefault();
    if (!draft.trim()) return;
    send({ type: "chat", text: draft, anonymous: anon });
    setDraft("");
  };
  const copy = () => navigator.clipboard?.writeText(`${location.origin}/rooms/${room.code}`).catch(() => {});

  return (
    <>
      <div className="page-head split">
        <div>
          <h1>{room.name}</h1>
          <p className="muted">{t("{online} of {capacity} here.", { online: presence.online, capacity: room.capacity })} {t("Share the code")} <strong className="code">{room.code}</strong> <button className="link" onClick={copy}>{t("Copy invite link")}</button></p>
        </div>
        {conn === "closed" && <p className="banner wait" role="status">{t("Connection lost. Reload to rejoin.")}</p>}
      </div>

      <div className="room">
        <div className="room-main">
          <form className="ask" onSubmit={(e) => { e.preventDefault(); send({ type: "topic", topic: topicDraft }); }}>
            <label className="grow">
              {t("Topic for the room")}
              <input value={topicDraft} onChange={(e) => setTopicDraft(e.target.value)} maxLength={160} placeholder={t("Type the topic to study")} />
            </label>
            <button className="btn" disabled={topicDraft.trim().length < 2 || topicDraft.trim() === topic}>{t("Set topic")}</button>
          </form>
          {topic && <p className="current-topic">{t("Studying now:")} <mark>{topic}</mark></p>}
          <div className="row wrap">
            <button className="btn primary" disabled={!topic || !!busy} onClick={() => send({ type: "explain" })}>{t("Explain for the room")}</button>
            <button className="btn" disabled={!topic || !!busy || (comp && comp.status !== "over")} onClick={() => send({ type: "quiz_start" })}>{t("Start a shared quiz")}</button>
          </div>
          {busy === "quiz" && <StageLoader stages={[t("Choosing questions"), t("Writing answer options")]} />}
          {busy === "ask" && !liveAsk && <StageLoader stages={[t("Finding the right passages"), t("Writing the answer")]} />}
          {busy === "explain" && !live && <StageLoader stages={[t("Reading your material")]} />}
          {aiError && (
            <div className="error-box" role="alert">
              <p>{aiError.message}</p>
              {aiError.retry_after && <p className="small">{t("Try again in about {n} s.", { n: Math.ceil(aiError.retry_after) })}</p>}
            </div>
          )}
          {!topic && <p className="muted">{t("Set a topic, then ask for an explanation. It is generated once for the whole room: everyone sees the same draft, answer and figure appear as they are ready.")}</p>}
          {live && (
            <section className="panel room-answer">
              <StageTrail live={live} />
              {(live.draft || live.final) && (
                <Answer text={live.final || live.draft} isDraft={!live.final} sources={live.sources} visual={live.visual}
                  visualFallback={live.visualFallback} figureNote={live.figureNote} wantsFigure pendingFigure={!live.visual} stepFocus={live.stepFocus} />
              )}
            </section>
          )}
          {explanation && !live && (
            <section className="panel room-answer">
              <Answer text={explanation.text} sources={explanation.sources} used={explanation.used} visual={explanation.visual}
                visualFallback={explanation.visual_fallback} figureNote={explanation.figure_note} meta={explanation.meta} stepFocus={explanation.step_focus} />
            </section>
          )}
          <section className="panel">
            <h3>{t("Ask the room's material")}</h3>
            <form className="ask" onSubmit={(e) => { e.preventDefault(); if (question.trim().length >= 3) { send({ type: "ask", question }); setQuestion(""); } }}>
              <label className="grow">
                {t("Question for everyone")}
                <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} placeholder={t("Type a question about the material")} />
              </label>
              <MicButton disabled={!!busy} onText={(text) => setQuestion(text)} />
              <button className="btn" disabled={!!busy || question.trim().length < 3}>{t("Ask")}</button>
            </form>
            {qa.length === 0 && !liveAsk && <p className="muted small">{t("Answers are shared with the room and cite the document passages they come from.")}</p>}
            {liveAsk && (
              <article className="room-qa live">
                <p className="q"><strong>{liveAsk.asker}</strong> {t("asked:")} {liveAsk.question}</p>
                <StageTrail live={{ ...liveAsk, wantsFigure: false }} />
                {(liveAsk.draft || liveAsk.final) && <Answer text={liveAsk.final || liveAsk.draft} isDraft={!liveAsk.final} sources={liveAsk.sources} />}
              </article>
            )}
            {qa.map((a, i) => (
              <article key={i} className="room-qa">
                <p className="q">{a.asker && <><strong>{a.asker}</strong> {t("asked:")} </>}{a.question}</p>
                <Answer text={a.text || a.answer || ""} sources={a.sources} used={a.used} meta={a.meta} />
              </article>
            ))}
          </section>
          <CompetitionPanel comp={comp} topic={topic} busy={busy} you={you} ownerHere={false}
            onStart={() => send({ type: "comp_start" })}
            onAnswer={(i, value) => { setComp((c) => ({ ...c, pending: value })); send({ type: "comp_answer", i, value }); }}
            onStop={() => send({ type: "comp_stop" })} />
          {quiz && <RoomQuiz quiz={quiz} picks={picks} members={presence.members}
            onPick={(q, c) => { setPicks((p) => ({ ...p, [q]: c })); send({ type: "answer", q, choice: c }); }}
            onReveal={() => send({ type: "quiz_reveal" })} />}
        </div>

        <aside className="room-side">
          <MaterialsPanel scope={room.code} reloadKey={matKey} />

          <section className="panel timer-panel">
            {cycle ? (
              <>
                <TimerRing cycle={cycle} />
                <button className="btn small" onClick={() => send({ type: "timer_stop" })}>{t("Stop timer")}</button>
              </>
            ) : (
              <>
                <h3>{t("Shared timer")}</h3>
                <div className="row">
                  <label className="mini">{t("Focus (min)")}<input type="number" min="5" max="90" value={mins.focus} onChange={(e) => setMins({ ...mins, focus: +e.target.value })} /></label>
                  <label className="mini">{t("Break (min)")}<input type="number" min="1" max="30" value={mins.brk} onChange={(e) => setMins({ ...mins, brk: +e.target.value })} /></label>
                </div>
                <button className="btn small" onClick={() => send({ type: "timer_start", focus_min: mins.focus, break_min: mins.brk })}>{t("Start for everyone")}</button>
              </>
            )}
          </section>

          <section className="panel">
            <h3>{t("Who is here")}</h3>
            <ul className="members">
              {presence.members.map((m) => (
                <li key={m.pseudo}>
                  <span className={`pip ${m.focused === true ? "on" : m.focused === false ? "off" : ""}`} title={m.focused == null ? t("Focus status hidden") : m.focused ? t("Focused") : t("Away")} />
                  <span className="name">{m.pseudo}{m.pseudo === user.pseudo && ` ${t("(you)")}`}</span>
                  <span className="meter" role="img" aria-label={t("{n} messages", { n: m.msgs })}><i style={{ width: `${((m.msgs || 0) / maxMsgs) * 100}%` }} /></span>
                </li>
              ))}
            </ul>
            <p className="muted small">{t("The bar shows how much each person has spoken, so quiet voices are easy to invite in.")}</p>
            <label className="check"><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> {t("Show whether I am focused (computed on this device)")}</label>
          </section>

          <section className="panel chat">
            <div className="row between">
              <h3>{t("Conversation")}</h3>
              <button className="btn small ghost" disabled={!!busy} onClick={() => send({ type: "summarize" })}>{busy === "summary" ? t("Summarising…") : t("Summarise")}</button>
            </div>
            {summary.text && (
              <div className="session-summary">
                <strong className="small">{t("Session summary")}</strong>
                {summary.text.split("\n").filter(Boolean).map((line, i) => (<p key={i} className="small">{line.replace(/^-\s*/, "• ")}</p>))}
              </div>
            )}
            {nudge && <p className="banner info" role="status">{nudge} <button className="link" onClick={() => setNudge(null)}>{t("Dismiss")}</button></p>}
            <ul className="log convo" ref={log} aria-live="polite">
              {history.length === 0 && <li className="muted small">{t("No messages yet.")}</li>}
              {history.map((m) => (
                m.kind === "chat"
                  ? <li key={m.id} className={m.anon ? "anon" : ""}><strong>{m.anon ? t("Anonymous") : m.author}</strong> {m.text}</li>
                  : <li key={m.id} className={`event ${m.kind}`}>
                      <span className="tag">{m.kind === "explanation" ? t("Explanation") : m.kind === "answer" ? t("Answer") : t("Topic")}</span> {m.text}
                    </li>
              ))}
            </ul>
            <form onSubmit={submitChat} className="compose">
              <input aria-label={t("Message")} value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={500} placeholder={anon ? t("Ask anonymously") : t("Write a message")} />
              <button className="btn small">{t("Send")}</button>
            </form>
            <label className="check"><input type="checkbox" checked={anon} onChange={(e) => setAnon(e.target.checked)} /> {t("Send anonymously")}</label>
          </section>
        </aside>
      </div>
    </>
  );
}

export default function Room() {
  const { code } = useParams();
  const t = useT();
  return (
    <AuthGate note={t("Rooms show your pseudo to the other people in them.")}>
      <RoomView key={code} code={code} />
    </AuthGate>
  );
}
