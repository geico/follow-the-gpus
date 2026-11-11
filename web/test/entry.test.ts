import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDir = join(dirname(fileURLToPath(import.meta.url)), "..");

// Spec 0006 section 12: the cut-over makes index.html the board and removes the old page.
describe("the board entry", () => {
  it("index.html loads the board bootstrap and has the board root", () => {
    const html = readFileSync(join(webDir, "index.html"), "utf8");
    expect(html).toContain('src="/src/board/main.tsx"');
    expect(html).toContain('<div id="root">');
  });

  it("has no second entry and no old-page file", () => {
    expect(existsSync(join(webDir, "board.html"))).toBe(false);
    for (const f of ["scripts/sync.sh", "sql/cells.sql", "src/main.ts", "src/panels.ts"]) {
      expect(existsSync(join(webDir, f)), f).toBe(false);
    }
    expect(readFileSync(join(webDir, "vite.config.ts"), "utf8")).not.toContain("board.html");
  });
});
