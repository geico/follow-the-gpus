import { useMemo } from "react";
import { deriveSubstitutes } from "../derive/substitutes";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";

const LABEL = "Substitutes";

export function SubstitutesView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveSubstitutes(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  return (
    <SectionShell name={result.section} label={LABEL}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>{result.title}</h2>
      <p
        style={{
          margin: "6px 0 0",
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--color-neutral-500)",
          textWrap: "pretty",
        }}
      >
        {result.intro}
      </p>
      <ul
        aria-label="Share of the no rounds in which each other machine had a yes"
        style={{
          listStyle: "none",
          margin: "14px 0 0",
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {result.rows.map((row) => (
          <li
            key={row.name}
            data-testid="sub-row"
            style={{
              display: "grid",
              gridTemplateColumns: "110px 1fr 56px",
              alignItems: "center",
              gap: 10,
            }}
          >
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: "var(--color-text)",
              }}
            >
              {row.name}
            </span>
            <span
              aria-hidden="true"
              data-testid="sub-track"
              style={{
                display: "block",
                height: 10,
                borderRadius: 3,
                background: "var(--color-neutral-900)",
                overflow: "hidden",
              }}
            >
              <span
                data-testid="sub-bar"
                style={{
                  display: "block",
                  height: 10,
                  width: `${row.w}%`,
                  borderRadius: 3,
                  background: "var(--color-accent-500)",
                }}
              />
            </span>
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                textAlign: "right",
                letterSpacing: "-0.02em",
                color: `var(${row.tone})`,
              }}
            >
              {row.big}
            </span>
          </li>
        ))}
      </ul>
      <p style={{ margin: "12px 0 0", fontSize: 11, color: "var(--color-neutral-500)" }}>
        {result.footer}
      </p>
    </SectionShell>
  );
}
