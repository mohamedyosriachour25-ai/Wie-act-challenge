import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { LANGS, useLang, useT } from "../lib/i18n.jsx";
import { AuthGate } from "../components/Common.jsx";
import { ErrorNote } from "../components/Study.jsx";

function useDate() {
  const { lang } = useLang();
  const html = LANGS.find((l) => l.code === lang)?.html || "en";
  const f = new Intl.DateTimeFormat(html, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  return (ts) => (ts ? f.format(new Date(ts * 1000)) : "—");
}

function RoomReport({ code }) {
  const t = useT();
  const when = useDate();
  const [r, setR] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { setR(null); api(`/api/teacher/rooms/${code}`).then(setR).catch(setError); }, [code]);
  if (error) return <ErrorNote error={error} />;
  if (!r) return <p className="muted">{t("Loading…")}</p>;
  const max = Math.max(1, ...r.learners.map((l) => l.messages));
  return (
    <div className="teacher-report">
      <div className="t-stats">
        {[["learners", t("Learners")], ["messages", t("Messages")], ["anonymous_messages", t("Anonymous")], ["questions", t("Questions to the material")],
          ["explanations", t("Explanations")], ["competitions", t("Competitions")]].map(([k, label]) => (
          <div key={k} className="t-stat"><b>{r.totals[k]}</b><span className="small muted">{label}</span></div>
        ))}
      </div>

      <section className="panel">
        <h3>{t("Participation")}</h3>
        {r.balance.dominated && <p className="banner wait">{t("One learner writes {p}% of the conversation. Invite the others in, or start a competition so everyone answers.", { p: Math.round(r.balance.top_share * 100) })}</p>}
        {r.balance.quiet.length > 0 && <p className="small">{t("Joined but never wrote:")} <strong>{r.balance.quiet.join(", ")}</strong></p>}
        <table className="t-table">
          <thead><tr><th>{t("Learner")}</th><th>{t("Messages")}</th><th>{t("Questions")}</th><th>{t("Competition")}</th><th>{t("Last seen")}</th></tr></thead>
          <tbody>
            {r.learners.map((l) => (
              <tr key={l.pseudo}>
                <td>{l.pseudo}</td>
                <td><span className="t-bar"><i style={{ width: `${(l.messages / max) * 100}%` }} /></span> {l.messages}</td>
                <td>{l.questions_asked}</td>
                <td>{l.competition ? t("{p} pts · {c} right", { p: l.competition.points, c: l.competition.correct }) : "—"}</td>
                <td className="small muted">{when(l.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small muted">{t("Anonymous messages are counted for the room, never attributed to anyone.")}</p>
      </section>

      <div className="t-grid">
        <section className="panel">
          <h3>{t("What to revisit")}</h3>
          {r.hardest.length === 0 ? <p className="muted small">{t("Run a competition to see which questions the group finds hard.")}</p> : (
            <ol className="t-hard">
              {r.hardest.map((q, i) => (
                <li key={i}><span className={`t-rate${q.rate < 0.5 ? " low" : ""}`}>{Math.round(q.rate * 100)}%</span> {q.q}
                  <span className="small muted"> · {q.topic} · {t("{a} answered", { a: q.answered })}</span></li>
              ))}
            </ol>
          )}
        </section>
        <section className="panel">
          <h3>{t("Questions asked")}</h3>
          {r.questions.length === 0 ? <p className="muted small">{t("No questions yet.")}</p> : (
            <ul className="t-list">{[...r.questions].reverse().map((q, i) => <li key={i}><strong>{q.by}</strong>: {q.text} <span className="small muted">{when(q.at)}</span></li>)}</ul>
          )}
          <h3>{t("Topics studied")}</h3>
          <p className="small">{r.topics.length ? [...r.topics].reverse().map((x) => x.topic).join(" · ") : "—"}</p>
        </section>
      </div>

      {r.competitions.length > 0 && (
        <section className="panel">
          <h3>{t("Competitions")}</h3>
          <ul className="t-list">
            {r.competitions.map((c, i) => (
              <li key={i}><strong>{c.topic}</strong> <span className="small muted">{when(c.at)} · {t("{n} players", { n: c.players })}</span> — {c.podium.map((p) => `${p.rank}. ${p.pseudo} (${p.points})`).join("  ")}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Dashboard() {
  const t = useT();
  const when = useDate();
  const [rooms, setRooms] = useState(null);
  const [open, setOpen] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api("/api/teacher/rooms").then((d) => { setRooms(d.rooms); if (d.rooms[0]) setOpen(d.rooms[0].code); }).catch(setError);
  }, []);
  return (
    <div className="teacher">
      <div className="page-head">
        <h1>{t("Teacher dashboard")}</h1>
        <p className="muted">{t("For the study rooms you created: who takes part, who stays quiet, and what the group finds hard. Pseudonyms only; nothing here calls the AI.")}</p>
      </div>
      <ErrorNote error={error} />
      {rooms && rooms.length === 0 && (
        <div className="panel"><p>{t("You have not created a study room yet.")}</p><Link className="btn primary" to="/rooms">{t("Create a study room")}</Link></div>
      )}
      {rooms && rooms.length > 0 && (
        <>
          <div className="space-tabs" role="tablist" aria-label={t("Your rooms")}>
            {rooms.map((r) => (
              <button key={r.code} role="tab" aria-selected={open === r.code} className={`space-tab${open === r.code ? " on" : ""}`} onClick={() => setOpen(r.code)}>
                <span className="space-name">{r.name}</span>
                <span className="space-count small">{t("{n} learners", { n: r.learners })} · {when(r.last_activity)}</span>
              </button>
            ))}
          </div>
          {open && <RoomReport key={open} code={open} />}
        </>
      )}
    </div>
  );
}

export default function Teacher() {
  const t = useT();
  return (
    <AuthGate note={t("Sign in to see the rooms you created.")}>
      <Dashboard />
    </AuthGate>
  );
}
