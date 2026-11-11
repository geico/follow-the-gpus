import { useMemo } from "react";
import { deriveControls, deriveHeader } from "../derive/header";
import type { BoardData, ViewState } from "../derive/types";
import { withHorizon, withTier } from "../derive/viewstate";

/** The header row and the controls. They render before the data arrives (spec 0006 section 11.6). */
export function HeaderView({
  data,
  state,
  setState,
}: {
  data: BoardData | null;
  state: ViewState;
  setState: (next: ViewState) => void;
}) {
  const result = useMemo(() => (data === null ? null : deriveHeader(data, state)), [data, state]);
  const full = result !== null && !result.empty ? result : null;
  const controls = useMemo(() => deriveControls(state), [state]);
  return (
    <section className="sec hdr" data-section="header" aria-label="Header">
      <span className="hdr-title">follow-the-gpus</span>
      <span className="hdr-span" data-ready={full === null ? "false" : "true"}>
        {full?.span}
      </span>
      <div className="hdr-group">
        <span className="hdr-label" id="ftg-hz-label">
          Horizon
        </span>
        <div className="seg" role="radiogroup" aria-labelledby="ftg-hz-label">
          {controls.horizons.map((h) => (
            // biome-ignore lint/a11y/useSemanticElements: spec 0006 section 11.7 asks for buttons with role="radio"
            <button
              key={h.value}
              type="button"
              role="radio"
              aria-checked={h.selected}
              onClick={() => setState(withHorizon(state, h.value))}
            >
              {h.label}
            </button>
          ))}
        </div>
      </div>
      <div className="hdr-group">
        <button
          type="button"
          className="pager"
          aria-label="Earlier"
          disabled={full === null || full.noPrev}
          onClick={() => full !== null && setState({ ...state, page: full.page - 1 })}
        >
          ‹
        </button>
        <span className="pager-label" aria-live="polite">
          {full?.pageLabel}
        </span>
        <button
          type="button"
          className="pager"
          aria-label="Later"
          disabled={full === null || full.noNext}
          onClick={() => full !== null && setState({ ...state, page: full.page + 1 })}
        >
          ›
        </button>
      </div>
      <div className="hdr-group">
        <label className="hdr-label" htmlFor="ftg-tier">
          Tier
        </label>
        <select
          id="ftg-tier"
          className="input"
          aria-label="Tier"
          value={state.tier}
          onChange={(e) => setState(withTier(state, e.target.value))}
        >
          {controls.tiers.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      <div className="hdr-group">
        <label className="hdr-label" htmlFor="ftg-gpu">
          Focus
        </label>
        <select
          id="ftg-gpu"
          className="input"
          aria-label="Machine"
          title={controls.focusTitle}
          value={state.gpu}
          onChange={(e) => setState({ ...state, gpu: e.target.value })}
        >
          {controls.machines.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
      </div>
    </section>
  );
}
