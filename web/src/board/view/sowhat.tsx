import { useMemo } from "react";
import { deriveSowhat } from "../derive/sowhat";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";

const LABEL = "So what";

/** The "so what" card (spec 0006 section 11.10): 1 line per machine of the tier. */
export function SowhatView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveSowhat(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  return (
    <SectionShell name={result.section} label={LABEL}>
      <div
        style={{
          background: "var(--color-surface)",
          borderRadius: "var(--radius-md)",
          boxShadow: "var(--shadow-sm)",
          padding: "14px 16px 12px",
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 11,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "var(--color-neutral-400)",
          }}
        >
          {result.kicker}
        </h2>
        <ul
          aria-label="One line per machine"
          style={{
            listStyle: "none",
            margin: "8px 0 0",
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {result.plans.map((plan) => (
            <li
              key={plan.name}
              style={{
                display: "grid",
                gridTemplateColumns: "130px 1fr",
                fontSize: 12,
                lineHeight: 1.5,
                color: "var(--color-neutral-300)",
              }}
            >
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color: plan.name === state.gpu ? "var(--color-accent-300)" : "var(--color-text)",
                }}
              >
                {plan.name}
              </span>
              <span>{plan.text}</span>
            </li>
          ))}
        </ul>
      </div>
    </SectionShell>
  );
}
