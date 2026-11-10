import { useEffect, useState } from "react";
import { tx, useT } from "../lib/i18n.jsx";
import { StageLoader } from "./Common.jsx";

const TYPE_LABEL = {
  mcq: tx("Multiple choice"), truefalse: tx("True or false"), numeric: tx("Closest number wins"),
  exercise: tx("Exercise"), short: tx("Short answer"),
};

/** Fold one competition socket message into the client state. */
export function compStep(c, m) {
  switch (m.type) {
    case "comp_preparing": return { ...m.competition, status: "preparing" };
    case "comp_ready": return { ...(c || {}), status: "countdown", n: m.n, startsAt: Date.now() + m.starts_in * 1000 };
    case "comp_question": return { ...(c || {}), status: "question", question: m.question, qAt: Date.now(), sent: undefined, answered: 0, reveal: null };
    case "comp_ack": return c ? { ...c, sent: c.pending } : c;
    case "comp_progress": return c && c.question && m.i === c.question.i ? { ...c, answered: m.answered, players: m.players } : c;
    case "comp_reveal": return { ...(c || {}), status: "reveal", reveal: m.reveal, leaderboard: m.reveal.leaderboard };
    case "comp_over": return { ...(c || {}), status: "over", leaderboard: m.leaderboard };
    default: return c;
  }
}

function useSecondsLeft(endsAt) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!endsAt) return undefined;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [endsAt]);
  return endsAt ? Math.max(0, (endsAt - now) / 1000) : 0;
}

function Board({ rows, you, limit = 8 }) {
  const t = useT();
  if (!rows?.length) return null;
  return (
    <ol className="leaderboard comp-board">
      {rows.slice(0, limit).map((r) => (
        <li key={r.pseudo} className={r.pseudo === you ? "me" : ""}>
          <span className="rank">{r.rank}</span><span className="who">{r.pseudo}</span>
          <span className="muted small">{t("{n} right", { n: r.correct })}</span><b>{r.points}</b>
        </li>
      ))}
    </ol>
  );
}

function QuestionCard({ c, onAnswer }) {
  const t = useT();
  const q = c.question;
  const endsAt = c.qAt + q.ends_in * 1000;
  const left = useSecondsLeft(endsAt);
  const [value, setValue] = useState("");
  useEffect(() => setValue(""), [q.i]);
  const sent = c.sent !== undefined;
  const submit = (v) => { if (!sent && left > 0) onAnswer(q.i, v); };
  const pct = Math.max(0, Math.min(100, (left / q.time_s) * 100));
  return (
    <div className="comp-q" aria-live="polite">
      <div className="comp-q-head">
        <span className="small muted">{t("Question {i} of {n}", { i: q.i + 1, n: q.n })}</span>
        <span className="chip small">{t(TYPE_LABEL[q.type])}</span>
        <span className="comp-clock" dir="ltr">{Math.ceil(left)} s</span>
      </div>
      <div className={`meter comp-time${pct < 25 ? " low" : ""}`}><i style={{ width: `${pct}%` }} /></div>
      <p className="comp-prompt">{q.q}</p>
      {q.type === "mcq" && (
        <div className="comp-options">
          {q.options.map((o, k) => (
            <button key={k} className={`btn comp-opt${c.sent === k ? " picked" : ""}`} disabled={sent || left <= 0} onClick={() => submit(k)}>
              <span className="opt-k">{"ABCD"[k]}</span> {o}
            </button>
          ))}
        </div>
      )}
      {q.type === "truefalse" && (
        <div className="comp-options two">
          <button className={`btn comp-opt${c.sent === true ? " picked" : ""}`} disabled={sent || left <= 0} onClick={() => submit(true)}>{t("True")}</button>
          <button className={`btn comp-opt${c.sent === false ? " picked" : ""}`} disabled={sent || left <= 0} onClick={() => submit(false)}>{t("False")}</button>
        </div>
      )}
      {(q.type === "numeric" || q.type === "exercise" || q.type === "short") && (
        <form className="comp-input" onSubmit={(e) => { e.preventDefault(); if (value.trim()) submit(q.type === "short" ? value.trim() : value.trim().replace(",", ".")); }}>
          <input
            value={value} onChange={(e) => setValue(e.target.value)} disabled={sent || left <= 0} autoFocus
            inputMode={q.type === "short" ? "text" : "decimal"} maxLength={q.type === "short" ? 80 : 24}
            aria-label={t("Your answer")} placeholder={q.type === "short" ? t("A word or short phrase") : t("A number")}
          />
          {q.unit && <span className="unit">{q.unit}</span>}
          <button className="btn primary" disabled={sent || left <= 0 || !value.trim()}>{t("Send")}</button>
        </form>
      )}
      {q.type === "exercise" && !sent && <p className="small muted">{t("Work it out on paper: the closer your result, the more points.")}</p>}
      <p className="small muted">
        {sent ? t("Answer sent. Waiting for the others ({a} of {p}).", { a: c.answered || 1, p: c.players || "…" })
          : t("Right and fast earns the most: up to 1000 points.")}
      </p>
    </div>
  );
}

