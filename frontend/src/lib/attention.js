/**
 * Camera attention detector — everything here runs on the learner's device.
 *
 * Two parts:
 *  1. `createTracker()` — pure decision logic (no DOM, no model). It turns a stream of per-frame
 *     observations ({ t, face: { yaw, pitch } | null }) into one stable yes/no "attentive" verdict.
 *     Being pure, it is unit-tested in Node (see scripts/attention-test.mjs).
 *  2. `loadDetector()` — lazily loads the on-device face model (Human, models served from /models/).
 *     Nothing is fetched until the learner switches the feature on, and no frame ever leaves the page.
 *
 * Design choices for studying, not surveillance:
 *  - Looking down at notes, a book or the keyboard is normal, so only an extreme head tilt counts.
 *  - A turned head or a missing face only counts after a grace period, so a sneeze or a stretch is fine.
 *  - Coming back is confirmed quickly, so the verdict recovers fast.
 */

export const ATTENTION = {
  yawMaxDeg: 40,        // head turned further than this left/right = looking elsewhere
  pitchMaxDeg: 55,      // head tilted further than this up OR down = away (generous: reading and writing tilt the head)
  turnedAfterMs: 8000,  // head must stay turned this long before "away"
  noFaceAfterMs: 10000, // face must be missing this long before "away"
  backAfterMs: 1500,    // attentive again after this long facing the screen
  sampleMs: 500,        // ~2 analyses per second — light enough for low-end laptops
  minFaceScore: 0.5,    // ignore weak detections
};

const RAD = 180 / Math.PI;

/** Classify one observation. `face` angles are in degrees. */
export function classify(face, cfg = ATTENTION) {
  if (!face) return { ok: false, reason: "no_face" };
  if (Math.abs(face.yaw) > cfg.yawMaxDeg) return { ok: false, reason: "turned" };
  if (Math.abs(face.pitch) > cfg.pitchMaxDeg) return { ok: false, reason: "turned" };
  return { ok: true, reason: "ok" };
}

/**
 * Stateful smoothing with hysteresis. `update(obs)` returns the committed verdict:
 *   { attentive: null | true | false, reason: "starting" | "ok" | "no_face" | "turned" }
 * `attentive` stays null until the first verdict is confirmed, so the UI never flashes a false alarm.
 */
export function createTracker(cfg = ATTENTION) {
  let committed = { attentive: null, reason: "starting" };
  let cand = null; // { ok, reason, since }
  return {
    update(obs) {
      const c = classify(obs.face, cfg);
      if (!cand || cand.ok !== c.ok) cand = { ok: c.ok, reason: c.reason, since: obs.t };
      else cand.reason = c.reason;
      const need = cand.ok ? cfg.backAfterMs : cand.reason === "no_face" ? cfg.noFaceAfterMs : cfg.turnedAfterMs;
      if (obs.t - cand.since >= need) committed = { attentive: cand.ok, reason: cand.reason };
      return committed;
    },
    /** Forget the pending candidate (e.g. after the tab was hidden) but keep the last verdict. */
    pause() {
      cand = null;
    },
    reset() {
      committed = { attentive: null, reason: "starting" };
      cand = null;
    },
    get verdict() {
      return committed;
    },
  };
}

/** Pick the best face from a Human result and convert its head angles to degrees. */
export function faceFromResult(result, cfg = ATTENTION) {
  const faces = (result?.face || []).filter((f) => (f.score ?? f.boxScore ?? 0) >= cfg.minFaceScore);
  if (!faces.length) return null;
  const f = faces.reduce((a, b) => ((b.size?.[0] || 0) * (b.size?.[1] || 0) > (a.size?.[0] || 0) * (a.size?.[1] || 0) ? b : a));
  const ang = f.rotation?.angle;
  if (!ang) return { yaw: 0, pitch: 0 }; // face seen but no pose: treat as facing the screen
  return { yaw: ang.yaw * RAD, pitch: ang.pitch * RAD };
}

/**
 * Lazily load the face model. Only face detection + mesh (for head pose) are enabled; every other
 * Human module is off. Models live in /models (about 2 MB) and are served by this app — no third-party
 * download. Resolves to `{ analyse(videoEl) -> face|null, backend }`.
 */
export async function loadDetector() {
  const { Human } = await import("@vladmandic/human");
  const base = `${import.meta.env.BASE_URL || "/"}models/`;
  const human = new Human({
    modelBasePath: base,
    backend: "webgl",
    debug: false,
    warmup: "none",
    filter: { enabled: false },
    face: {
      enabled: true,
      detector: { rotation: true, maxDetected: 1, minConfidence: 0.5 },
      mesh: { enabled: true },
      iris: { enabled: false },
      attention: { enabled: false },
      description: { enabled: false },
      emotion: { enabled: false },
      antispoof: { enabled: false },
      liveness: { enabled: false },
      gear: { enabled: false },
    },
    body: { enabled: false },
    hand: { enabled: false },
    object: { enabled: false },
    gesture: { enabled: false },
    segmentation: { enabled: false },
  });
  try {
    await human.load();
  } catch {
    human.config.backend = "cpu"; // no WebGL: slower but works
    await human.load();
  }
  return {
    backend: human.tf?.getBackend?.() || human.config.backend,
    async analyse(video) {
      const res = await human.detect(video);
      return faceFromResult(res);
    },
    dispose() {
      try {
        human.tf?.disposeVariables?.();
      } catch {
        /* nothing to free */
      }
    },
  };
}
