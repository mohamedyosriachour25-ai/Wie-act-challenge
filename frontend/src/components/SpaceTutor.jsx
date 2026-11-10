import { useCallback, useEffect, useRef, useState } from "react";
import { api, streamRequest } from "../lib/api.js";
import { useT } from "../lib/i18n.jsx";
import { ErrorNote, HintLadder, SoloQuiz } from "./Study.jsx";
import { Answer, StageTrail } from "./Tutor.jsx";
import { MicButton } from "./Voice.jsx";

/* ------------------------------------------------------------------ one space's tutor */
const wantsFigureFor = (intent) => !!intent && (intent.intent === "explain" || (intent.figure && ["question", "follow_up"].includes(intent.intent)));

/** The staged tutor of one study space. `compact` = the narrow live-room notepad (figures stacked, no tools). */
export function SpaceTutor({ space, useWeb, setUseWeb, webAvailable, onActivity = () => {}, compact = false }) {
  const t = useT();
  const [thread, setThread] = useState([]);   // stored messages: { id, role, content, meta }
  const [hasMemory, setHasMemory] = useState(false);
  const [live, setLive] = useState(null);     // the turn being generated
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const endRef = useRef(null);

  const load = useCallback(async () => {
    const data = await api(`/api/spaces/${space.id}/messages`);
    setThread(data.messages);
    setHasMemory(data.has_memory);
    return data;
  }, [space.id]);

  /** Follow a turn's events (a new one, or one still running from an earlier visit) until it ends. */
  const follow = useCallback(async (method, path, body, message) => {
    const controller = new AbortController();
    abortRef.current = controller;
    let state = { message, stages: [], startedAt: Date.now(), done: false };
    setLive(state);
    const update = (patch) => { state = { ...state, ...patch }; setLive(state); };
    try {
      const ran = await streamRequest(method, path, body, (ev) => {
        if (ev.type === "stage") update({ stages: [...state.stages, ev.stage] });
        else if (ev.type === "intent") update({ intent: ev, wantsFigure: wantsFigureFor(ev) });
        else if (ev.type === "sources") update({ sources: ev.sources });
        else if (ev.type === "draft") update({ draft: ev.text });
        else if (ev.type === "final") update({ final: ev.text });
        else if (ev.type === "visual") update({ visual: ev.visual, visualFallback: ev.fallback, figureNote: ev.note, stepFocus: ev.step_focus });
        else if (ev.type === "error") update({ error: ev });
        else if (ev.type === "done") update({ done: true, doneAt: Date.now() });
      }, controller.signal);
      if (state.error && state.error.kind !== "stopped") setError({ message: state.error.detail, retryAfter: state.error.retry_after });
      if (ran && state.done) onActivity();
      setLive(null);
      await load();     // the server saved the answer (or what was produced): show the stored version
    } catch (e) {
      if (e?.name === "AbortError") return;     // left the page: the server keeps going, we reconnect next time
      setError(e);
      setLive(null);
      load().catch(() => {});
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [load, onActivity]);

  useEffect(() => {
    setLive(null); setError(null); setThread([]);
    load().then((data) => {
      if (!data.running) return;
      // an answer is still being written from an earlier visit: pick it up where it is
      const lastUser = [...data.messages].reverse().find((m) => m.role === "user");
      follow("GET", `/api/spaces/${space.id}/turn`, undefined, lastUser?.content || "");
    }).catch(setError);
    return () => abortRef.current?.abort();   // only stops listening; the answer keeps being written on the server
  }, [load, follow, space.id]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [thread.length, live?.stages?.length, live?.final, live?.draft]);

  const send = async (text = input) => {
    const message = text.trim();
    if (message.length < 2 || live) return;
    setError(null); setInput("");
    await follow("POST", `/api/spaces/${space.id}/ask`, { message, web: useWeb }, message);
  };

  const stop = () => api(`/api/spaces/${space.id}/turn`, { method: "DELETE" }).catch(() => {});

  const clear = async () => {
    await api(`/api/spaces/${space.id}/messages`, { method: "DELETE" });
    await load();
  };

  // While a turn is followed live, its saved placeholder (and the question before it) are drawn by the live view.
  const pendingIdx = live ? thread.findLastIndex((m) => m.role === "assistant" && m.meta?.pending) : -1;
  const shown = pendingIdx >= 0 ? thread.filter((_, i) => i !== pendingIdx && i !== pendingIdx - 1) : thread;
  const lastAssistant = [...shown].reverse().find((m) => m.role === "assistant" && !m.meta?.events);
  const started = thread.length > 0 || live;
  return (
    <>
      <div className={`thread${compact ? " compact" : ""}`}>
        {!started && (
          <div className="welcome msg-in">
            <h2>{t("Ask your tutor anything")}</h2>
            <p className="muted">{t("This space remembers your conversation and answers from the material you add to it. Each space keeps its own.")}</p>
          </div>
        )}
        {hasMemory && thread.length > 0 && <p className="small muted memory-note">{t("Earlier parts of this conversation are summarised and still taken into account.")}</p>}

        {shown.map((m, i) => m.role === "user" ? (
          <div className="msg-row you" key={m.id}><div className="bubble msg-in">{m.content}</div></div>
        ) : m.meta?.events ? (
          <Unfinished key={m.id} m={m} question={shown[i - 1]?.role === "user" ? shown[i - 1].content : ""} onRetry={send} busy={!!live} />
        ) : (
          <div className="msg-row tutor" key={m.id}>
            <div className="avatar-sm" aria-hidden="true">H</div>
            <div className="bubble tutor-card msg-in">
              <Answer text={m.content} sources={m.meta.sources} used={m.meta.used} visual={m.meta.visual} visualFallback={m.meta.visual_fallback} figureNote={m.meta.figure_note} meta={m.meta.meta} stepFocus={m.meta.step_focus} />
              {!compact && m === lastAssistant && !live && m.meta.intent && m.meta.intent.intent !== "chat" && (
                <div className="turn-tools">
                  <HintLadder topic={m.meta.intent.topic} />
                  <SoloQuiz topic={m.meta.intent.topic} level={m.meta.intent.level} onFinish={onActivity} />
                </div>
              )}
            </div>
          </div>
        ))}

        {live && (
          <div className="exchange">
            <div className="msg-row you"><div className="bubble msg-in">{live.message}</div></div>
            <div className="msg-row tutor">
              <div className="avatar-sm" aria-hidden="true">H</div>
              <div className="bubble tutor-card msg-in">
                <StageTrail live={live} />
                {live.intent && live.intent.standalone && live.intent.standalone !== live.message && (
                  <p className="small muted understood">{t("Understood as:")} <em>{live.intent.standalone}</em></p>
                )}
                {(live.draft || live.final) && (
                  <Answer text={live.final || live.draft} isDraft={!live.final && live.intent?.intent !== "chat"} sources={live.sources}
                    visual={live.visual} visualFallback={live.visualFallback} figureNote={live.figureNote} wantsFigure={live.wantsFigure} pendingFigure={!live.visual && !live.done} stepFocus={live.stepFocus} />
                )}
              </div>
            </div>
          </div>
        )}
        {error && <ErrorNote error={error} />}
        <div ref={endRef} />
      </div>

      <form className="composer tall" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <textarea
          aria-label={t("What do you want to understand?")}
          value={input}
          rows={1}
          maxLength={800}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder={started ? t("Ask a follow-up, or a new topic") : t("Type the topic you want explained")}
        />
        <MicButton disabled={!!live} onText={(text) => setInput(text)} />
        {live ? (
          <button type="button" className="btn send" onClick={stop}>{t("Stop")}</button>
        ) : (
          <button className="btn primary send" disabled={input.trim().length < 2}>{started ? t("Ask") : t("Explain it")}</button>
        )}
      </form>
      <div className="composer-opts small">
        {webAvailable && (
          <label className="web-toggle">
            <input type="checkbox" checked={useWeb} onChange={(e) => setUseWeb(e.target.checked)} />
            {t("Also look it up on Wikipedia when my material does not cover it")}
          </label>
        )}
        {thread.length > 0 && !live && <button className="link small" onClick={clear}>{t("Clear this conversation")}</button>}
      </div>
    </>
  );
}


/** An answer that did not finish (stopped, interrupted by a restart, or an AI error): what was produced is kept. */
function Unfinished({ m, question, onRetry, busy }) {
  const t = useT();
  const ev = m.meta.events || [];
  const pick = (type) => [...ev].reverse().find((e) => e.type === type);
  const sources = pick("sources")?.sources;
  const visual = pick("visual");
  const why = m.meta.pending ? t("Still being written…")
    : m.meta.stopped ? t("You stopped this answer before it finished.")
    : m.meta.interrupted ? t("This answer was interrupted before it finished (the server restarted).")
    : m.meta.error?.detail || t("This answer could not be finished.");
  return (
    <div className="msg-row tutor">
      <div className="avatar-sm" aria-hidden="true">H</div>
      <div className="bubble tutor-card msg-in unfinished">
        {m.content && (
          <Answer text={m.content} isDraft={!pick("final")} sources={sources} visual={visual?.visual} visualFallback={visual?.fallback}
            figureNote={visual?.note} stepFocus={visual?.step_focus} />
        )}
        <p className="small muted">{why}</p>
        {!m.meta.pending && question && (
          <button className="btn small" disabled={busy} onClick={() => onRetry(question)}>{t("Ask again")}</button>
        )}
      </div>
    </div>
  );
}
