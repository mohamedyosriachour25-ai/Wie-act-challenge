import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { tx, useLang, useT } from "../lib/i18n.jsx";
import { StageLoader } from "./Common.jsx";
import VisualSheet from "../visuals/VisualSheet.jsx";

export function ErrorNote({ error, onRetry }) {
  const t = useT();
  if (!error) return null;
  return (
    <div className="error-box" role="alert">
      <p>{error.message}</p>
      {error.retryAfter && <p className="small">{t("Try again in about {n} s. Your quota refills continuously.", { n: error.retryAfter })}</p>}
      {onRetry && <button className="btn small" onClick={onRetry}>{t("Try again")}</button>}
    </div>
  );
}

export function Meta({ meta }) {
  const t = useT();
  if (!meta) return null;
  const compact = meta.calls.filter((c) => c.cls === "compact").length;
  const mid = meta.calls.filter((c) => c.cls === "mid").length;
  return (
    <p className="meta small muted">
      {meta.cached ? t("Reused from an earlier answer, no AI call.") : t("AI calls: {compact} compact, {mid} mid-size.", { compact, mid })}
      {meta.degraded && ` ${t("The larger model was busy, so a compact model wrote this. It may be shorter than usual.")}`}
    </p>
  );
}

/** The core view: explanation text on the left, illustration on the right, linked by the active step. */
export function Explanation({ data, wide }) {
  const t = useT();
  const [step, setStep] = useState(-1);
  useEffect(() => setStep(-1), [data.topic, data.title]);
  const focus = step >= 0 ? data.steps[step].focus : "";
  return (
    <article className={`explain${wide ? " wide" : ""}`}>
      <div className="explain-text">
        <h2>{data.title}</h2>
        {data.summary && <p className="lead">{data.summary}</p>}
        {data.grounded_in?.length > 0 && <p className="grounded">{t("Built from your material:")} <strong>{data.grounded_in.join(", ")}</strong></p>}
        <ol className="steps">
          {data.steps.map((s, i) => (
            <li key={i}>
              <button type="button" className={i === step ? "on" : ""} aria-pressed={i === step} onClick={() => setStep(i === step ? -1 : i)}>
                {s.text}
              </button>
            </li>
          ))}
        </ol>
        {data.figure_note && <p className="muted small figure-note">{t("Why this figure:")} {data.figure_note}</p>}
        <p className="muted small">{t("Select a step to highlight it in the figure.")}</p>
        {data.everyday_example && <section className="note"><h3>{t("In daily life")}</h3><p>{data.everyday_example}</p></section>}
        {data.common_mistake && <section className="note warn"><h3>{t("Common mistake")}</h3><p>{data.common_mistake}</p></section>}
        <Meta meta={data.meta} />
      </div>
      <VisualSheet visual={data.visual} fallback={data.visual_fallback} focus={focus} />
    </article>
  );
}

export function HintLadder({ topic }) {
  const t = useT();
  const [question, setQuestion] = useState("");
  const [hints, setHints] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const next = hints.length + 1;
  const labels = [t("Give me a nudge"), t("Name the key idea"), t("Show the first step")];

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api("/api/study/hint", { method: "POST", body: { topic, question, level: next } });
      setHints((h) => [...h, r.hint]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <h3>{t("Stuck on something?")}</h3>
      <p className="muted">{t("Describe where you are stuck. You get hints in three levels and never the final answer.")}</p>
      <label className="grow">
        {t("What are you trying to work out?")}
        <input value={question} onChange={(e) => { setQuestion(e.target.value); setHints([]); }} placeholder={t("e.g. find DE when AD = 3 and AB = 9")} />
      </label>
      <ul className="hints">{hints.map((h, i) => <li key={i}><strong>{t("Hint {n}.", { n: i + 1 })}</strong> {h}</li>)}</ul>
      {busy && <StageLoader stages={[t("Reading your question"), t("Writing a hint")]} />}
      <ErrorNote error={error} />
      {next <= 3 ? (
        <button className="btn" disabled={busy || question.trim().length < 3} onClick={ask}>{labels[next - 1]}</button>
      ) : (
        <p className="muted small">{t("That was the last hint. Try the first step yourself, then check with the quiz below.")}</p>
      )}
    </section>
  );
}

