import { Fragment, useEffect, useRef, useState } from "react";
import { tx, useT } from "../lib/i18n.jsx";
import VisualSheet from "../visuals/VisualSheet.jsx";
import { Meta } from "./Study.jsx";
import { ReadAloud } from "./Voice.jsx";

/* ------------------------------------------------------------------ Markdown (safe, no HTML) */
// The tutor writes light Markdown: headings, numbered/bulleted lists, **bold**, `code`, [n] citations.
// Rendered to React elements only; nothing from the model is ever injected as HTML.
const INLINE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d+\])/g;

function inline(text, sources, key) {
  return text.split(INLINE).filter(Boolean).map((part, i) => {
    const k = `${key}-${i}`;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={k}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={k}>{part.slice(1, -1)}</code>;
    const cite = part.match(/^\[(\d+)\]$/);
    if (cite) {
      const s = sources?.find((x) => x.n === Number(cite[1]));
      return <sup key={k} className={`cite${s?.kind === "web" ? " web" : ""}`} title={s ? s.title : undefined}>{cite[1]}</sup>;
    }
    return <Fragment key={k}>{part}</Fragment>;
  });
}

export function Markdown({ text, sources }) {
  const blocks = [];
  let para = [];
  let list = null;
  const endPara = () => { if (para.length) { blocks.push({ kind: "p", text: para.join(" ") }); para = []; } };
  const endList = () => { if (list) { blocks.push(list); list = null; } };
  for (const raw of (text || "").split("\n")) {
    const line = raw.trimEnd();
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const ul = line.match(/^\s*[-*•]\s+(.*)$/);
    if (!line.trim()) { endPara(); endList(); continue; }
    if (h) { endPara(); endList(); blocks.push({ kind: h[1].length === 3 ? "h4" : "h3", text: h[2] }); continue; }
    if (ol || ul) {
      endPara();
      const kind = ol ? "ol" : "ul";
      if (!list || list.kind !== kind) { endList(); list = { kind, items: [] }; }
      list.items.push((ol || ul)[1]);
      continue;
    }
    endList();
    para.push(line.trim());
  }
  endPara(); endList();
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.kind === "h3") return <h3 key={i}>{inline(b.text, sources, i)}</h3>;
        if (b.kind === "h4") return <h4 key={i}>{inline(b.text, sources, i)}</h4>;
        if (b.kind === "p") return <p key={i}>{inline(b.text, sources, i)}</p>;
        const Tag = b.kind;
        return <Tag key={i}>{b.items.map((it, j) => <li key={j}>{inline(it, sources, `${i}-${j}`)}</li>)}</Tag>;
      })}
    </div>
  );
}

/* --------------------------------------------------------------------------- sources */
export function Sources({ sources, used }) {
  const t = useT();
  if (!sources?.length) return null;
  const usedSet = new Set(used || []);
  return (
    <div className="sources">
      <p className="small muted">{t("Sources")}</p>
      <ul>
        {sources.map((s) => (
          <li key={s.n} className={`${s.kind}${usedSet.size && !usedSet.has(s.n) ? " unused" : ""}`}>
            <span className="src-n">{s.n}</span>
            {s.kind === "web" ? (
              <span>
                <span className="src-badge">{t("Wikipedia")}</span>{" "}
                <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a>
              </span>
            ) : (
              <details>
                <summary><span className="src-badge mat">{t("Your material")}</span> {s.title}</summary>
                <p className="small muted">{s.snippet}</p>
              </details>
            )}
          </li>
        ))}
      </ul>
      {sources.some((s) => s.kind === "web") && <p className="small muted web-note">{t("Web extracts come from Wikipedia (CC BY-SA), an external source you can switch off.")}</p>}
    </div>
  );
}

/* ---------------------------------------------------------------------- stage trail */
const STAGES = [
  ["understanding", tx("Understanding your question")],
  ["reading", tx("Reading your material")],
  ["drafting", tx("Quick draft")],
  ["writing", tx("Final answer")],
  ["illustrating", tx("Figure")],
];

