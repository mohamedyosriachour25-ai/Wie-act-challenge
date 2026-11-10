import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, uploadMaterial, materialRawUrl } from "../lib/api.js";
import { ErrorNote, Meta } from "./Study.jsx";
import { tx, useT } from "../lib/i18n.jsx";

const STATUS = {
  indexing: tx("Indexing"),
  waiting: tx("Waiting for AI quota"),
  ready: tx("Ready"),
  failed: tx("Failed"),
  reading: tx("Reading scanned pages"),
  file: tx("Kept as a file"),
};

/** Course material for one scope: "me" for the personal library, "space-<id>" for a study space, a room code for a shared one. */
export function MaterialsPanel({ scope, reloadKey = 0, heading, note }) {
  const t = useT();
  const [docs, setDocs] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const input = useRef(null);

  const load = useCallback(async () => {
    try {
      setDocs((await api(`/api/materials?scope=${encodeURIComponent(scope)}`)).documents);
    } catch (e) {
      setError(e);
    }
  }, [scope]);

  useEffect(() => { load(); }, [load, reloadKey]);
  const pending = docs.some((d) => ["indexing", "waiting", "reading"].includes(d.status));
  useEffect(() => {
    if (!pending) return undefined;
    const id = setInterval(load, 1200);
    return () => clearInterval(id);
  }, [pending, load]);

  const add = async (body, filename, name = "") => {
    setBusy(true);
    setError(null);
    try {
      await uploadMaterial(scope, body, filename, name);
      setPasting(false);
      setText("");
      setTitle("");
      await load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    setError(null);
    try {
      await api(`/api/materials/${id}?scope=${encodeURIComponent(scope)}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError(e);
    }
  };

  const [zoom, setZoom] = useState(null);
  useEffect(() => {
    if (!zoom) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setZoom(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom]);
  const images = docs.filter((d) => d.kind === "image");
  const textDocs = docs.filter((d) => d.kind !== "image");
  const shared = scope !== "me" && !scope.startsWith("space-");
  return (
    <section className="panel materials" aria-labelledby={`mat-${scope}`}>
      <h3 id={`mat-${scope}`}>{heading || (shared ? t("Room material") : t("My course material"))}</h3>
      {note && <p className="small space-note">{note}</p>}
      <p className="muted small">
        {shared
          ? t("Everyone in the room can ask questions about these documents, and explanations will cite them.")
          : t("Add notes or a chapter. Explanations and answers will come from them first.")}
        {" "}{t("E-mail addresses and phone numbers are removed before anything is sent to the AI service. Scanned PDFs are read page by page.")}
      </p>

      {docs.length === 0 && <p className="muted small">{t("Nothing here yet. Add a PDF or an image, a .txt or .md file, or paste your notes.")}</p>}
      <ul className="docs">
        {textDocs.map((d) => (
          <li key={d.id}>
            <div className="doc-main">
              <strong>{d.title}</strong>
              <span className={`status ${d.status}`}>{t(STATUS[d.status] || d.status)}</span>
            </div>
            {(d.status === "indexing" || d.status === "waiting") && (
              <progress max={d.chunks_total} value={d.chunks_done} aria-label={t("Indexing {title}", { title: d.title })} />
            )}
            {d.status === "reading" && (
              <progress max={d.chunks_total || 1} value={d.chunks_done} aria-label={t("Reading {title}", { title: d.title })} />
            )}
            <span className="muted small doc-meta">
              {d.status === "ready" ? t(d.chunks_total === 1 ? tx("{n} passage") : tx("{n} passages"), { n: d.chunks_total })
                : d.status === "failed" ? t("Indexing failed. Remove it and add it again.")
                : d.status === "reading" ? (d.chunks_total ? t("Reading scanned page {i} of {n}…", { i: d.chunks_done + 1, n: d.chunks_total }) : t("Preparing the scanned pages…"))
                : d.status === "file" ? t("No text could be read from it. You can open it; paste its text to make it searchable.")
                : t("{done} of {total} passages", { done: d.chunks_done, total: d.chunks_total })}
            </span>
            {d.note?.ocr_pages > 0 && <span className="muted small doc-meta">{t("{n} scanned pages read.", { n: d.note.ocr_pages })}{d.note.ocr_failed ? ` ${t("{n} could not be read.", { n: d.note.ocr_failed })}` : ""}</span>}
            {d.note?.removed > 0 && <span className="muted small doc-meta">{t("{n} personal details removed before sending.", { n: d.note.removed })}</span>}
            {d.has_file && <a className="link small" href={materialRawUrl(d.id, scope)} target="_blank" rel="noopener noreferrer">{t("Open")}</a>}
            <button className="link small doc-remove" onClick={() => remove(d.id)} aria-label={t("Remove {title}", { title: d.title })}>{t("Remove")}</button>
          </li>
        ))}
      </ul>

      {images.length > 0 && (
        <div className="gallery-wrap">
          <p className="muted small">{t("Images are kept as visual references. They are not searched or sent to the AI.")}</p>
          <ul className="gallery">
            {images.map((d) => (
              <li key={d.id}>
                <button className="thumb" onClick={() => setZoom(d)} aria-label={t("View {title}", { title: d.title })}>
                  <img src={materialRawUrl(d.id, scope)} alt={d.title} loading="lazy" />
                </button>
                <button className="link small doc-remove" onClick={() => remove(d.id)} aria-label={t("Remove {title}", { title: d.title })}>{t("Remove")}</button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {zoom && createPortal(
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={zoom.title} onClick={() => setZoom(null)}>
          <img src={materialRawUrl(zoom.id, scope)} alt={zoom.title} />
          <button className="btn small lightbox-close" onClick={() => setZoom(null)}>{t("Close")}</button>
        </div>,
        document.body,   // outside the panel, whose transform would otherwise trap the "fixed" overlay inside it
      )}
      <ErrorNote error={error} />

      {pasting ? (
        <form className="paste" onSubmit={(e) => { e.preventDefault(); add(text, "notes.txt", title); }}>
          <label>{t("Title")}<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={80} placeholder={t("e.g. Chapter 3 notes")} /></label>
          <label>{t("Notes")}<textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} placeholder={t("Paste the text you want to study from")} /></label>
          <div className="row">
            <button className="btn small primary" disabled={busy || text.trim().length < 80}>{busy ? t("Adding") : t("Add notes")}</button>
            <button type="button" className="btn small ghost" onClick={() => setPasting(false)}>{t("Cancel")}</button>
          </div>
          {text.trim().length > 0 && text.trim().length < 80 && <p className="muted small">{t("Add a few more sentences so there is something to search.")}</p>}
        </form>
      ) : (
        <div className="row wrap">
          <input ref={input} type="file" accept=".pdf,.txt,.md,.markdown,image/png,image/jpeg,image/webp,image/gif" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) add(f, f.name); e.target.value = ""; }} />
          <button className="btn small" disabled={busy} onClick={() => input.current?.click()}>{busy ? t("Uploading") : t("Add a file")}</button>
          <button className="btn small ghost" onClick={() => setPasting(true)}>{t("Paste notes")}</button>
        </div>
      )}
    </section>
  );
}

function Cited({ text }) {
  // Turn [1] into a superscript marker that matches the numbered sources below the answer.
  return text.split(/(\[\d+\])/g).map((part, i) => (/^\[\d+\]$/.test(part) ? <sup key={i} className="cite">{part.slice(1, -1)}</sup> : part));
}

export function AnswerCard({ data }) {
  const t = useT();
  if (!data) return null;
  return (
    <article className={`answer${data.grounded ? "" : " nf"}`} aria-live="polite">
      <p className="q">{data.asker ? <><strong>{data.asker}</strong> {t("asked:")} </> : null}{data.question}</p>
      <p className="a">{data.grounded ? <Cited text={data.answer} /> : t("I could not find this in the material. Try different words, or add the page that covers it.")}</p>
      {data.grounded && data.sources.length > 0 && (
        <details>
          <summary>{data.used.length ? t("Sources used ({n})", { n: data.used.length }) : t("Passages searched")}</summary>
          <ol className="sources">
            {data.sources.map((s) => (
              <li key={s.n} className={data.used.includes(s.n) ? "used" : ""}>
                <span className="cite-n">{s.n}</span>
                <span><strong>{s.title}</strong> <span className="muted small">{t("match {n}%", { n: Math.round(s.score * 100) })}</span><br /><span className="small">{s.snippet}</span></span>
              </li>
            ))}
          </ol>
        </details>
      )}
      {!data.grounded && <p className="muted small">{t("Nothing in your documents matches the question closely enough.")}</p>}
      <Meta meta={data.meta} />
    </article>
  );
}

/** Solo version: asks over REST. In a room the same card is fed by the WebSocket instead. */
export function AskMaterial({ scope = "me" }) {
  const t = useT();
  const [q, setQ] = useState("");
  const [answers, setAnswers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (q.trim().length < 3) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api("/api/materials/ask", { method: "POST", body: { scope, question: q } });
      setAnswers((a) => [out, ...a].slice(0, 6));
      setQ("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <h3>{t("Ask your material")}</h3>
      <form className="ask" onSubmit={submit}>
        <label className="grow">
          {t("Question")}
          <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={300} placeholder={t("Type a question about the material")} />
        </label>
        <button className="btn" disabled={busy || q.trim().length < 3}>{busy ? t("Searching") : t("Ask")}</button>
      </form>
      <ErrorNote error={error} />
      {answers.map((a, i) => <AnswerCard key={i} data={a} />)}
    </section>
  );
}