export function SoloQuiz({ topic, level, onFinish }) {
  const t = useT();
  const { lang } = useLang();
  const [quiz, setQuiz] = useState(null);
  const [error, setError] = useState(null);
  const [i, setI] = useState(0);
  const [picked, setPicked] = useState(null);
  const [result, setResult] = useState(null);
  const [score, setScore] = useState(0);
  const [done, setDone] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = async () => {
    setError(null);
    try { const q = await api("/api/study/quiz", { method: "POST", body: { topic, level } }); setQuiz({ ...q, lang }); } catch (e) { setError(e); }
  };
  useEffect(() => { setQuiz(null); setI(0); setPicked(null); setResult(null); setScore(0); setDone(false); load(); }, [topic]);

  const choose = async (idx) => {
    if (picked !== null) return;
    setPicked(idx);
    try {
      const r = await api("/api/study/quiz/answer", { method: "POST", body: { topic, level, question: i, choice: idx, lang: quiz.lang } });
      setResult(r);
      if (r.correct) setScore((s) => s + 1);
    } catch (e) { setError(e); setPicked(null); }
  };
  const advance = async () => {
    if (i + 1 < quiz.questions.length) { setI(i + 1); setPicked(null); setResult(null); return; }
    setDone(true);
    try {
      await api("/api/study/quiz/finish", { method: "POST", body: { topic, correct: score, total: quiz.questions.length } });
      api("/api/review/add_quiz", { method: "POST", body: { topic, level } }).then(() => setSaved(true)).catch(() => {});  // these questions become review cards
      onFinish?.();
    } catch (e) { setError(e); }
  };

  if (error && !quiz) return <section className="panel"><h3>{t("Check yourself")}</h3><ErrorNote error={error} onRetry={load} /></section>;
  if (!quiz) return <section className="panel"><h3>{t("Check yourself")}</h3><StageLoader stages={[t("Choosing questions"), t("Writing answer options"), t("Checking them")]} /></section>;

  const q = quiz.questions[i];
  return (
    <section className="panel">
      <h3>{t("Check yourself")}</h3>
      {done ? (
        <div>
          <p className="score">{t("{score} of {total} correct", { score, total: quiz.questions.length })}</p>
          <p className="muted">{score === quiz.questions.length ? t("Solid. Try a harder topic or explain this one to someone.") : t("Re-read the steps you missed, then try again.")}</p>
          {saved && <p className="muted small">{t("Added to your revision — review them from the Revision tab.")}</p>}
          <button className="btn" onClick={() => { setI(0); setPicked(null); setResult(null); setScore(0); setDone(false); }}>{t("Try again")}</button>
        </div>
      ) : (
        <>
          <p className="muted small">{t("Question {i} of {n}", { i: i + 1, n: quiz.questions.length })}</p>
          <p className="question">{q.q}</p>
          <ul className="options">
            {q.options.map((o, n) => {
              const state = result ? (n === result.answer ? "right" : n === picked ? "wrong" : "") : n === picked ? "pending" : "";
              return (
                <li key={n}>
                  <button className={`option ${state}`} disabled={picked !== null} onClick={() => choose(n)}>{o}</button>
                </li>
              );
            })}
          </ul>
          <ErrorNote error={error} />
          {result && (
            <div className={`feedback ${result.correct ? "right" : "wrong"}`} role="status">
              <strong>{result.correct ? t("Correct.") : t("Not quite.")}</strong> {result.why}
              <div><button className="btn small" onClick={advance}>{i + 1 < quiz.questions.length ? t("Next question") : t("See my score")}</button></div>
            </div>
          )}
        </>
      )}
      <Meta meta={quiz.meta} />
    </section>
  );
}

