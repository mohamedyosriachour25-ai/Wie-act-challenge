import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { LANGS, tx, useLang, useT } from "../lib/i18n.jsx";
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

export const STYLE_NAME = { visual: tx("visual"), stepwise: tx("step by step"), analogy: tx("by comparison"), practice: tx("by practice"), repetition: tx("by repetition"), balanced: tx("balanced") };

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

const RULE_LABEL = {
  signup: tx("Welcome bonus"), daily_login: tx("Daily visit"), streak: tx("Day streak bonus"),
  explain: tx("Explanation studied"), quiz_correct: tx("Correct quiz answer"), quiz_perfect: tx("Perfect quiz"),
  group_quiz: tx("Correct answer in a study room"), resume: tx("Revision sheet"), review_card: tx("Flashcard reviewed"),
  game: tx("Break game"), pocket_tree: tx("Pocket tree grown"), study_time: tx("Focused study minute"),
  material: tx("Course material added"), work_day: tx("Working day reached"), work_streak: tx("Working-day streak"),
  group_time: tx("Study minutes with friends"), competition: tx("Room competition"),
};
const KIND_LABEL = { productive: tx("Productive work"), rhythm: tx("Working days"), social: tx("Social play") };

const hm = (t, minutes) => (minutes >= 60 ? t("{h} h {m} min", { h: Math.floor(minutes / 60), m: minutes % 60 }) : t("{m} min", { m: minutes }));

/** Last 7 days: focused minutes (solid) and minutes with friends (light), a dot on working days. */
function WeekBars({ days }) {
  const t = useT();
  const { lang } = useLang();
  const html = LANGS.find((l) => l.code === lang)?.html || "en";
  const fmt = new Intl.DateTimeFormat(html, { weekday: "short", timeZone: "UTC" });
  const top = Math.max(60, ...days.map((d) => d.focused + d.group));
  return (
    <div className="week-bars" role="img" aria-label={t("Study minutes over the last 7 days")}>
      {days.map((d) => (
        <div key={d.day} className="wb-col" title={`${hm(t, d.focused)} · ${t("with friends")}: ${hm(t, d.group)}`}>
          <div className="wb-bar">
            <i className="g" style={{ height: `${(d.group / top) * 100}%` }} />
            <i className="f" style={{ height: `${(d.focused / top) * 100}%` }} />
          </div>
          <span className={`wb-day${d.work_day ? " work" : ""}`}>{fmt.format(new Date(d.day * 86400000))}</span>
        </div>
      ))}
    </div>
  );
}

