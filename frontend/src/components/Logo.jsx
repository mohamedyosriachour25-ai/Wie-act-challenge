/**
 * The HerwieGO mark: an "H" whose crossbar rises into an arrow ("GO"), wearing a graduation cap tilted to the
 * left with an orchid tassel, on the purple gradient tile. Same drawing as the "HerwieGO logo" design canvas.
 * `small` swaps in the simplified cap used at favicon sizes (no cord, a sturdier tassel).
 */
export function LogoMark({ size = 32, label = null, small = false }) {
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 120 120" {...a11y}>
      <defs>
        <linearGradient id="hgMarkBg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8057FF" />
          <stop offset="0.55" stopColor="#6839F5" />
          <stop offset="1" stopColor="#29145E" />
        </linearGradient>
        <linearGradient id="hgMarkPk" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#F5B4F7" />
          <stop offset="1" stopColor="#D946EF" />
        </linearGradient>
      </defs>
      <rect width="120" height="120" rx="30" fill="url(#hgMarkBg)" />
      <g transform="translate(17 28) scale(0.76)">
        <rect x="28" y="26" width="15" height="68" rx="7.5" fill="#FFFFFF" />
        <rect x="77" y="60" width="15" height="34" rx="7.5" fill="#FFFFFF" />
        <path d="M35.5 66 L86 31" fill="none" stroke="url(#hgMarkPk)" strokeWidth="14" strokeLinecap="round" />
        <path d="M70 28 H90 V48" fill="none" stroke="url(#hgMarkPk)" strokeWidth="14" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      <g transform="translate(60 47) rotate(-12)">
        <path d="M-14 -9 V0 Q0 5 14 0 V-9 Z" fill="#E4D8FF" />
        <path d={small ? "M-31 -12 L0 -23 L31 -12 L0 -1 Z" : "M-30 -12 L0 -22 L30 -12 L0 -2 Z"} fill="#FFFFFF" />
      </g>
      {small ? (
        <path d="M28.2 41.5 V52" fill="none" stroke="#E879F9" strokeWidth="5" strokeLinecap="round" />
      ) : (
        <>
          <path d="M57.5 35.3 L28.2 41.5 V51" fill="none" stroke="url(#hgMarkPk)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="28.2" cy="53" r="3" fill="url(#hgMarkPk)" />
          <circle cx="57.5" cy="35.3" r="2.4" fill="url(#hgMarkPk)" />
        </>
      )}
    </svg>
  );
}

/** Mark + wordmark, as in the header. */
export default function Logo({ size = 34 }) {
  return (
    <>
      <LogoMark size={size} />
      <span className="wordmark">Herwie<span className="brand-go">GO</span></span>
    </>
  );
}
