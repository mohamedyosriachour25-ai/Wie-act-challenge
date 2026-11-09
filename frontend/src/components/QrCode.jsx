import { useMemo } from "react";
import { qrMatrix, qrPath } from "../lib/qr.js";

const QUIET = 4; // modules of white margin; scanners need it

/** A scannable QR code drawn as inline SVG. Always dark on white, whatever the page theme. */
export default function QrCode({ value, label, size = 176 }) {
  const matrix = useMemo(() => qrMatrix(value), [value]);
  const n = matrix.length + QUIET * 2;
  return (
    <svg className="qr" viewBox={`0 0 ${n} ${n}`} width={size} height={size} role="img" aria-label={label} data-value={value} shapeRendering="crispEdges">
      <rect width={n} height={n} fill="#fff" />
      <path d={qrPath(matrix)} transform={`translate(${QUIET} ${QUIET})`} fill="#111" />
    </svg>
  );
}
