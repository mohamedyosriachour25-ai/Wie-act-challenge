// Unit tests for the QR helper: the matrix is rasterised and decoded again by an independent decoder (jsQR),
// so we know the code really scans. Run: npm run qr:test
import assert from "node:assert/strict";
import jsQR from "jsqr";
import { qrMatrix, qrPath } from "../src/lib/qr.js";

let n = 0;
const test = (name, fn) => { fn(); n++; console.log("ok  " + name); };

function decode(value, { scale = 6, quiet = 4 } = {}) {
  const m = qrMatrix(value);
  const side = (m.length + quiet * 2) * scale;
  const px = new Uint8ClampedArray(side * side * 4).fill(255);
  m.forEach((row, y) => row.forEach((dark, x) => {
    if (!dark) return;
    for (let dy = 0; dy < scale; dy++) for (let dx = 0; dx < scale; dx++) {
      const i = (((y + quiet) * scale + dy) * side + (x + quiet) * scale + dx) * 4;
      px[i] = px[i + 1] = px[i + 2] = 0;
    }
  }));
  return jsQR(px, side, side)?.data;
}

test("the join links decode back to exactly the same URL", () => {
  for (const url of [
    "http://192.168.1.20:8000/forest/LIVE-FOREST",
    "https://herwiego.example.org/forest/LIVE-DEMO",
    "https://herwiego.example.org/forest/ABCD23?ref=qr&lang=ar",
    "http://localhost:5173/forest/LIVE-FOREST",
  ]) assert.equal(decode(url), url, url);
});
test("the matrix is square, deterministic, and has the three finder squares", () => {
  const a = qrMatrix("http://x.y/forest/LIVE-FOREST"), b = qrMatrix("http://x.y/forest/LIVE-FOREST");
  assert.deepEqual(a, b);
  assert.ok(a.length >= 21 && a.every((r) => r.length === a.length));
  const last = a.length - 1;
  for (const [r, c] of [[0, 0], [0, last], [last, 0]]) assert.equal(a[r][c], true); // finder pattern corners
});
test("different links give different codes", () => {
  assert.notDeepEqual(qrMatrix("http://x.y/forest/A"), qrMatrix("http://x.y/forest/B"));
});
test("the SVG path describes exactly the dark modules", () => {
  const m = qrMatrix("http://x.y/forest/LIVE-FOREST");
  const back = m.map((row) => row.map(() => false));
  for (const [, x, y, len] of qrPath(m).matchAll(/M(\d+) (\d+)h(\d+)v1h-\3z/g)) for (let i = 0; i < +len; i++) back[+y][+x + i] = true;
  assert.deepEqual(back, m);
});
console.log(`\n${n} tests passed`);
