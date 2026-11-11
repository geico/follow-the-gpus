import { useMemo } from "react";
import { deriveIntro } from "../derive/intro";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";

const LABEL = "Intro and headline chips";

export function IntroView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveIntro(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  return (
    <SectionShell name={result.section} label={LABEL}>
      {result.banner !== null && (
        <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--color-accent-300)" }}>
          {result.banner}
        </p>
      )}
      <p style={{ margin: 0, fontSize: 13, color: "var(--color-neutral-500)" }}>
        {result.intro}{" "}
        <span data-sub style={{ color: "var(--color-neutral-400)" }}>
          {result.sub}
        </span>
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 18 }}>
        {result.groups.map((g) => (
          <div
            key={g.tag}
            data-group={g.tag}
            style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 16 }}
          >
            <div
              style={{
                paddingTop: 12,
                fontSize: 11,
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.07em",
                color: g.tagColor,
              }}
            >
              {g.tag}
            </div>
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                display: "flex",
                flexWrap: "wrap",
                gap: 8,
              }}
            >
              {g.chips.map((c) => (
                <li
                  key={c.name}
                  data-chip={c.name}
                  style={{
                    width: 236,
                    display: "flex",
                    flexDirection: "column",
                    gap: 5,
                    padding: "8px 12px 9px",
                    background: "var(--color-surface)",
                    borderRadius: "var(--radius-md)",
                  }}
                >
                  <span
                    style={{
                      fontSize: 15,
                      fontWeight: 600,
                      letterSpacing: "-0.01em",
                      color: c.focus ? "var(--color-accent-300)" : "var(--color-text)",
                    }}
                  >
                    {c.name}
                  </span>
                  <span
                    style={{
                      fontSize: 22,
                      fontWeight: 500,
                      letterSpacing: "-0.02em",
                      color: c.bigColor,
                    }}
                  >
                    {c.big}
                  </span>
                  {c.barOn && (
                    <div
                      data-bar
                      style={{
                        height: 5,
                        borderRadius: 2,
                        background: "var(--color-neutral-900)",
                      }}
                    >
                      <div
                        style={{
                          width: `${c.w}%`,
                          height: "100%",
                          borderRadius: 2,
                          background: "var(--color-accent-500)",
                        }}
                      />
                    </div>
                  )}
                  <span
                    style={{ fontSize: 10, lineHeight: 1.35, color: "var(--color-neutral-500)" }}
                  >
                    {c.sub}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <p
        style={{
          margin: "18px 0 0",
          maxWidth: 960,
          fontSize: 13,
          color: "var(--color-neutral-500)",
        }}
      >
        {result.footnote}
      </p>
    </SectionShell>
  );
}
