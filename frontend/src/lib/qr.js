import qrcode from "qrcode-generator";

/**
 * QR code as a square matrix of booleans (true = dark module). Pure and DOM-free, so the unit test can
 * rasterise it and decode it again to prove the code is really scannable (scripts/qr-test.mjs).
 * Error-correction level M (about 15%) is a good balance for a short URL shown on a screen.
 */
export function qrMatrix(value, level = "M") {
  const qr = qrcode(0, level); // type 0 = smallest version that fits
  qr.addData(String(value), "Byte");
  qr.make();
  const n = qr.getModuleCount();
  return Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => qr.isDark(r, c)));
}

/** One SVG path for all dark modules, merged into horizontal runs (keeps the markup small). */
export function qrPath(matrix) {
  let d = "";
  matrix.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      if (!row[x]) { x++; continue; }
      let len = 0;
      while (x + len < row.length && row[x + len]) len++;
      d += `M${x} ${y}h${len}v1h-${len}z`;
      x += len;
    }
  });
  return d;
}
