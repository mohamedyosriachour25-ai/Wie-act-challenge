// Unit tests for the pure attention logic (no browser, no model). Run: npm run attention:test
import assert from "node:assert/strict";
import { ATTENTION as A, classify, createTracker, faceFromResult } from "../src/lib/attention.js";

const face = (yaw = 0, pitch = 0) => ({ yaw, pitch });
const feed = (tr, from, to, f) => { let v; for (let t = from; t <= to; t += A.sampleMs) v = tr.update({ t, face: f }); return v; };
let n = 0;
const test = (name, fn) => { fn(); n++; console.log("ok  " + name); };

test("classify: facing the screen, small tilts and looking down are fine", () => {
  assert.equal(classify(face(0, 0)).ok, true);
  assert.equal(classify(face(25, -30)).ok, true);   // reading a book: head tilted
  assert.equal(classify(face(-30, 30)).ok, true);
});
test("classify: strongly turned or no face is not ok", () => {
  assert.deepEqual(classify(face(60, 0)), { ok: false, reason: "turned" });
  assert.deepEqual(classify(face(-45, 0)), { ok: false, reason: "turned" });
  assert.deepEqual(classify(face(0, 70)), { ok: false, reason: "turned" });
  assert.deepEqual(classify(null), { ok: false, reason: "no_face" });
});
test("tracker: no verdict until confirmed, then attentive", () => {
  const tr = createTracker();
  assert.equal(tr.update({ t: 0, face: face() }).attentive, null);
  assert.equal(tr.update({ t: A.backAfterMs - 1, face: face() }).attentive, null);
  assert.equal(tr.update({ t: A.backAfterMs, face: face() }).attentive, true);
});
test("tracker: a short look away or a missing face does not flip the verdict", () => {
  const tr = createTracker();
  feed(tr, 0, 3000, face());
  assert.equal(feed(tr, 3500, 3500 + A.turnedAfterMs - 1000, face(70)).attentive, true);
  feed(tr, 20000, 21000, face());
  assert.equal(feed(tr, 22000, 22000 + A.noFaceAfterMs - 1000, null).attentive, true);
});
test("tracker: sustained turn -> away; sustained missing face -> away (longer grace)", () => {
  const a = createTracker(); feed(a, 0, 3000, face());
  assert.deepEqual(feed(a, 4000, 4000 + A.turnedAfterMs + 500, face(70)), { attentive: false, reason: "turned" });
  const b = createTracker(); feed(b, 0, 3000, face());
  assert.deepEqual(feed(b, 4000, 4000 + A.noFaceAfterMs + 500, null), { attentive: false, reason: "no_face" });
});
test("tracker: recovers quickly once the learner is back", () => {
  const tr = createTracker(); feed(tr, 0, 3000, face()); feed(tr, 4000, 20000, null);
  assert.equal(tr.verdict.attentive, false);
  assert.equal(feed(tr, 21000, 21000 + A.backAfterMs, face()).attentive, true);
});
test("tracker: glitchy frames reset the pending 'away' timer", () => {
  const tr = createTracker(); feed(tr, 0, 3000, face());
  feed(tr, 4000, 9000, null); feed(tr, 9500, 10000, face());          // flicker back
  assert.equal(feed(tr, 10500, 10500 + A.noFaceAfterMs - 1000, null).attentive, true);
});
test("tracker: pause() drops the pending candidate but keeps the last verdict", () => {
  const tr = createTracker(); feed(tr, 0, 3000, face());
  feed(tr, 4000, 12000, null); tr.pause();
  assert.equal(tr.verdict.attentive, true);
  assert.equal(feed(tr, 100000, 100000 + 1000, null).attentive, true);
});
test("faceFromResult: converts radians to degrees, picks the largest face, ignores weak ones", () => {
  const r = { face: [
    { score: 0.9, size: [50, 50], rotation: { angle: { yaw: 0, pitch: 0 } } },
    { score: 0.95, size: [120, 120], rotation: { angle: { yaw: Math.PI / 4, pitch: -Math.PI / 6 } } },
    { score: 0.2, size: [300, 300], rotation: { angle: { yaw: 3, pitch: 3 } } },
  ] };
  const f = faceFromResult(r);
  assert.ok(Math.abs(f.yaw - 45) < 1e-9 && Math.abs(f.pitch + 30) < 1e-9);
  assert.equal(faceFromResult({ face: [] }), null);
  assert.equal(faceFromResult(null), null);
  assert.deepEqual(faceFromResult({ face: [{ score: 0.9, size: [10, 10] }] }), { yaw: 0, pitch: 0 });
});
console.log(`\n${n} tests passed`);
