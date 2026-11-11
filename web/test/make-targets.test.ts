import { execFileSync } from "node:child_process";
import {
  chmodSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Spec 0006 sections 3 and 12 name `make web/preview` and the `export` npm script.
const webDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = dirname(webDir);
// The Makefile guard stops web/ goals when the repo path has a space; these tests need it to run.
const skipSpaced = repoRoot.includes(" ");

describe("web make targets", () => {
  it.skipIf(skipSpaced)("make -n web/preview runs vite preview on web/dist", () => {
    const out = execFileSync("make", ["-n", "web/preview"], { cwd: repoRoot, encoding: "utf8" });
    expect(out).toContain("run preview");
    expect(out).toContain("web/dist");
  });

  it.skipIf(skipSpaced)("make -n web/export runs the export script", () => {
    const out = execFileSync("make", ["-n", "web/export"], { cwd: repoRoot, encoding: "utf8" });
    expect(out).toContain("run export");
  });

  it.skipIf(skipSpaced)(
    "make -n web/export, web/preview and web/clean accept an absolute WEB_DIR",
    () => {
      const run = (target: string) =>
        execFileSync("make", ["-n", target, `WEB_DIR=${webDir}`], {
          cwd: repoRoot,
          encoding: "utf8",
        });
      const out = run("web/export");
      expect(out).toContain(`"${webDir}/dist"`);
      expect(out).not.toContain(`${repoRoot}${webDir}`);
      expect(run("web/preview")).toContain("run preview");
      expect(run("web/clean")).toContain(`${webDir}/dist`);
    },
  );

  it.skipIf(skipSpaced)(
    "make -n web/export resolves a relative WEB_DB against the repo root",
    () => {
      const out = execFileSync("make", ["-n", "web/export", "WEB_DB=data/x.duckdb"], {
        cwd: repoRoot,
        encoding: "utf8",
      });
      expect(out).toContain(`run export -- "${repoRoot}/data/x.duckdb"`);
    },
  );

  it("make stops with a message when WEB_DIR contains a space", () => {
    let stderr = "";
    try {
      execFileSync("make", ["-n", "web/export", "WEB_DIR=my web"], {
        cwd: repoRoot,
        encoding: "utf8",
        stdio: "pipe",
      });
    } catch (e) {
      stderr = String((e as { stderr?: string }).stderr);
    }
    expect(stderr).toContain("must not contain a space");
  });

  it("make stops with a message when WEB_DB contains a space", () => {
    let stderr = "";
    try {
      execFileSync("make", ["-n", "web/export", "WEB_DB=data/my x.duckdb"], {
        cwd: repoRoot,
        encoding: "utf8",
        stdio: "pipe",
      });
    } catch (e) {
      stderr = String((e as { stderr?: string }).stderr);
    }
    expect(stderr).toContain("must not contain a space");
  });

  it("a repo path with a space breaks only the web/ goals", () => {
    const base = mkdtempSync(join(tmpdir(), "ftg-make."));
    try {
      const copy = join(base, "a b");
      mkdirSync(copy);
      cpSync(join(repoRoot, "Makefile"), join(copy, "Makefile"));
      cpSync(join(repoRoot, "tasks"), join(copy, "tasks"), { recursive: true });
      for (const goal of ["help", "data/db"]) {
        execFileSync("make", ["-C", copy, "-n", goal], { encoding: "utf8", stdio: "pipe" });
      }
      let stderr = "";
      try {
        execFileSync("make", ["-C", copy, "-n", "web/export"], { encoding: "utf8", stdio: "pipe" });
      } catch (e) {
        stderr = String((e as { stderr?: string }).stderr);
      }
      expect(stderr).toContain("must not contain a space");
    } finally {
      rmSync(base, { recursive: true, force: true });
    }
  });

  it("package.json has the export script", () => {
    const pkg = JSON.parse(readFileSync(join(webDir, "package.json"), "utf8"));
    expect(pkg.scripts.export).toContain("scripts/export.ts");
  });

  // A symlink to another checkout's node_modules was emptied by npm ci; web/deps skipped the install.
  // The fake npm matches the real `npm ci --loglevel=error`: no stdout, exit 0, and it writes node_modules/.package-lock.json.
  it.skipIf(skipSpaced)("make web/deps installs when node_modules is an empty directory", () => {
    const tmp = mkdtempSync(join(tmpdir(), "ftg-deps-"));
    try {
      const fake = join(tmp, "npm");
      const dir = join(tmp, "web");
      mkdirSync(join(dir, "node_modules"), { recursive: true });
      writeFileSync(join(dir, "package-lock.json"), "{}\n");
      writeFileSync(
        fake,
        `#!/bin/sh\necho "$@" > "${tmp}/args"\nmkdir -p "${dir}/node_modules" && echo '{}' > "${dir}/node_modules/.package-lock.json"\n`,
      );
      chmodSync(fake, 0o755);
      const run = () =>
        execFileSync("make", ["web/deps", `WEB_DIR=${dir}`, `WEB_NPM=${fake}`], {
          cwd: repoRoot,
          encoding: "utf8",
        });
      expect(run()).toBe("");
      expect(readFileSync(join(tmp, "args"), "utf8")).toContain(
        "ci --no-audit --no-fund --loglevel=error",
      );
      // Installed and current: a second run skips the install.
      rmSync(join(tmp, "args"));
      run();
      expect(existsSync(join(tmp, "args"))).toBe(false);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});
