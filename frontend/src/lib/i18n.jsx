import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { setApiLang } from "./api.js";
import fr from "../locales/fr.js";
import ar from "../locales/ar.js";
import darija from "../locales/darija.js";

/** English sentences are the keys. A missing translation shows the English text, never a blank. */
export const LANGS = [
  { code: "en", label: "English", dir: "ltr", html: "en" },
  { code: "fr", label: "Français", dir: "ltr", html: "fr" },
  { code: "ar", label: "العربية", dir: "rtl", html: "ar" },
  { code: "darija", label: "الدارجة التونسية", dir: "rtl", html: "ar-TN" },
];
const DICTS = { fr, ar, darija };
const KEY = "herwiego.lang";

/** Marks a sentence for translation where it cannot be translated yet (module-level data). Translate it with t() when rendering. */
export const tx = (text) => text;

const fill = (text, vars) => (vars ? text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : text);

function initialLang() {
  try {
    const saved = localStorage.getItem(KEY);
    if (LANGS.some((l) => l.code === saved)) return saved;
  } catch {
    /* storage unavailable */
  }
  const nav = (navigator.language || "en").toLowerCase();
  if (nav.startsWith("fr")) return "fr";
  if (nav.startsWith("ar")) return "ar";
  return "en";
}

const LangContext = createContext(null);
export const useLang = () => useContext(LangContext);
export const useT = () => useContext(LangContext).t;

export function LangProvider({ children }) {
  const [lang, setLangState] = useState(initialLang);
  setApiLang(lang); // before children fetch: every request carries the language

  useEffect(() => {
    const meta = LANGS.find((l) => l.code === lang);
    document.documentElement.lang = meta.html;
    document.documentElement.dir = meta.dir;
  }, [lang]);

  const setLang = useCallback((code) => {
    setLangState(code);
    try {
      localStorage.setItem(KEY, code);
    } catch {
      /* the choice just will not survive a reload */
    }
  }, []);

  const value = useMemo(() => {
    const dict = DICTS[lang] || {};
    return { lang, setLang, dir: LANGS.find((l) => l.code === lang).dir, t: (text, vars) => fill(dict[text] ?? text, vars) };
  }, [lang, setLang]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function LangSwitch() {
  const { lang, setLang, t } = useLang();
  return (
    <label className="lang-switch">
      <span className="globe" aria-hidden="true">🌐</span>
      <span className="sr-only">{t("Language")}</span>
      <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t("Language")}>
        {LANGS.map((l) => <option key={l.code} value={l.code} lang={l.html}>{l.label}</option>)}
      </select>
    </label>
  );
}
