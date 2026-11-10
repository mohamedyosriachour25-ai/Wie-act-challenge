import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { tx, useT } from "../lib/i18n.jsx";
import { AuthGate } from "../components/Common.jsx";
import { AskMaterial, MaterialsPanel } from "../components/Materials.jsx";
import { ErrorNote, Explanation, HintLadder, ProgressPanel, RevisionSheet, SoloQuiz, STYLE_NAME } from "../components/Study.jsx";

const THINK = [tx("Designing the illustration"), tx("Writing the explanation"), tx("Checking the figure")];

function Thinking() {
  const t = useT();
  const [i, setI] = useState(0);
  useEffect(() => { const id = setInterval(() => setI((n) => (n + 1) % THINK.length), 1400); return () => clearInterval(id); }, []);
  return (
    <div className="thinking" aria-live="polite">
      <span className="typing"><i /><i /><i /></span>
      <span className="muted small">{t(THINK[i])}…</span>
    </div>
  );
}

function SoloSpace() {
  const { user } = useAuth();
  const t = useT();
  const [topic, setTopic] = useState("");
  const [turns, setTurns] = useState([]);   // { topic, data }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns, busy]);

  const ask = async (q = topic) => {
    const asked = q.trim();
    if (asked.length < 2 || busy) return;
    setError(null); setBusy(true); setTopic("");
    setTurns((ts) => [...ts, { topic: asked, data: null }]);
    try {
      const data = await api("/api/study/explain", { method: "POST", body: { topic: asked } });
      setTurns((ts) => ts.map((x, i) => (i === ts.length - 1 ? { ...x, data } : x)));
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const started = turns.length > 0;
  return (
    <div className="tutor">
      <div className="tutor-main">
        <div className="page-head">
          <h1>{t("Solo study")}</h1>
          <p className="muted">
            {user.learning_style
              ? <>{t("Explaining in your style:")} <strong>{t(STYLE_NAME[user.learning_style] || STYLE_NAME.balanced)}</strong>.</>
              : t("Explaining in a balanced style.")}
          </p>
        </div>

        <div className="thread">
          {!started && (
            <div className="welcome msg-in">
              <h2>{t("Ask your tutor anything")}</h2>
              <p className="muted">{t("Every answer comes with a figure you can drag and explore, hints when you're stuck, and a quick quiz to check yourself.")}</p>
            </div>
          )}

          {turns.map((turn, i) => (
            <div className="exchange" key={i}>
              <div className="msg-row you"><div className="bubble msg-in">{turn.topic}</div></div>
              {turn.data ? (
                <div className="msg-row tutor">
                  <div className="avatar-sm" aria-hidden="true">H</div>
                  <div className="bubble tutor-card msg-in">
                    <Explanation data={turn.data} />
                    {i === turns.length - 1 && (
                      <div className="turn-tools">
                        <HintLadder topic={turn.data.topic} />
                        <SoloQuiz topic={turn.data.topic} level={turn.data.level} onFinish={() => setRefresh((n) => n + 1)} />
                      </div>
                    )}
                  </div>
                </div>
              ) : busy && i === turns.length - 1 ? (
                <div className="msg-row tutor">
                  <div className="avatar-sm" aria-hidden="true">H</div>
                  <div className="bubble tutor-card msg-in"><Thinking /></div>
                </div>
              ) : null}
            </div>
          ))}

          {error && <ErrorNote error={error} onRetry={() => ask(turns[turns.length - 1]?.topic || "")} />}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <input
            aria-label={t("What do you want to understand?")}
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder={started ? t("Ask a follow-up, or a new topic") : t("Type the topic you want explained")}
            maxLength={160}
          />
          <button className="btn primary send" disabled={busy || topic.trim().length < 2}>{busy ? t("Working") : started ? t("Ask") : t("Explain it")}</button>
        </form>
      </div>

      <aside className="tutor-side">
        <ProgressPanel refreshKey={refresh} />
        <RevisionSheet reloadKey={refresh} />
        <MaterialsPanel scope="me" reloadKey={refresh} />
        <AskMaterial scope="me" />
      </aside>
    </div>
  );
}

export default function Study() {
  const t = useT();
  return (
    <AuthGate note={t("Your pseudo keeps your progress between sessions.")}>
      <SoloSpace />
    </AuthGate>
  );
}
