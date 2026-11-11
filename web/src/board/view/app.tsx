import { useEffect, useMemo, useState } from "react";
import type { DataSource } from "../../data/source";
import { RequestError } from "../../data/source";
import { makeFrame } from "../derive/time";
import type { BoardData, ViewState } from "../derive/types";
import { fromHash, fromStored, toHash, toStored } from "../derive/viewstate";
import { HeaderView } from "./header";
import { renderSections } from "./sections";
import { SectionPlaceholder } from "./shell";
import { GlobalTooltip } from "./tooltip";

const STORAGE_KEY = "ftg.v2.view";

type Load =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "error"; text: string }
  | { kind: "ready"; data: BoardData };

/** The hash wins over localStorage (spec 0006 section 11.5). */
function initialState(): ViewState {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // storage disabled or throwing: use the default
  }
  const stored = fromStored(raw);
  return window.location.hash.length > 1 ? fromHash(window.location.hash, stored) : stored;
}

export function App({ source }: { source: DataSource }) {
  const [state, setState] = useState<ViewState>(initialState);
  const [load, setLoad] = useState<Load>({ kind: "loading" });

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, toStored(state));
    } catch {
      // storage disabled, full, or throwing: the hash still holds the state
    }
    const hash = toHash(state);
    if (window.location.hash !== hash) window.history.replaceState(null, "", hash);
  }, [state]);

  useEffect(() => {
    const onHash = () => setState((s) => fromHash(window.location.hash, s));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let live = true;
    setLoad({ kind: "loading" });
    (async () => {
      const meta = await source.meta();
      if (meta.t0 === null) return { kind: "empty" } as const;
      // A null page is the default page of the time model (section 11.2 item 3), the page every section frames.
      const page = makeFrame(meta, state.hz, state.page).page;
      if (state.page !== null && page !== state.page) {
        // A page outside the list was clamped (section 11.5): the state and the hash show the page the board shows.
        setState((s) => (s.hz === state.hz && s.page === state.page ? { ...s, page } : s));
        return null;
      }
      const window_ = await source.window(state.hz, page);
      return { kind: "ready", data: { meta, window: window_ } } as const;
    })().then(
      (l) => live && l !== null && setLoad(l),
      (e: unknown) =>
        live &&
        setLoad({
          kind: "error",
          text: e instanceof RequestError ? e.message : `Load failed: ${String(e)}`,
        }),
    );
    return () => {
      live = false;
    };
  }, [source, state.hz, state.page]);

  // The joined data object keeps its identity until a new response arrives, so the derive memos hold.
  const data = useMemo(() => (load.kind === "ready" ? load.data : null), [load]);

  return (
    <GlobalTooltip>
      <div className="page">
        <div className="board-scroll">
          <main className="board">
            <HeaderView data={data} state={state} setState={setState} />
            {load.kind === "error" && <p className="load-line">{load.text}</p>}
            {load.kind === "empty" && (
              <p className="load-line">
                No rows in this store. Run make data/sync, then make web/run.
              </p>
            )}
            {load.kind === "loading" &&
              renderSections((s) => (
                <SectionPlaceholder key={s.name} name={s.name} label={s.name} />
              ))}
            {data !== null &&
              renderSections((s) => (
                <s.View key={s.name} data={data} state={state} setState={setState} />
              ))}
          </main>
        </div>
      </div>
    </GlobalTooltip>
  );
}