function Reveal({ c, you }) {
  const t = useT();
  const r = c.reveal;
  const mine = r.results.find((x) => x.pseudo === you);
  return (
    <div className="comp-reveal" aria-live="polite">
      <p className="small muted">{t("Question {i} of {n}", { i: r.i + 1, n: c.n })}</p>
      <p className="comp-prompt">{r.q}</p>
      <p className="comp-answer">{t("Answer:")} <strong>{r.type === "truefalse" ? t(r.answer === "true" ? "True" : "False") : r.answer}</strong></p>
      {r.why && <p className="small">{r.why}</p>}
      {mine ? (
        <p className={`comp-mine${mine.points ? " ok" : ""}`}>{mine.points ? t("+{n} points", { n: mine.points }) : t("No points this time")}{mine.closest ? ` · ${t("closest!")}` : ""}</p>
      ) : <p className="small muted">{t("You did not answer this one.")}</p>}
      <ul className="comp-results small">
        {r.results.slice(0, 6).map((x) => (
          <li key={x.pseudo}><span>{x.pseudo}</span><span className="muted">{r.type === "truefalse" ? t(x.value === "true" ? "True" : "False") : x.value}</span><b>+{x.points}</b></li>
        ))}
      </ul>
    </div>
  );
}

/** The study-room competition: start card, preparation, timed questions, reveals, podium. */
export function CompetitionPanel({ comp, topic, busy, you, ownerHere, onStart, onAnswer, onStop }) {
  const t = useT();
  const active = comp && comp.status !== "over";
  return (
    <section className="panel competition">
      <div className="comp-head">
        <h3>{t("Room competition")}</h3>
        {active && (comp.by === you || ownerHere) && <button className="link small" onClick={onStop}>{t("Stop")}</button>}
      </div>
      {!comp && (
        <>
          <p className="muted small">{t("Everyone here answers the same questions on the topic: multiple choice, true or false, the closest number, short answers and small exercises. Right and fast wins.")}</p>
          <button className="btn primary" disabled={!topic || !!busy} onClick={onStart}>{t("Start a competition")}</button>
          {!topic && <p className="small muted">{t("Set a topic first.")}</p>}
        </>
      )}
      {comp?.status === "preparing" && <StageLoader stages={[t("Writing the questions"), t("Checking the answers")]} />}
      {comp?.status === "countdown" && <Countdown at={comp.startsAt} n={comp.n} />}
      {comp?.status === "question" && comp.question && <QuestionCard c={comp} onAnswer={onAnswer} />}
      {comp?.status === "reveal" && comp.reveal && <Reveal c={comp} you={you} />}
      {comp && comp.status !== "preparing" && comp.status !== "over" && comp.leaderboard?.length > 0 && <Board rows={comp.leaderboard} you={you} limit={5} />}
      {comp?.status === "over" && (
        <div className="comp-over">
          <div className="podium">
            {[1, 0, 2].map((k) => comp.leaderboard?.[k] && (
              <div key={k} className={`step s${k + 1}`}>
                <span className="who">{comp.leaderboard[k].pseudo}</span>
                <span className="pts">{comp.leaderboard[k].points}</span>
                <span className="place">{k + 1}</span>
              </div>
            ))}
          </div>
          <Board rows={comp.leaderboard} you={you} />
          <button className="btn" disabled={!topic || !!busy} onClick={onStart}>{t("Play again")}</button>
        </div>
      )}
    </section>
  );
}

function Countdown({ at, n }) {
  const t = useT();
  const left = useSecondsLeft(at);
  return <p className="comp-countdown">{t("{n} questions. Starting in {s} s…", { n, s: Math.ceil(left) })}</p>;
}
