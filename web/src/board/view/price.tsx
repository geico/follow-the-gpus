import { type CSSProperties, useMemo } from "react";
import { derivePrice } from "../derive/price";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const TITLE = "Did price move with scarcity?";

const rowGrid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "130px 48px minmax(0, 1fr) 64px 64px 60px",
  gap: 12,
  alignItems: "center",
};
const num: CSSProperties = { fontSize: 12, textAlign: "right", color: "var(--color-neutral-300)" };

export function PriceView({ data, state }: SectionProps) {
  const r = useMemo(() => derivePrice(data, state), [data, state]);
  const tip = useTip();
  if (r.empty) return <SectionShell name={r.section} label={TITLE} />;
  return (
    <SectionShell name={r.section} label={TITLE}>
      <h2 style={{ margin: "0 0 6px", fontSize: 15, fontWeight: 500 }}>{TITLE}</h2>
      <p
        style={{
          margin: "0 0 12px",
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--color-neutral-500)",
        }}
      >
        {r.intro}
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 300px", gap: 32 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {r.rows.length > 0 && (
            <div
              style={{ ...rowGrid, fontSize: 10, color: "var(--color-neutral-500)" }}
              aria-hidden="true"
            >
              <span>machine</span>
              <span />
              <span>daily mean, scaled to its own range</span>
              <span style={{ textAlign: "right" }}>{r.first}</span>
              <span style={{ textAlign: "right" }}>{r.last}</span>
              <span style={{ textAlign: "right" }}>change</span>
            </div>
          )}
          <ul
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            {r.rows.map((row) => {
              const text = { title: row.tip.title, lines: row.tip.lines };
              return (
                <li key={`${row.name}|${row.cloud}`} style={rowGrid}>
                  <span
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      letterSpacing: "-0.01em",
                      color:
                        row.name === state.gpu ? "var(--color-accent-300)" : "var(--color-text)",
                    }}
                  >
                    {row.name}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      textAlign: "center",
                      padding: "1px 0",
                      borderRadius: "var(--radius-sm)",
                      background: `var(${row.tagBg})`,
                      color: `var(${row.tagFg})`,
                    }}
                  >
                    {row.cloud}
                  </span>
                  <button
                    type="button"
                    style={{
                      display: "block",
                      width: "100%",
                      padding: 0,
                      border: 0,
                      background: "none",
                      cursor: "default",
                    }}
                    aria-label={[row.tip.title, ...row.tip.lines].join(". ")}
                    onMouseMove={(e) => tip.show(e, text)}
                    onMouseLeave={tip.hide}
                    onFocus={(e) => {
                      const b = e.currentTarget.getBoundingClientRect();
                      tip.show({ clientX: b.left, clientY: b.top }, text);
                    }}
                    onBlur={tip.hide}
                  >
                    <svg
                      viewBox="0 0 1000 100"
                      preserveAspectRatio="none"
                      width="100%"
                      height={22}
                      style={{ display: "block" }}
                      aria-hidden="true"
                    >
                      <path
                        d={row.path}
                        fill="none"
                        stroke="var(--color-neutral-400)"
                        strokeWidth={1.5}
                        vectorEffect="non-scaling-stroke"
                      />
                    </svg>
                  </button>
                  <span style={num}>{row.first}</span>
                  <span style={num}>{row.last}</span>
                  <span style={{ ...num, fontWeight: 600, color: `var(${row.deltaTone})` }}>
                    {row.delta}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        <p
          style={{
            margin: 0,
            paddingTop: 26,
            fontSize: 12,
            lineHeight: 1.55,
            color: "var(--color-neutral-300)",
          }}
        >
          {r.verdict}
        </p>
      </div>
    </SectionShell>
  );
}