/** The account score (GET /api/score/me), scoring v2: productivity, working days and their streak, then social play. */
export function ScorePanel({ refreshKey }) {
  const t = useT();
  const [s, setS] = useState(null);
  const [rules, setRules] = useState(null);
  const [period, setPeriod] = useState("week");
  const [board, setBoard] = useState(null);
  useEffect(() => {
    const load = () => api("/api/score/me").then(setS).catch(() => {});
    load();
    const id = setInterval(load, 120_000);      // minutes keep adding up while the page is open
    return () => clearInterval(id);
  }, [refreshKey]);
  useEffect(() => { api(`/api/score/leaderboard?period=${period}`).then(setBoard).catch(() => {}); }, [period, refreshKey]);
  if (!s) return null;
  const label = (rule) => t(RULE_LABEL[rule] || rule);
  const d = s.today;   // {focused, group, effective, goal, work_day}
  const pts = s.week?.points_by_kind || { productive: 0, rhythm: 0, social: 0 };
  const sum = Math.max(1, pts.productive + pts.rhythm + pts.social);
  const goalPct = d ? Math.min(100, Math.round((d.effective / d.goal) * 100)) : 0;
  return (
    <section className="panel score-panel">
      <div className="score-head">
        <div>
          <h3>{t("Your score")}</h3>
          <p className="muted small">{s.rank ? t("Rank #{n} overall", { n: s.rank }) : t("No points yet")}</p>
        </div>
        <span className="level-badge" aria-label={t("Level {n}", { n: s.level })}>{t("Level {n}", { n: s.level })}</span>
      </div>
      <p className="score-total"><strong>{s.total}</strong> {t("points")}</p>
      <div className="meter level-meter" role="img" aria-label={s.next ? t("{n} points to the next level", { n: s.next - s.total }) : t("Top level")}>
        <i style={{ width: `${Math.round(s.progress * 100)}%` }} />
      </div>
      <p className="muted small">{s.next ? t("{n} points to the next level", { n: s.next - s.total }) : t("Top level")}
        {" · "}{t("+{n} today", { n: s.today_points })}</p>

      {d && (
        <div className="workday">
          <div className="wd-head">
            <strong>{t("Today")}</strong>
            <span className="small">{d.work_day ? t("Working day reached") : t("{n} min to a working day", { n: Math.max(0, d.goal - d.effective) })}</span>
          </div>
          <div className={`meter wd-meter${d.work_day ? " done" : ""}`}><i style={{ width: `${goalPct}%` }} /></div>
          <p className="muted small">{t("Focused")}: {hm(t, d.focused)}{d.group ? ` · ${t("with friends")}: ${hm(t, d.group)}` : ""}</p>
          <p className="streak-line">{s.work_streak > 0 ? t("{n} working days in a row", { n: s.work_streak }) : t("Reach a working day to start a streak")}</p>
        </div>
      )}
      {s.days && <WeekBars days={s.days} />}
      {s.week && (
        <>
          <p className="small week-line">
            {t("This week")}: <b>{hm(t, s.week.focused)}</b> {t("focused")} · <b>{s.week.work_days}</b> {t("working days")} ·{" "}
            {t("productivity")} <b>{s.week.productivity}</b>/100
          </p>
          <div className="kind-bar" role="img" aria-label={t("Points this week by kind")}>
            {["productive", "rhythm", "social"].map((k) => <i key={k} className={k} style={{ width: `${(pts[k] / sum) * 100}%` }} />)}
          </div>
          <ul className="kind-legend small">
            {["productive", "rhythm", "social"].map((k) => <li key={k}><span className={`sw ${k}`} />{t(KIND_LABEL[k])} {pts[k]}</li>)}
          </ul>
        </>
      )}
      <details className="how-points" onToggle={(e) => { if (e.target.open && !rules) api("/api/score/rules").then(setRules).catch(() => {}); }}>
        <summary className="small">{t("How points work")}</summary>
        <p className="small">{t("A working day is {n} effective minutes: focused minutes, plus 0.6 per minute with friends, plus 2 per productive action. Each working day gives 20 points and grows your streak (5 points per day in a row, up to 10).", { n: rules?.work_day_minutes ?? 25 })}</p>
        {rules && (
          <table className="rules-table small">
            <tbody>
              {rules.rules.map((r) => (
                <tr key={r.rule} className={r.kind}>
                  <td>{label(r.rule)}</td>
                  <td>+{r.rule === "group_time" ? "0.6" : r.points}</td>
                  <td className="muted">{r.daily_cap ? t("max {n}/day", { n: r.daily_cap }) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
      {s.recent.length > 0 && (
        <ul className="score-recent">
          {s.recent.slice(0, 5).map((r, i) => <li key={i}><span>{label(r.rule)}</span><b>+{r.points}</b></li>)}
        </ul>
      )}
      <div className="board-head">
        <h4>{t("Leaderboard")}</h4>
        <div className="row">
          <button className={`chip${period === "week" ? " on" : ""}`} onClick={() => setPeriod("week")}>{t("This week")}</button>
          <button className={`chip${period === "all" ? " on" : ""}`} onClick={() => setPeriod("all")}>{t("All time")}</button>
        </div>
      </div>
      {board && (board.top.length === 0
        ? <p className="muted small">{t("No points yet")}</p>
        : <ol className="leaderboard">
            {board.top.map((r) => (
              <li key={r.pseudo} className={r.pseudo === board.you.pseudo ? "me" : ""}>
                <span className="rank">{r.rank}</span><span className="who">{r.pseudo}</span><b>{r.score}</b>
              </li>
            ))}
            {board.you.rank > board.top.length && (
              <li className="me"><span className="rank">{board.you.rank}</span><span className="who">{board.you.pseudo}</span><b>{board.you.score}</b></li>
            )}
          </ol>)}
    </section>
  );
}

/** On-demand recap of everything the learner has studied (POST /api/study/resume). Used in solo study and the live-room notepad. */
export function RevisionSheet({ reloadKey }) {
  const t = useT();
  const [sheet, setSheet] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const generate = async () => {
    setBusy(true); setError(null);
    try {
      const data = await api("/api/study/resume", { method: "POST" });
      setSheet(data.sheet);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const lines = (sheet || "").split("\n").filter((l) => l.trim());
  return (
    <section className="panel sheet-panel">
      <h3>{t("Revision sheet")}</h3>
      <p className="muted small">{t("A quick recap of everything you've studied, built on demand.")}</p>
      <button className="btn small" onClick={generate} disabled={busy}>
        {busy ? t("Building…") : sheet ? t("Rebuild") : t("Generate revision sheet")}
      </button>
      {error && <ErrorNote error={error} onRetry={generate} />}
      {sheet && (
        <div className="revision-body" data-testid="sheet">
          {lines.map((l, i) => l.startsWith("## ")
            ? <h4 key={i}>{l.slice(3)}</h4>
            : l.startsWith("- ") ? <p key={i} className="revision-pt">• {l.slice(2)}</p>
            : <p key={i}>{l}</p>)}
        </div>
      )}
    </section>
  );
}
