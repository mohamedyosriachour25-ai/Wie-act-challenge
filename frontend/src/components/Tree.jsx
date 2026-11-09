/**
 * A tree that grows with `p` (0..1). It is one drawing of a full-grown tree scaled up from the ground, so a
 * progress update just animates the scale (CSS transition). States: growing | grown | wilted.
 */
const lerp = (a, b, t) => Math.round(a + (b - a) * t);
const mix = (from, to, t) => `rgb(${lerp(from[0], to[0], t)}, ${lerp(from[1], to[1], t)}, ${lerp(from[2], to[2], t)})`;

export default function Tree({ p = 0, state = "growing", label, className = "" }) {
  const s = Math.max(0, Math.min(1, p));
  const wilted = state === "wilted";
  const ease = 1 - (1 - s) * (1 - s);
  const k = 0.2 + 0.8 * ease;
  const leaf = wilted ? "#b9a46a" : mix([143, 214, 172], [30, 127, 89], ease);
  const leaf2 = wilted ? "#a8935a" : mix([120, 200, 152], [24, 104, 73], ease);
  const trunk = wilted ? "#7d6444" : "#7a5230";
  const sag = wilted ? 7 : 0; // a wilted crown droops
  return (
    <svg className={`tree ${state} ${className}`} viewBox="0 0 100 120" role="img" aria-label={label} data-state={state} data-progress={s.toFixed(3)}>
      <ellipse cx="50" cy="111" rx="34" ry="6" fill="#cfd9c9" />
      {s < 0.05 && state === "growing" ? (
        <g>
          <ellipse cx="50" cy="108" rx="10" ry="4" fill="#8a6a43" />
          <path d="M50 108 q-1 -8 4 -11" stroke="#4fae7c" strokeWidth="2.5" fill="none" strokeLinecap="round" />
        </g>
      ) : (
        <g className="tree-body" style={{ transform: `scale(${k})`, transformOrigin: "50px 110px" }}>
          <rect x="46" y={62 + sag} width="8" height={48 - sag} rx="3" fill={trunk} />
          <circle cx="50" cy={48 + sag} r="26" fill={leaf} />
          <circle cx="33" cy={62 + sag} r="18" fill={leaf2} />
          <circle cx="67" cy={62 + sag} r="18" fill={leaf2} />
          {!wilted && <circle cx="43" cy="40" r="6" fill="#fff" opacity="0.28" />}
        </g>
      )}
      {wilted && (
        <g fill="#a8935a">
          <ellipse cx="30" cy="109" rx="4" ry="1.6" transform="rotate(-18 30 109)" />
          <ellipse cx="68" cy="110" rx="4" ry="1.6" transform="rotate(14 68 110)" />
          <ellipse cx="55" cy="112" rx="3.4" ry="1.4" />
        </g>
      )}
    </svg>
  );
}
