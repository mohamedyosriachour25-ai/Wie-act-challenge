/** Split a label into at most two lines of roughly n characters, for SVG text. */
export const wrap = (text, n) => {
  const lines = [];
  let line = "";
  text.split(" ").forEach((w) => {
    if ((line + " " + w).trim().length > n && line) { lines.push(line); line = w; }
    else line = (line + " " + w).trim();
  });
  if (line) lines.push(line);
  if (lines.length > 2) { lines.length = 2; lines[1] = lines[1].replace(/.{1}$/, "…"); }
  return lines;
};
