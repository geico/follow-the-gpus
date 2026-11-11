import { type CSSProperties, type ReactNode, useMemo } from "react";
import { type BacktestRow, deriveBacktest } from "../derive/backtest";
import { plural } from "../derive/format";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const LABEL = "Stay or follow? One region vs asking everywhere";

const TITLE: CSSProperties = { margin: 0, fontSize: 15, fontWeight: 500 };
const INTRO: CSSProperties = {
  margin: "4px 0 12px",
  fontSize: 12,
  lineHeight: 1.5,
  color: "var(--color-neutral-500)",
  textWrap: "pretty",
};
const WRAP: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0,1fr) 300px",
  gap: 32,
};
const TABLE: CSSProperties = {
  borderCollapse: "separate",
  borderSpacing: "12px 6px",
  margin: "-6px -12px",
};
const CELL: CSSProperties = { padding: 0, verticalAlign: "middle", textAlign: "left" };
const TIP_BTN: CSSProperties = {
  border: 0,
  background: "none",
  padding: 0,
  margin: 0,
  font: "inherit",
  color: "inherit",
  textAlign: "inherit",
  display: "block",
  width: "100%",
  cursor: "default",
  boxSizing: "border-box",
};
const HEAD: CSSProperties = { fontSize: 10, color: "var(--color-neutral-500)" };
const HEAD_WORD: CSSProperties = { fontWeight: 600, color: "var(--color-neutral-300)" };
const NAME: CSSProperties = { fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" };
const SUB: CSSProperties = { fontSize: 10, color: "var(--color-neutral-500)" };
const VERDICT: CSSProperties = {
  fontSize: 12,
  lineHeight: 1.55,
  color: "var(--color-neutral-300)",
  paddingTop: 26,
  margin: 0,
};
const TRACK: CSSProperties = {
  display: "block",
  flex: 1,
  height: 12,
  borderRadius: 3,
  background: "var(--color-neutral-900)",
  overflow: "hidden",
};
const VALUE: CSSProperties = { width: 38, fontSize: 11, color: "var(--color-neutral-300)" };

function Row({ r, focus }: { r: BacktestRow; focus: boolean }) {
  const t = useTip();
  const label = `${r.name}, ${r.tip.lines.join(". ")}`;
  const btn = (children: ReactNode) => (
    <button
      type="button"
      style={TIP_BTN}
      aria-label={label}
      onMouseMove={(e) => t.show(e, r.tip)}
      onMouseLeave={t.hide}
      onFocus={(e) => {
        const b = e.currentTarget.getBoundingClientRect();
        t.show({ clientX: b.left, clientY: b.top }, r.tip);
      }}
      onBlur={t.hide}
    >
      {children}
    </button>
  );
  const bar = (w: number, fill: string, value: string) => (
    <span style={{ display: "flex", alignItems: "center", gap: 8 }} aria-hidden="true">
      <span style={TRACK}>
        <span style={{ display: "block", width: `${w}%`, height: "100%", background: fill }} />
      </span>
      <span style={VALUE}>{value}</span>
    </span>
  );
  return (
    <tr>
      <th scope="row" style={CELL}>
        <div style={{ ...NAME, color: focus ? "var(--color-accent-300)" : "var(--color-text)" }}>
          {r.name}
        </div>
        <div style={SUB}>
          {plural(r.n, "round")} · {plural(r.nPlaces, "place")}
        </div>
      </th>
      <td style={CELL}>
        {btn(
          <>
            {bar(r.sW, "var(--color-neutral-500)", r.stay)}
            <span style={{ ...SUB, display: "block", color: "var(--color-neutral-600)" }}>
              {r.place}
            </span>
          </>,
        )}
      </td>
      <td style={CELL}>
        {btn(
          <>
            {bar(r.fW, "var(--color-accent-500)", r.follow)}
            <span style={{ ...SUB, display: "block" }}>any of {r.nPlaces} places</span>
          </>,
        )}
      </td>
      <td style={CELL}>
        {btn(
          <>
            <span
              style={{
                display: "block",
                fontSize: 12,
                fontWeight: 600,
                color: r.gain >= 10 ? "var(--color-accent-300)" : "var(--color-neutral-500)",
              }}
            >
              {r.gainTxt}
            </span>
            <span style={{ ...SUB, display: "block" }}>
              {r.cover} to cover · {r.sole} sole-source
            </span>
          </>,
        )}
      </td>
    </tr>
  );
}

export function BacktestView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveBacktest(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  return (
    <SectionShell name={result.section} label={LABEL}>
      <h2 style={TITLE}>{LABEL}</h2>
      <p style={INTRO}>{result.intro}</p>
      <div style={WRAP}>
        {result.rows.length > 0 ? (
          <table aria-label={LABEL} style={TABLE}>
            <colgroup>
              <col style={{ width: 130 }} />
              <col />
              <col />
              <col style={{ width: 150 }} />
            </colgroup>
            <thead>
              <tr>
                <td style={{ ...CELL, ...HEAD }} />
                <th scope="col" style={{ ...CELL, ...HEAD }}>
                  <b style={HEAD_WORD}>stay</b> · best single region
                </th>
                <th scope="col" style={{ ...CELL, ...HEAD }}>
                  <b style={HEAD_WORD}>follow</b> · any region
                </th>
                <th scope="col" style={{ ...CELL, ...HEAD }}>
                  {result.heads.gain}
                </th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((r) => (
                <Row key={r.name} r={r} focus={r.name === state.gpu} />
              ))}
            </tbody>
          </table>
        ) : (
          <div />
        )}
        <p style={VERDICT}>{result.verdict}</p>
      </div>
    </SectionShell>
  );
}
