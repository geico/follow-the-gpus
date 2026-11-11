import { type FocusEvent, useMemo } from "react";
import { deriveHowmany, type HowmanyRung } from "../derive/howmany";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const LABEL = "How many at once?";

function Rung({ rung }: { rung: HowmanyRung }) {
  const tip = useTip();
  const text = { title: rung.title, lines: [rung.line] };
  const onFocus = (e: FocusEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    tip.show({ clientX: r.left, clientY: r.top }, text);
  };
  return (
    <div
      role="img"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a keyboard user reaches each bar to read its tooltip
      tabIndex={0}
      aria-label={`${rung.title}: ${rung.line}`}
      data-rung={rung.label}
      onMouseMove={(e) => tip.show(e, text)}
      onMouseLeave={tip.hide}
      onFocus={onFocus}
      onBlur={tip.hide}
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        justifyContent: "flex-end",
        alignItems: "stretch",
        height: 42,
        position: "relative",
        boxShadow: "inset 0 -1px 0 var(--color-neutral-800)",
      }}
    >
      <div
        data-bar
        style={{
          height: `${rung.h}%`,
          background: "var(--color-accent-500)",
          borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
        }}
      />
      <span
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: `calc(${rung.h}% + 2px)`,
          textAlign: "center",
          fontSize: 9,
          color: "var(--color-neutral-500)",
        }}
      >
        {rung.label}
      </span>
    </div>
  );
}

export function HowmanyView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveHowmany(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  return (
    <SectionShell name={result.section} label={LABEL}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>{LABEL}</h2>
      <p
        style={{
          margin: "6px 0 14px",
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--color-neutral-500)",
          textWrap: "pretty",
        }}
      >
        {result.note}
      </p>
      <ul
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        {result.rows.map((row) => (
          <li
            key={row.name}
            style={{ display: "grid", gridTemplateColumns: "96px 1fr", gap: 12, alignItems: "end" }}
          >
            <div>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color: row.focus ? "var(--color-accent-300)" : "var(--color-text)",
                }}
              >
                {row.name}
              </div>
              <div style={{ fontSize: 10, color: "var(--color-neutral-500)" }}>{row.sub}</div>
            </div>
            <div style={{ display: "flex", gap: 6, paddingTop: 12 }}>
              {row.rungs.map((r) => (
                <Rung key={r.label} rung={r} />
              ))}
            </div>
          </li>
        ))}
      </ul>
    </SectionShell>
  );
}