const STYLE_TEXT = {
  visual: tx("You tend to learn best from pictures and diagrams. Explanations will lean on the figure."),
  stepwise: tx("You tend to learn best step by step. Explanations will be broken into small ordered steps."),
  analogy: tx("You tend to learn best through comparisons. Explanations will start from an everyday example."),
  practice: tx("You tend to learn best by trying things. Explanations will be short and push you to practise."),
  repetition: tx("You tend to learn best with repetition. Key rules will be restated in more than one way."),
  balanced: tx("No single style stood out, so explanations will mix a definition, an example and a figure."),
};
export const STYLE_NAME = { visual: tx("visual"), stepwise: tx("step by step"), analogy: tx("by comparison"), practice: tx("by practice"), repetition: tx("by repetition"), balanced: tx("balanced") };

export function Diagnostic({ onDone }) {
  const t = useT();
  const { lang } = useLang();
  const [qs, setQs] = useState(null);
  const [answers, setAnswers] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => { api("/api/study/diagnostic").then((d) => setQs(d.questions)).catch(setError); }, [lang]);
  const pick = async (idx) => {
    const next = [...answers, idx];
    setAnswers(next);
    if (next.length === qs.length) {
      try { setResult(await api("/api/study/diagnostic", { method: "POST", body: { answers: next } })); } catch (e) { setError(e); }
    }
  };

  if (error) return <ErrorNote error={error} />;
  if (!qs) return <p className="muted">{t("Loading questions…")}</p>;
  if (result)
    return (
      <section className="panel diag">
        <h2>{t("Your learning style: {style}", { style: t(STYLE_NAME[result.learning_style] || STYLE_NAME.balanced) })}</h2>
        <p>{t(STYLE_TEXT[result.learning_style] || STYLE_TEXT.balanced)}</p>
        <p className="muted small">{t("This adapts how EdNova explains things. It stays on your account and you can retake it any time.")}</p>
        <button className="btn primary" onClick={() => onDone(result.learning_style)}>{t("Start studying")}</button>
      </section>
    );
  if (answers.length >= qs.length) return <p className="muted" role="status">{t("Working out your learning style…")}</p>;
  const q = qs[answers.length];
  return (
    <section className="panel diag">
      <h2>{t("Four quick questions so EdNova explains the way you learn")}</h2>
      <p className="muted small">{t("Question {i} of {n}.", { i: answers.length + 1, n: qs.length })} {t("There are no wrong answers.")}</p>
      <p className="question">{q.q}</p>
      <ul className="options">
        {q.options.map((o, n) => <li key={n}><button className="option" onClick={() => pick(n)}>{o}</button></li>)}
      </ul>
    </section>
  );
}

export function ProgressPanel({ refreshKey }) {
  const t = useT();
  const [p, setP] = useState(null);
  const [due, setDue] = useState(0);
  useEffect(() => { api("/api/progress").then(setP).catch(() => {}); api("/api/review/stats").then((s) => setDue(s.due)).catch(() => {}); }, [refreshKey]);
  if (!p) return null;
  return (
    <section className="panel progress">
      <h3>{t("Your progress")}</h3>
      {due > 0 && <a className="banner info" href="/review">{t("{n} cards to review", { n: due })}</a>}
      <dl className="stats three">
        <div className="stat"><dd>{p.xp}</dd><dt>{t("Points")}</dt></div>
        <div className="stat"><dd>{p.streak_days}</dd><dt>{t("Day streak")}</dt></div>
        <div className="stat"><dd>{p.accuracy == null ? t("none") : `${Math.round(p.accuracy * 100)}%`}</dd><dt>{t("Quiz accuracy")}</dt></div>
      </dl>
      {p.topics.length === 0 ? (
        <p className="muted">{t("Nothing yet. Ask about a topic and your progress shows up here.")}</p>
      ) : (
        <ul className="topics">
          {p.topics.map((row) => (
            <li key={row.topic}>
              <span>{row.topic}</span>
              <span className="meter" role="img" aria-label={row.mastery == null ? t("Not quizzed yet") : t("{n} percent", { n: Math.round(row.mastery * 100) })}>
                <i style={{ width: `${(row.mastery ?? 0) * 100}%` }} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
