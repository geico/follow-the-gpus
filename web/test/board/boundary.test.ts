// Proves that each derive-layer check fails on a violation (spec 0006 section 10.3): the Biome
// import rule and the no-DOM type check. A clean file passes both, so a failure is the violation.

import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..", "..");
const BIN = join(WEB, "node_modules", ".bin");

// Biome runs in a temporary project with the real biome.json, so the tracked tree stays untouched.
function biome(source: string) {
  const dir = mkdtempSync(join(tmpdir(), "ftg-biome-"));
  try {
    const config = JSON.parse(readFileSync(join(WEB, "biome.json"), "utf8"));
    delete config.$schema;
    writeFileSync(join(dir, "biome.json"), JSON.stringify(config));
    mkdirSync(join(dir, "src/board/derive"), { recursive: true });
    writeFileSync(join(dir, "src/board/derive/probe.ts"), source);
    return spawnSync(join(BIN, "biome"), ["lint", "src/board/derive/probe.ts"], {
      cwd: dir,
      encoding: "utf8",
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function tsc(source: string) {
  const dir = mkdtempSync(join(tmpdir(), "ftg-derive-"));
  try {
    writeFileSync(join(dir, "probe.ts"), source);
    writeFileSync(
      join(dir, "tsconfig.json"),
      JSON.stringify({
        extends: join(WEB, "src/board/derive/tsconfig.json"),
        include: [join(dir, "probe.ts")],
      }),
    );
    return spawnSync(join(BIN, "tsc"), ["--noEmit", "-p", dir], { cwd: WEB, encoding: "utf8" });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const CLEAN = "export const x: number = 1;\n";

describe("the derive boundary", () => {
  it("passes a clean file", () => {
    expect(biome(CLEAN).status).toBe(0);
    expect(tsc(CLEAN).status).toBe(0);
  });

  it.each([
    ['import { useMemo } from "react";'],
    ['import { createRoot } from "react-dom/client";'],
    ['import { App } from "../view/app";'],
    ['import { Tooltip } from "../../components/ui/tooltip";'],
    ['import { HttpDataSource } from "../../data/source";'],
    ['import { readFileSync } from "node:fs";'],
    ['import { Database } from "@duckdb/node-api";'],
  ])("Biome fails on %s", (line) => {
    const r = biome(`${line}\nexport const x = 1;\n`);
    expect(r.status).not.toBe(0);
    expect(`${r.stdout}${r.stderr}`).toContain("lint/style/noRestrictedImports");
  });

  it("the type check fails on a DOM global", () => {
    const r = tsc("export const t = document.title;\n");
    expect(r.status).not.toBe(0);
    expect(r.stdout).toContain("document");
  });

  it("the derive tsconfig includes tsx files", () => {
    const c = JSON.parse(readFileSync(join(WEB, "src/board/derive/tsconfig.json"), "utf8"));
    expect(c.include).toContain("./**/*.tsx");
  });
});
