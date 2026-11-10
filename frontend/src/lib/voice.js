/**
 * Voice interaction, entirely in the browser (the spec suggests voice interaction):
 *  - dictation: the Web Speech API turns speech into text in the input box. HerwieGO's server never receives
 *    audio. The BROWSER's speech service may (Chrome sends audio to Google unless on-device recognition is
 *    available, which we request), so it is opt-in and declared before first use.
 *  - read aloud: speechSynthesis with the device's own voices.
 */
const SR = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;

export const canDictate = () => !!SR;
export const canSpeak = () => typeof window !== "undefined" && "speechSynthesis" in window;

const LOCALE = { en: "en-US", fr: "fr-FR", ar: "ar-SA", darija: "ar-TN" };
export const speechLang = (lang) => LOCALE[lang] || "en-US";

const CONSENT = "herwiego.voice.ok";
export const voiceConsented = () => { try { return localStorage.getItem(CONSENT) === "1"; } catch { return false; } };
export const giveVoiceConsent = () => { try { localStorage.setItem(CONSENT, "1"); } catch { /* not kept */ } };

/** Start dictation; returns a stop function. onText(text, final) gets the whole transcript so far. */
export function dictate(lang, { onText, onEnd, onError }) {
  const r = new SR();
  r.lang = speechLang(lang);
  r.interimResults = true;
  r.continuous = false;
  r.maxAlternatives = 1;
  try { if ("processLocally" in r) r.processLocally = true; } catch { /* older browsers */ }
  r.onresult = (e) => {
    let text = "";
    for (const res of e.results) text += res[0].transcript;
    onText(text.trim(), e.results[e.results.length - 1].isFinal);
  };
  r.onerror = (e) => onError?.(e.error);
  r.onend = () => onEnd?.();
  r.start();
  return () => { try { r.stop(); } catch { /* already stopped */ } };
}

/** Plain text for speech: no Markdown marks, no [n] citations. */
export const speakable = (md) => (md || "")
  .replace(/\[\d+\]/g, "")
  .replace(/^#+\s*/gm, "")
  .replace(/\*\*([^*]+)\*\*/g, "$1")
  .replace(/`([^`]+)`/g, "$1")
  .replace(/^\s*[-*•]\s+/gm, "")
  .replace(/\s+/g, " ")
  .trim();

export function speak(text, lang, onEnd) {
  const synth = window.speechSynthesis;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(speakable(text));
  const want = speechLang(lang);
  const voices = synth.getVoices();
  u.voice = voices.find((v) => v.lang === want) || voices.find((v) => v.lang?.startsWith(want.slice(0, 2))) || null;
  u.lang = want;
  u.rate = 1;
  u.onend = () => onEnd?.();
  u.onerror = () => onEnd?.();
  synth.speak(u);
}

export const stopSpeaking = () => { if (canSpeak()) window.speechSynthesis.cancel(); };
