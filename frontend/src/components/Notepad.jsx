import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { tx, useT } from "../lib/i18n.jsx";
import { AskMaterial, MaterialsPanel } from "./Materials.jsx";
import { ErrorNote, RevisionSheet } from "./Study.jsx";

const TABS = [
  { id: "ask", label: tx("Assistant") },
  { id: "rooms", label: tx("My rooms") },
  { id: "sheet", label: tx("Revision") },
  { id: "notes", label: tx("Notes") },
];

/** Private scratch notes, saved in this browser only (per pseudo). Wrapped in try/catch: storage can be
 * unavailable (private mode, blocked), and the notes then simply live for this visit. */
function Notes() {
  const t = useT();
  const { user } = useAuth();
  const key = `herwiego.notes.${user?.pseudo || "anon"}`;
  const [text, setText] = useState(() => { try { return localStorage.getItem(key) || ""; } catch { return ""; } });
  const [saved, setSaved] = useState(true);
  useEffect(() => {
    setSaved(false);
    const id = setTimeout(() => { try { localStorage.setItem(key, text); } catch { /* storage off */ } setSaved(true); }, 400);
    return () => clearTimeout(id);
  }, [key, text]);
  return (
    <div className="notes-tab">
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12} maxLength={20000}
                aria-label={t("My notes")} placeholder={t("Jot down formulas, questions for later, things to remember…")} />
      <p className="muted small">{saved ? t("Saved on this device.") : t("Saving…")}</p>
    </div>
  );
}

/** The study rooms I created or spoke in, with their saved summary and latest AI explanations. */
function MyRooms() {
  const t = useT();
  const [rooms, setRooms] = useState(null);
  const [open, setOpen] = useState(null);       // code of the room being read
  const [convo, setConvo] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => { api("/api/rooms/mine").then((d) => setRooms(d.rooms)).catch(setError); }, []);
  useEffect(() => {
    if (!open) return;
    setConvo(null);
    api(`/api/rooms/${encodeURIComponent(open)}/conversation`).then(setConvo).catch(setError);
  }, [open]);

  if (error) return <ErrorNote error={error} />;
  if (rooms === null) return <p className="muted small">{t("Loading…")}</p>;
  if (rooms.length === 0) {
    return <p className="muted small">{t("No study rooms yet. Rooms you create or talk in show up here.")} <Link to="/rooms">{t("Open study rooms")}</Link></p>;
  }
  if (open) {
    const room = rooms.find((r) => r.code === open);
    const notes = (convo?.history || []).filter((m) => m.kind === "explanation" || m.kind === "answer").slice(-6);
    return (
      <div className="room-read">
        <button className="link small" onClick={() => setOpen(null)}>{t("← All my rooms")}</button>
        <h4>{room?.name}</h4>
        {room?.topic && <p className="muted small">{room.topic}</p>}
        {!convo ? <p className="muted small">{t("Loading…")}</p> : (
          <>
            {convo.summary?.text
              ? <div className="session-summary"><strong className="small">{t("Summary")}</strong><p className="small">{convo.summary.text}</p></div>
              : <p className="muted small">{t("No summary yet. Use Summarise in the room to make one.")}</p>}
            {notes.length > 0 && (
              <ul className="room-notes">
                {notes.map((m) => <li key={m.id} className="small"><span className="tag">{m.kind === "answer" ? t("Answer") : t("Explanation")}</span>{m.text}</li>)}
              </ul>
            )}
          </>
        )}
        <Link className="btn small" to={`/rooms/${open}`}>{t("Open the room")}</Link>
      </div>
    );
  }
  return (
    <ul className="my-rooms">
      {rooms.map((r) => (
        <li key={r.code}>
          <button className="my-room" onClick={() => setOpen(r.code)}>
            <strong>{r.name}</strong>
            <span className="muted small">{r.topic || (r.summary ? r.summary.slice(0, 80) + (r.summary.length > 80 ? "…" : "") : t("No topic yet"))}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** The live room's study notepad: everything needed to keep working without leaving the room. */
export default function Notepad() {
  const t = useT();
  const [tab, setTab] = useState("ask");
  return (
    <section className="panel notepad" aria-label={t("Study notepad")}>
      <div className="notepad-head">
        <h3>{t("Study notepad")}</h3>
        <div className="tabs" role="tablist" aria-label={t("Study notepad")}>
          {TABS.map((x) => (
            <button key={x.id} role="tab" id={`np-${x.id}`} aria-selected={tab === x.id} aria-controls={`np-panel-${x.id}`}
                    className={`tab ${tab === x.id ? "on" : ""}`} onClick={() => setTab(x.id)}>{t(x.label)}</button>
          ))}
        </div>
      </div>
      {/* every tab stays mounted (hidden when inactive) so answers and notes survive switching tabs */}
      <div className="notepad-body" role="tabpanel" id="np-panel-ask" aria-labelledby="np-ask" hidden={tab !== "ask"}>
        <p className="muted small">{t("Ask about your own course material. Answers cite the passages they come from.")}</p>
        <AskMaterial scope="me" />
        <details className="np-materials"><summary className="small">{t("My course material")}</summary><MaterialsPanel scope="me" /></details>
      </div>
      <div className="notepad-body" role="tabpanel" id="np-panel-rooms" aria-labelledby="np-rooms" hidden={tab !== "rooms"}><MyRooms /></div>
      <div className="notepad-body" role="tabpanel" id="np-panel-sheet" aria-labelledby="np-sheet" hidden={tab !== "sheet"}><RevisionSheet /></div>
      <div className="notepad-body" role="tabpanel" id="np-panel-notes" aria-labelledby="np-notes" hidden={tab !== "notes"}><Notes /></div>
    </section>
  );
}
