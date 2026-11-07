import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { useT } from "../lib/i18n.jsx";
import { AuthGate, Dots } from "../components/Common.jsx";

function Lobby() {
  const t = useT();
  const nav = useNavigate();
  const [rooms, setRooms] = useState(null);
  const [form, setForm] = useState({ name: "", topic: "", capacity: 6, is_public: true });
  const [code, setCode] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    const load = () => api("/api/rooms?kind=group").then((d) => alive && setRooms(d.rooms)).catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const create = async (e) => {
    e.preventDefault();
    setError("");
    try {
      const room = await api("/api/rooms", { method: "POST", body: form });
      nav(`/rooms/${room.code}`);
    } catch (err) {
      setError(err.message);
    }
  };
  const join = async (e) => {
    e.preventDefault();
    setError("");
    try {
      const room = await api(`/api/rooms/${code.trim().toUpperCase()}`);
      nav(`/rooms/${room.code}`);
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <>
      <div className="page-head">
        <h1>{t("Study rooms")}</h1>
        <p className="muted">{t("Two to ten people, one topic. The explanation and the quiz are shared, so the AI answers once for everyone.")}</p>
      </div>
      <div className="lobby">
        <section>
          <h2 className="section-title">{t("Open rooms")}</h2>
          {rooms === null && <p className="muted">{t("Loading rooms…")}</p>}
          {rooms?.length === 0 && <p className="muted">{t("No open room yet. Create the first one.")}</p>}
          <ul className="room-list">
            {rooms?.map((r) => {
              const full = r.online >= r.capacity;
              return (
                <li key={r.code}>
                  <div>
                    <h3>{r.name}</h3>
                    <p className="muted small">{r.topic || t("No topic yet")}</p>
                  </div>
                  <Dots n={r.capacity} filled={r.online} label={t("{online} of {capacity} people here", { online: r.online, capacity: r.capacity })} />
                  <button className="btn small" disabled={full} onClick={() => nav(`/rooms/${r.code}`)}>{full ? t("Full") : t("Join")}</button>
                </li>
              );
            })}
          </ul>
        </section>
        <section className="side">
          <form className="panel" onSubmit={create}>
            <h2>{t("Create a room")}</h2>
            <label>{t("Room name")}<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={2} maxLength={40} placeholder={t("e.g. Linear algebra, Tuesday")} /></label>
            <label>{t("Topic (optional)")}<input value={form.topic} onChange={(e) => setForm({ ...form, topic: e.target.value })} maxLength={160} placeholder={t("e.g. Eigenvalues")} /></label>
            <label>{t("People allowed: {n}", { n: form.capacity })}
              <input type="range" min="2" max="10" value={form.capacity} onChange={(e) => setForm({ ...form, capacity: +e.target.value })} />
            </label>
            <label className="check"><input type="checkbox" checked={form.is_public} onChange={(e) => setForm({ ...form, is_public: e.target.checked })} /> {t("Show in the open rooms list")}</label>
            <button className="btn primary">{t("Create room")}</button>
          </form>
          <form className="panel" onSubmit={join}>
            <h2>{t("Have a code?")}</h2>
            <label>{t("Room code")}<input value={code} onChange={(e) => setCode(e.target.value)} placeholder={t("e.g. K7M2QX")} maxLength={12} required /></label>
            <button className="btn">{t("Join room")}</button>
          </form>
          {error && <p className="error" role="alert">{error}</p>}
        </section>
      </div>
    </>
  );
}

export default function Rooms() {
  const t = useT();
  return (
    <AuthGate note={t("Rooms show your pseudo to the other people in them.")}>
      <Lobby />
    </AuthGate>
  );
}
