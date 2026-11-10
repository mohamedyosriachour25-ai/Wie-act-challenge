import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useStudyClock } from "../lib/studyclock.js";
import { tx, useT } from "../lib/i18n.jsx";

const GRADES = [["again", tx("Again")], ["hard", tx("Hard")], ["good", tx("Good")], ["easy", tx("Easy")]];

export default function Review() {
  const t = useT();
  useStudyClock("solo");          // revising flashcards is focused study
  const [cards, setCards] = useState(null);
  const [i, setI] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { const r = await api("/api/review/due"); setCards(r.cards); setI(0); setFlipped(false); }
    catch { setCards([]); }
  };
  useEffect(() => { load(); }, []);

  const card = cards?.[i];
  const grade = async (g) => {
    if (!card || busy) return;
    setBusy(true);
    try { await api("/api/review/grade", { method: "POST", body: { id: card.id, grade: g } }); } catch { /* keep going */ }
    setBusy(false);
    setFlipped(false);
    if (i + 1 < cards.length) setI(i + 1); else load();
  };

  return (
    <div className="narrow review">
      <div className="page-head">
        <h1>{t("Revision")}</h1>
        <p className="muted">{t("Cards from your quizzes come back for review just before you would forget them.")}</p>
      </div>
      {cards == null ? (
        <p className="muted">{t("Loading…")}</p>
      ) : cards.length === 0 ? (
        <section className="panel"><p className="muted">{t("Nothing to review right now. Take a quiz and your cards show up here.")}</p></section>
      ) : (
        <section className="panel">
          <p className="muted small">{t("{n} to review", { n: cards.length })} · {card.topic}</p>
          <div className={`flashcard ${flipped ? "flipped" : ""}`} onClick={() => setFlipped(true)} role="button" tabIndex={0}
               onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setFlipped(true); }}>
            <p className="front">{card.front}</p>
            {flipped && <p className="back">{card.back}</p>}
          </div>
          {flipped ? (
            <div className="grades">
              {GRADES.map(([g, label]) => (
                <button key={g} className={`btn small grade-${g}`} disabled={busy} onClick={() => grade(g)}>{t(label)}</button>
              ))}
            </div>
          ) : (
            <button className="btn primary" onClick={() => setFlipped(true)}>{t("Show answer")}</button>
          )}
        </section>
      )}
    </div>
  );
}