/** Live progress of one turn: which stage is running, which are done, and the elapsed time. */
export function StageTrail({ live }) {
  const t = useT();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (live.done) return undefined;
    const id = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(id);
  }, [live.done]);
  const seen = live.stages;
  const current = seen[seen.length - 1];
  const visible = STAGES.filter(([k]) => {
    if (k === "understanding") return !(live.intent && !seen.includes("understanding"));   // skipped when the request is explicit (rooms)
    if (k === "writing") return live.intent?.intent !== "chat";
    if (k === "illustrating") return live.wantsFigure;
    return true;
  });
  const secs = (((live.doneAt || now) - live.startedAt) / 1000).toFixed(1);
  return (
    <ol className="stage-trail" aria-live="polite">
      {visible.map(([k, label]) => {
        const state = !seen.includes(k) ? "todo" : k === current && !live.done ? "now" : "done";
        let extra = "";
        if (k === "reading" && live.sources) {
          const m = live.sources.filter((s) => s.kind === "material").length;
          const w = live.sources.filter((s) => s.kind === "web").length;
          extra = m || w ? ` · ${t("{m} from your material, {w} from the web", { m, w })}` : ` · ${t("nothing found, answering from general knowledge")}`;
        }
        return (
          <li key={k} className={state}>
            <span className="dot" aria-hidden="true" />
            {t(label)}{state === "done" ? extra : ""}
          </li>
        );
      })}
      <li className="clock" dir="ltr">{secs} s</li>
    </ol>
  );
}

/* ---------------------------------------------------------------------- answer card */
/** One tutor answer: the text (draft until the final arrives), its sources, and the figure when it lands. */
export function Answer({ text, isDraft, sources, used, visual, visualFallback, figureNote, wantsFigure, meta, pendingFigure, stepFocus }) {
  const t = useT();
  const [focus, setFocus] = useState("");
  const textRef = useRef(null);
  const links = !isDraft && visual && (stepFocus || []).some(Boolean) ? stepFocus : null;
  // Numbered steps that name a part of the figure become buttons: selecting one highlights that part.
  useEffect(() => {
    const items = textRef.current ? [...textRef.current.querySelectorAll("ol > li")] : [];
    items.forEach((li, i) => {
      const key = links?.[i] || "";
      li.classList.toggle("step-link", Boolean(key));
      li.classList.toggle("on", Boolean(key) && key === focus);
      if (key) { li.tabIndex = 0; li.setAttribute("role", "button"); li.setAttribute("aria-pressed", String(key === focus)); }
      else { li.removeAttribute("tabindex"); li.removeAttribute("role"); li.removeAttribute("aria-pressed"); }
    });
  }, [links, focus, text]);
  const pick = (e) => {
    if (!links || (e.type === "keydown" && e.key !== "Enter" && e.key !== " ")) return;
    const li = e.target.closest("ol > li");
    if (!li || !textRef.current.contains(li)) return;
    const i = [...textRef.current.querySelectorAll("ol > li")].indexOf(li);
    const key = links[i] || "";
    if (!key) return;
    e.preventDefault();
    setFocus((f) => (f === key ? "" : key));
  };
  return (
    <article className={`answer${visual || pendingFigure ? " with-figure" : ""}`}>
      <div className="answer-text">
        {isDraft && <p className="draft-tag small">{t("Quick draft — the tutor is checking and completing it…")}</p>}
        <div className={isDraft ? "draft" : ""} ref={textRef} onClick={pick} onKeyDown={pick}><Markdown text={text} sources={sources} /></div>
        {links && <p className="muted small">{t("Select a numbered step to highlight its part of the figure.")}</p>}
        {!isDraft && <ReadAloud text={text} />}
        <Sources sources={sources} used={used} />
        {figureNote && visual && <p className="muted small figure-note">{t("Why this figure:")} {figureNote}</p>}
        {meta && <Meta meta={meta} />}
      </div>
      {visual ? (
        <VisualSheet visual={visual} fallback={visualFallback} focus={focus} />
      ) : pendingFigure && wantsFigure ? (
        <div className="figure-pending" aria-live="polite">
          <div className="shimmer" />
          <p className="small muted">{t("Drawing the figure…")}</p>
        </div>
      ) : null}
    </article>
  );
}
