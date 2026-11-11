import { describe, expect, it } from "vitest";
import { HttpDataSource, metaPath, RequestError, windowPath } from "../src/data/source";
import { stubMeta, stubWindow } from "../src/data/stub";

// The paths and the key order are those of spec 0006 sections 7.1, 7.3, 7.4, and 7.5.
const META_KEYS = [
  "t0",
  "tEnd",
  "nh",
  "dates",
  "weekends",
  "cadenceH",
  "probeStartH",
  "zones",
  "cbHorizonDays",
  "regions",
  "rows",
  "sweeps",
  "firstH",
  "cbFirstH",
  "upStartH",
  "zoneStartH",
  "simulated",
  "now",
];
const WINDOW_KEYS = ["hz", "page", "H0", "H1", "L", "sweeps", "cb", "spot", "azspot", "price"];

describe("DataSource paths and stub shapes", () => {
  it("uses the 2 paths of spec 0006 section 7.1", () => {
    expect(metaPath()).toBe("api/meta.json");
    expect(windowPath("week", 3)).toBe("api/window/week/3.json");
  });

  it("gives the stub the fields of the spec, in the order of the spec", () => {
    expect(Object.keys(stubMeta())).toEqual(META_KEYS);
    expect(Object.keys(stubWindow())).toEqual(WINDOW_KEYS);
  });

  it("fetches relative to the base, caches a response, and does not cache a failure", async () => {
    const calls: string[] = [];
    let fail = true;
    const src = new HttpDataSource("./", async (u) => {
      calls.push(u);
      return { ok: !fail, status: fail ? 404 : 200, json: async () => stubWindow("month", 1) };
    });
    await expect(src.window("month", 1)).rejects.toEqual(
      new RequestError("api/window/month/1.json", 404),
    );
    fail = false;
    await src.window("month", 1);
    await src.window("month", 1);
    expect(calls).toEqual(["./api/window/month/1.json", "./api/window/month/1.json"]);
  });
});
