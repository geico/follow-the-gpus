// The standalone board (spec 0006 section 9.1): 1 HTML file that works from file:// with no
// network. It reads a folder that `make web/export` wrote and inlines the CSS, the module script,
// the Latin Inter woff2 faces (as data URIs), and every api/*.json body (minified, nothing dropped)
// in window.__FTG, and the license texts of the bundled packages as a non-rendered text block.
// A script before the module replaces window.fetch with a lookup in that object.
// Run: node scripts/standalone.ts <dist-dir> <out-file> (use `make web/standalone`).
// No network call and no credential.

import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const NOTICES = "THIRD_PARTY_NOTICES.txt";
/** The id of the block that holds the notices text. */
export const NOTICES_ID = "third-party-notices";

/** The script that answers fetch("./api/...") from window.__FTG; any other URL gets a 404. */
export const FETCH_SHIM = `(function(){var D=window.__FTG;window.fetch=function(u){var p=String(u).split(/[?#]/)[0].replace(/^\\.\\//,"");var b=Object.prototype.hasOwnProperty.call(D,p)?D[p]:null;return Promise.resolve({ok:b!==null,status:b!==null?200:404,json:function(){return b!==null?Promise.resolve(JSON.parse(b)):Promise.reject(new Error("404 "+p))}})}})();`;

function listJson(dir: string, prefix: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(join(dir, prefix), { withFileTypes: true })) {
    const rel = `${prefix}/${e.name}`;
    if (e.isDirectory()) out.push(...listJson(dir, rel));
    else if (e.name.endsWith(".json")) out.push(rel);
  }
  return out.sort();
}

/** Keeps only the Latin woff2 face of each weight, as a data URI; drops the other scripts. */
function inlineFonts(css: string, dist: string): string {
  return css.replace(/@font-face\{[^}]*\}/g, (face) => {
    const m = face.match(/url\(\.\/(inter-latin-\d[^)]*?\.woff2)\)/);
    const file = m?.[1];
    if (file === undefined) return "";
    const b64 = readFileSync(join(dist, "assets", file)).toString("base64");
    return face.replace(/src:[^;}]*/, `src:url(data:font/woff2;base64,${b64})format("woff2")`);
  });
}

/** Builds the HTML text of the standalone board from the folder `distDir`. */
export function buildStandaloneHtml(distDir: string): string {
  const dist = resolve(distDir);
  let html = readFileSync(join(dist, "index.html"), "utf8");
  const data: Record<string, string> = {};
  for (const f of listJson(dist, "api")) {
    data[f] = JSON.stringify(JSON.parse(readFileSync(join(dist, f), "utf8")));
  }
  // "<" as < keeps a "</script" inside a body from ending the script.
  const embed = `window.__FTG=${JSON.stringify(data).replace(/</g, "\\u003c")};`;
  let sawScript = false;
  let sawCss = false;
  html = html.replace(/<script type="module"[^>]*src="\.\/([^"]+)"[^>]*><\/script>/, (_, src) => {
    sawScript = true;
    const js = readFileSync(join(dist, src), "utf8").replace(/<\/script/gi, "<\\/script");
    return `<script>${embed}${FETCH_SHIM}</script><script type="module">${js}</script>`;
  });
  html = html.replace(/<link rel="stylesheet"[^>]*href="\.\/([^"]+)"[^>]*>/, (_, href) => {
    sawCss = true;
    return `<style>${inlineFonts(readFileSync(join(dist, href), "utf8"), dist)}</style>`;
  });
  if (!sawScript || !sawCss)
    throw new Error("standalone: index.html has no module script or CSS link");
  // The notice text has URLs; it sits in 1 non-rendered block, and "</" is escaped so that the text
  // cannot end the block. The test skips only this block in its URL check.
  const noticesFile = join(dist, NOTICES);
  if (!existsSync(noticesFile)) throw new Error(`standalone: ${NOTICES} is missing in ${dist}`);
  const notices = readFileSync(noticesFile, "utf8").replace(/<\//g, "<\\/");
  // A replacer function: a string would read "$&" and similar in the notice text as a pattern.
  return html.replace(
    "</body>",
    () => `<script type="text/plain" id="${NOTICES_ID}">\n${notices}</script></body>`,
  );
}

function main(): void {
  const [dist, out] = process.argv.slice(2);
  if (!dist || !out) throw new Error("usage: standalone.ts <dist-dir> <out-file>");
  const html = buildStandaloneHtml(dist);
  writeFileSync(resolve(out), html);
  console.log(
    `standalone: wrote ${resolve(out)} (${(Buffer.byteLength(html) / 1e6).toFixed(1)} MB)`,
  );
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (e) {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  }
}
