import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useAuth } from "../lib/auth.jsx";
import { tx, useT } from "../lib/i18n.jsx";
import { AuthGate } from "../components/Common.jsx";
import { MaterialsPanel } from "../components/Materials.jsx";
import { ErrorNote, ProgressPanel, RevisionSheet, STYLE_NAME } from "../components/Study.jsx";
import { SpaceTutor } from "../components/SpaceTutor.jsx";
import { useStudyClock } from "../lib/studyclock.js";

const SPACE_KEY = "herwiego.space";
const remember = (id) => { try { localStorage.setItem(SPACE_KEY, String(id)); } catch { /* not kept */ } };
const recalled = () => { try { return Number(localStorage.getItem(SPACE_KEY)) || null; } catch { return null; } };

/* ------------------------------------------------------------------ space switcher */
function SpaceBar({ spaces, current, onPick, onChanged, max }) {
  const t = useT();
  const [mode, setMode] = useState(null);      // null | "new" | "rename" | "delete"
  const [name, setName] = useState("");
  const [error, setError] = useState(null);
  const space = spaces.find((s) => s.id === current);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      if (mode === "new") {
        const created = await api("/api/spaces", { method: "POST", body: { name } });
        await onChanged(created.id);
      } else if (mode === "rename") {
        await api(`/api/spaces/${current}`, { method: "PATCH", body: { name } });
        await onChanged(current);
      }
      setMode(null); setName("");
    } catch (err) {
      setError(err);
    }
  };
  const remove = async () => {
    setError(null);
    try {
      await api(`/api/spaces/${current}`, { method: "DELETE" });
      setMode(null);
      await onChanged(null);
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="space-bar">
      <div className="space-tabs" role="tablist" aria-label={t("Study spaces")}>
        {spaces.map((s) => (
          <button key={s.id} role="tab" aria-selected={s.id === current} className={`space-tab${s.id === current ? " on" : ""}`} onClick={() => onPick(s.id)}>
            <span className="space-name">{s.name}</span>
            <span className="space-count small">{t(s.docs === 1 ? tx("{n} doc") : tx("{n} docs"), { n: s.docs })}</span>
          </button>
        ))}
        {spaces.length < max && (
          <button className="space-tab add" onClick={() => { setMode("new"); setName(""); }} aria-label={t("New study space")}>+ {t("New space")}</button>
        )}
      </div>
      {space && mode === null && (
        <div className="space-actions small">
          <button className="link small" onClick={() => { setMode("rename"); setName(space.name); }}>{t("Rename")}</button>
          {!space.primary && <button className="link small danger" onClick={() => setMode("delete")}>{t("Delete space")}</button>}
        </div>
      )}
      {(mode === "new" || mode === "rename") && (
        <form className="space-form" onSubmit={submit}>
          <input autoFocus value={name} maxLength={40} onChange={(e) => setName(e.target.value)} placeholder={t("Name, e.g. Organic chemistry")} aria-label={t("Space name")} />
          <button className="btn small primary" disabled={!name.trim()}>{mode === "new" ? t("Create") : t("Save")}</button>
          <button type="button" className="btn small" onClick={() => setMode(null)}>{t("Cancel")}</button>
        </form>
      )}
      {mode === "delete" && (
        <div className="space-form">
          <span className="small">{t("Delete this space, its material and its conversation?")}</span>
          <button className="btn small danger" onClick={remove}>{t("Delete")}</button>
          <button className="btn small" onClick={() => setMode(null)}>{t("Cancel")}</button>
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

/* ------------------------------------------------------------------------- page */
function SoloSpace() {
  const { user } = useAuth();
  const t = useT();
  useStudyClock("solo");
  const [spaces, setSpaces] = useState([]);
  const [current, setCurrent] = useState(null);
  const [meta, setMeta] = useState({ max: 12, web: true });
  const [useWeb, setUseWeb] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState(null);

  const loadSpaces = useCallback(async (pick) => {
    const data = await api("/api/spaces");
    setSpaces(data.spaces);
    setMeta({ max: data.max, web: data.web_search });
    const wanted = pick ?? recalled();
    const chosen = data.spaces.find((s) => s.id === wanted) || data.spaces[0];
    setCurrent(chosen?.id ?? null);
    if (chosen) remember(chosen.id);
  }, []);

  useEffect(() => { loadSpaces().catch(setError); }, [loadSpaces]);
  const pick = (id) => { setCurrent(id); remember(id); };
  const space = spaces.find((s) => s.id === current);

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
        <ErrorNote error={error} />
        <SpaceBar spaces={spaces} current={current} onPick={pick} max={meta.max} onChanged={(id) => loadSpaces(id).catch(setError)} />
        {space && (
          <SpaceTutor key={space.id} space={space} useWeb={useWeb} setUseWeb={setUseWeb} webAvailable={meta.web}
            onActivity={() => { setRefresh((n) => n + 1); loadSpaces(space.id).catch(() => {}); }} />
        )}
      </div>

      <aside className="tutor-side">
        {space && (
          <MaterialsPanel key={space.id} scope={`space-${space.id}`} reloadKey={refresh}
            heading={t("Material of “{name}”", { name: space.name })} note={t("The tutor reads this material before answering in this space.")} />
        )}
        <ProgressPanel refreshKey={refresh} />
        <RevisionSheet reloadKey={refresh} />
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
