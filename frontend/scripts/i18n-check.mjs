// Lists every sentence the interface shows through t()/tr()/tx() and checks the three dictionaries.
// Usage: node scripts/i18n-check.mjs          (exit code 1 when something is missing)
//        node scripts/i18n-check.mjs --list   (print the English keys)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const root = new URL("../src/", import.meta.url).pathname;
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) { if (name !== "locales") walk(p); }
    else if (/\.(jsx?|mjs)$/.test(name)) files.push(p);
  }
})(root);

const keys = new Map();
const problems = [];
const call = /(?<![\w.$])(?:t|tr|tx)\(\s*(["'`])((?:\\.|(?!\1)[^\\])*)\1/g;
for (const f of files) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(call)) {
    if (m[1] === "`" && m[2].includes("${")) { problems.push(`${f}: template literal inside t(): ${m[2].slice(0, 60)}`); continue; }
    keys.set(m[2].replace(/\\(["'])/g, "$1"), f.replace(root, ""));
  }
}
// strings used through variables, such as t(item.q), are declared with tx() where they are defined, so they are in `keys` already.

if (process.argv.includes("--list")) { console.log([...keys.keys()].join("\n")); process.exit(0); }

const holders = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
const dicts = {};
for (const lang of ["fr", "ar", "darija"]) dicts[lang] = (await import(pathToFileURL(join(root, "locales", `${lang}.js`)))).default;
for (const [lang, dict] of Object.entries(dicts)) {
  for (const [en, from] of keys) {
    const tr = dict[en];
    if (!tr) { problems.push(`[${lang}] missing: "${en}"  (${from})`); continue; }
    if (holders(en) !== holders(tr)) problems.push(`[${lang}] placeholders differ: "${en}"`);
    if (lang !== "fr" && !/[؀-ۿ]/.test(tr) && /[A-Za-z]{4,}/.test(en) && !/^[\d\s×%{}\w.,:;!?()'-]*$/.test(tr)) problems.push(`[${lang}] no Arabic script: "${en}"`);
    if (lang !== "fr" && !/[؀-ۿ]/.test(tr) && /[A-Za-z]{4,}/.test(en) && /^[\d\s×%{}\w.,:;!?()'-]*$/.test(tr) && tr === en) problems.push(`[${lang}] left in English: "${en}"`);
  }
  for (const k of Object.keys(dict)) if (!keys.has(k)) problems.push(`[${lang}] unused entry: "${k}"`);
}
console.log(`${keys.size} sentences, 3 dictionaries.`);
if (problems.length) { console.log(problems.join("\n")); process.exit(1); }
console.log("All translations present.");
