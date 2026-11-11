import { type CSSProperties, type ReactNode, useMemo } from "react";
import { deriveStickiness, type StickyRow } from "../derive/stickiness";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { type TipText, useTip } from "./tooltip";

const LABEL = "Is capacity sticky? What the last round tells you about the next";

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
const VALUE: CSSProperties = {
  width: 78,
  fontSize: 11,
  color: "var(--color-neutral-300)",
  whiteSpace: "nowrap",
};

function Bar({
  w,
  fill,
  value,
  n,
  tip,
  label,
}: {
  w: number;
  fill: string;
  value: string;
  n: number;
  tip: TipText;
  label: string;
}) {
  return (
    <TipButton tip={tip} label={label}>
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={TRACK} aria-hidden="true">
          <span style={{ display: "block", width: `${w}%`, height: "100%", background: fill }} />
        </span>
        <span style={VALUE} aria-hidden="true">
          {value}
          {n > 0 && <span style={{ color: "var(--color-neutral-600)" }}> n={n}</span>}
        </span>
      </span>
    </TipButton>
  );
}

function TipButton({ tip, label, children }: { tip: TipText; label: string; children: ReactNode }) {
  const t = useTip();
  return (
    <button
      type="button"
      style={TIP_BTN}
      aria-label={label}
      onMouseMove={(e) => t.show(e, tip)}
      onMouseLeave={t.hide}
      onFocus={(e) => {
        const b = e.currentTarget.getBoundingClientRect();
        t.show({ clientX: b.left, clientY: b.top }, tip);
      }}
      onBlur={t.hide}
    >
      {children}
    </button>
  );
}

function Row({ r, focus }: { r: StickyRow; focus: boolean }) {
  const lines = r.tip.lines.join(". ");
  return (
    <tr>
      <th
        scope="row"
        style={{ ...CELL, ...NAME, color: focus ? "var(--color-accent-300)" : "var(--color-text)" }}
      >
        {r.name}
      </th>
      <td style={CELL}>
        <Bar
          w={r.aW}
          fill="var(--color-accent-500)"
          value={r.a}
          n={r.an}
          tip={r.tip}
          label={`${r.name}, ${r.tip.lines[0]}`}
        />
      </td>
      <td style={CELL}>
        <Bar
          w={r.bW}
          fill="var(--color-neutral-500)"
          value={r.b}
          n={r.bn}
          tip={r.tip}
          label={`${r.name}, ${r.tip.lines[1]}`}
        />
      </td>
      <td style={{ ...CELL, fontSize: 11, color: "var(--color-neutral-300)" }}>
        <TipButton tip={r.tip} label={`${r.name}, ${lines}`}>
          {r.streakText}
        </TipButton>
      </td>
    </tr>
  );
}

export function StickinessView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveStickiness(data, state), [data, state]);
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
              <col style={{ width: 120 }} />
            </colgroup>
            <thead>
              <tr>
                <td style={{ ...CELL, ...HEAD }} />
                <th scope="col" style={{ ...CELL, ...HEAD }}>
                  yes again, after a <b style={HEAD_WORD}>yes</b>
                </th>
                <th scope="col" style={{ ...CELL, ...HEAD }}>
                  yes, after a <b style={HEAD_WORD}>no</b>
                </th>
                <th scope="col" style={{ ...CELL, ...HEAD }}>
                  {result.heads.streak}
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
