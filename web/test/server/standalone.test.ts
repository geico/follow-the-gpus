// Test of the standalone file (spec 0006 section 9.1): built from the generated sample database
// with the real scripts, it has no external URL and its fetch shim serves api paths from the embed.

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { exportApi } from "../../scripts/export";
import { buildStandaloneHtml, NOTICES_ID } from "../../scripts/standalone";
import { repoRoot, webDir } from "./fixture";

let work: string;
let html: string;
let meta: string;
const NOTICES_BLOCK = new RegExp(`<script type="text/plain" id="${NOTICES_ID}">.*?</script>`, "s");

beforeAll(async () => {
  work = mkdtempSync(join(tmpdir(), "ftg-standalone-"));
  const db = join(work, "sample.duckdb");
  // The sample is generated, not committed. The script needs the duckdb CLI on PATH.
  execFileSync(join(webDir, "scripts/make-sample.sh"), [join(work, "sample")], { stdio: "pipe" });
  execFileSync(
    join(repoRoot, "scripts/compact-rows.sh"),
    [join(work, "sample"), join(work, "rows")],
    { stdio: "pipe" },
  );
  execFileSync(join(repoRoot, "scripts/build-db.sh"), [join(work, "rows"), db], { stdio: "pipe" });
  const dist = join(work, "dist");
  await build({ root: webDir, logLevel: "silent", build: { outDir: dist, emptyOutDir: true } });
  await exportApi({ dbPath: db, outDir: dist, sqlDir: join(webDir, "sql"), repoRoot });
  html = buildStandaloneHtml(dist);
  meta = readFileSync(join(dist, "api/meta.json"), "utf8");
}, 240_000);
afterAll(() => rmSync(work, { recursive: true, force: true }));

describe("standalone board", () => {
  // The React bundle holds a few inert URLs in error text and comments; none is ever requested.
  it("has no http(s) URL except w3.org, react.dev, and inert React text", () => {
    // The notices block holds license URLs as text; it is the only part that is skipped.
    const noBlock = html.replace(NOTICES_BLOCK, "");
    expect(noBlock.length).toBeLessThan(html.length);
    const urls = noBlock.match(/https?:\/\/[^\s"'`)<\\]+/g) ?? [];
    const inert =
      /^https:\/\/(github\.com\/(facebook\/react|tc39)|reactjs\.org|developer\.mozilla\.org|bugs\.webkit\.org)\//;
    const bad = urls.filter(
      (u) => !/^https?:\/\/(www\.w3\.org|react\.dev)\//.test(u) && !inert.test(u),
    );
    expect(bad).toEqual([]);
    // The page itself (outside the inline script and style) loads nothing.
    const page = noBlock.replace(/<(script|style)[^>]*>.*?<\/\1>/gs, "");
    expect(page).not.toMatch(/<link[^>]*(href|src)="(?!data:)/);
    expect(page).not.toMatch(/<script/);
  });

  it("embeds the third-party notices in 1 non-rendered block with every package name", () => {
    const m = html.match(NOTICES_BLOCK);
    expect(m).not.toBeNull();
    const text = m?.[0] ?? "";
    const names = [
      "@fontsource/inter",
      "@phosphor-icons/react",
      "@radix-ui/react-tooltip",
      "@floating-ui/core",
      "@floating-ui/dom",
      "@floating-ui/react-dom",
      "@floating-ui/utils",
      "react-dom",
      "scheduler",
      "tailwind-merge",
      "clsx",
      "shadcn/ui",
      "tailwindcss",
    ];
    for (const n of names) expect(text).toContain(n);
    expect(text).toContain("SIL OPEN FONT LICENSE");
    expect(text).toContain("Floating UI contributors");
  });

  it("embeds notice text that has replace patterns unchanged", () => {
    const dist = join(work, "dist-pattern");
    mkdirSync(join(dist, "assets"), { recursive: true });
    mkdirSync(join(dist, "api"), { recursive: true });
    writeFileSync(
      join(dist, "index.html"),
      '<html><head><script type="module" src="./assets/a.js"></script>' +
        '<link rel="stylesheet" href="./assets/a.css"></head><body></body></html>',
    );
    writeFileSync(join(dist, "assets/a.js"), "1;");
    writeFileSync(join(dist, "assets/a.css"), "a{}");
    const text = "price $& and $1 and $$ and $` and $'\n";
    writeFileSync(join(dist, "THIRD_PARTY_NOTICES.txt"), text);
    const m = buildStandaloneHtml(dist).match(NOTICES_BLOCK);
    expect(m?.[0]).toContain(`\n${text}</script>`);
  });

  it("bundles the 6 answer icons of section 11.9 into the file", () => {
    // Clock is a bare key in the icon map: `clock:`. The other 5 names are quoted keys.
    for (const n of ["check-circle", "x-circle", "calendar-blank", "lock-simple", "minus-circle"]) {
      expect(html, n).toContain(`"${n}"`);
    }
    expect(html).toMatch(/\bclock:/);
    expect(html).toContain("data-icon");
  });

  it("embeds the Latin Inter faces only, no latin-ext face", () => {
    const faces = html.match(/@font-face\{[^}]*\}/g) ?? [];
    expect(faces).toHaveLength(3);
    expect(html).not.toMatch(/latin-ext/);
    // 3 weights, 3 data URIs; a latin-ext face would add more.
    expect(html.match(/url\(data:font\/woff2;base64,/g)).toHaveLength(3);
  });

  it("serves an api path from the embed with the fetch shim", async () => {
    const m = html.match(/<script>(window\.__FTG=.*?)<\/script><script type="module">/s);
    expect(m).not.toBeNull();
    const win: Record<string, unknown> = {};
    new Function("window", m?.[1] ?? "")(win);
    const f = win.fetch as (
      u: string,
    ) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>;
    const ok = await f("./api/meta.json");
    expect(ok.ok).toBe(true);
    expect(await ok.json()).toEqual(JSON.parse(meta));
    const w = await f("./api/window/all/0.json");
    expect(w.status).toBe(200);
    const miss = await f("./api/nope.json");
    expect(miss.ok).toBe(false);
    expect(miss.status).toBe(404);
  });
});
