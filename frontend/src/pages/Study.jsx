import { useState } from "react";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { tx, useT } from "../lib/i18n.jsx";
import { AuthGate, StageLoader } from "../components/Common.jsx";
import { AskMaterial, MaterialsPanel } from "../components/Materials.jsx";
import { Diagnostic, ErrorNote, Explanation, HintLadder, ProgressPanel, SoloQuiz, STYLE_NAME } from "../components/Study.jsx";

const EXAMPLES = [tx("Thales theorem"), tx("Quadratic functions"), tx("Photosynthesis"), tx("Causes of the French Revolution")];

function SoloSpace() {
  const { user, setStyle } = useAuth();
  const t = useT();
  const [skipped, setSkipped] = useState(false);
  const [retake, setRetake] = useState(false);
  const [topic, setTopic] = useState("");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [refresh, setRefresh] = useState(0);

  const ask = async (t = topic) => {
    if (t.trim().length < 2) return;
    setTopic(t);
    setBusy(true);
    setError(null);
    try {
      setData(await api("/api/study/explain", { method: "POST", body: { topic: t } }));
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  // Course material sits under the examples until there is an explanation, then moves below it so the figure stays in view.
  const material = (
    <div className="two">
      <MaterialsPanel scope="me" reloadKey={refresh} />
      <AskMaterial scope="me" />
    </div>
  );

  if ((!user.learning_style && !skipped) || retake) {
    return (
      <div className="narrow">
        <Diagnostic onDone={(style) => { setStyle(style); setRetake(false); }} />
        {!retake && <button className="link" onClick={() => setSkipped(true)}>{t("Skip for now")}</button>}
      </div>
    );
  }

  return (
    <>
      <div className="page-head">
        <h1>{t("Solo study")}</h1>
        <p className="muted">
          {user.learning_style ? <>{t("Explaining in your style:")} <strong>{t(STYLE_NAME[user.learning_style] || STYLE_NAME.balanced)}</strong>. <button className="link" onClick={() => setRetake(true)}>{t("Retake the questions")}</button></> : t("Explaining in a balanced style.")}
        </p>
      </div>

      <form className="ask" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <label className="grow">
          {t("What do you want to understand?")}
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder={t("e.g. Thales theorem, how photosynthesis works")} maxLength={160} />
        </label>
        <button className="btn primary" disabled={busy || topic.trim().length < 2}>{busy ? t("Working") : t("Explain it")}</button>
      </form>
      <div className="row wrap examples">
        <span className="muted small">{t("Try:")}</span>
        {EXAMPLES.map((e) => <button key={e} className="chip" disabled={busy} onClick={() => ask(t(e))}>{t(e)}</button>)}
      </div>

      {!data && material}

      {busy && <StageLoader stages={[t("Designing the illustration"), t("Writing the explanation"), t("Checking the figure")]} />}
      <ErrorNote error={error} onRetry={() => ask()} />

      {data && !busy && (
        <>
          <Explanation data={data} />
          <div className="two">
            <HintLadder topic={data.topic} />
            <SoloQuiz topic={data.topic} level={data.level} onFinish={() => setRefresh((n) => n + 1)} />
          </div>
        </>
      )}
      {data && !busy && material}
      <ProgressPanel refreshKey={refresh} />
    </>
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
