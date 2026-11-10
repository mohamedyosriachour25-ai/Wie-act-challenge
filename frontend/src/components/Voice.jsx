import { useEffect, useRef, useState } from "react";
import { useLang, useT } from "../lib/i18n.jsx";
import { canDictate, canSpeak, dictate, giveVoiceConsent, speak, stopSpeaking, voiceConsented } from "../lib/voice.js";

/** Microphone button: dictation into an input. Declared and opt-in the first time (see lib/voice.js). */
export function MicButton({ onText, disabled }) {
  const t = useT();
  const { lang } = useLang();
  const [on, setOn] = useState(false);
  const [ask, setAsk] = useState(false);
  const [err, setErr] = useState("");
  const stop = useRef(null);
  useEffect(() => () => stop.current?.(), []);
  if (!canDictate()) return null;
  const start = () => {
    setErr("");
    if (!voiceConsented()) { setAsk(true); return; }
    setOn(true);
    stop.current = dictate(lang, {
      onText: (text) => onText(text),
      onEnd: () => { setOn(false); stop.current = null; },
      onError: (e) => { setOn(false); setErr(e === "not-allowed" ? t("Microphone access was refused.") : t("Dictation stopped. Try again.")); },
    });
  };
  return (
    <span className="mic-wrap">
      <button type="button" className={`btn mic${on ? " on" : ""}`} disabled={disabled} aria-pressed={on}
        aria-label={on ? t("Stop dictation") : t("Dictate")} title={on ? t("Stop dictation") : t("Dictate")}
        onClick={() => (on ? stop.current?.() : start())}>
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-2.1A7 7 0 0 0 19 12h-2Z" /></svg>
      </button>
      {ask && (
        <span className="mic-consent" role="dialog" aria-label={t("Voice dictation")}>
          <span className="small">{t("Your browser turns your speech into text. HerwieGO never receives your voice, but your browser's speech service may (in Chrome, Google's). Use it?")}</span>
          <span className="row">
            <button type="button" className="btn small primary" onClick={() => { giveVoiceConsent(); setAsk(false); start(); }}>{t("Use dictation")}</button>
            <button type="button" className="btn small" onClick={() => setAsk(false)}>{t("Cancel")}</button>
          </span>
        </span>
      )}
      {err && <span className="small mic-err" role="status">{err}</span>}
    </span>
  );
}

/** Read an answer aloud with the device's voices. */
export function ReadAloud({ text }) {
  const t = useT();
  const { lang } = useLang();
  const [on, setOn] = useState(false);
  useEffect(() => () => { if (on) stopSpeaking(); }, [on]);
  if (!canSpeak() || !text) return null;
  return (
    <button type="button" className="link small read-aloud" aria-pressed={on}
      onClick={() => { if (on) { stopSpeaking(); setOn(false); } else { setOn(true); speak(text, lang, () => setOn(false)); } }}>
      {on ? t("Stop reading") : t("Read aloud")}
    </button>
  );
}
