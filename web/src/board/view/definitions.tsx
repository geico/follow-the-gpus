import { useMemo } from "react";
import { DEFS_ERROR_CODE, deriveDefinitions } from "../derive/definitions";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";

const LABEL = "Definitions, quota walls, not measured";

const KICKER = {
  margin: 0,
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "var(--color-neutral-400)",
} as const;

const BODY = {
  margin: "8px 0 0",
  fontSize: 12,
  lineHeight: 1.5,
  color: "var(--color-neutral-300)",
} as const;

/** The text with the error code (it occurs once) in monospace 11 px. */
function withCode(text: string) {
  const at = text.indexOf(DEFS_ERROR_CODE);
  if (at < 0) return text;
  return (
    <>
      {text.slice(0, at)}
      <code style={{ fontFamily: "monospace", fontSize: 11 }}>{DEFS_ERROR_CODE}</code>
      {text.slice(at + DEFS_ERROR_CODE.length)}
    </>
  );
}

export function DefinitionsView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveDefinitions(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  return (
    <SectionShell name={result.section} label={LABEL}>
      <div
        style={{
          background: "var(--color-surface)",
          borderRadius: "var(--radius-md)",
          boxShadow: "var(--shadow-sm)",
          padding: "14px 16px",
        }}
      >
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>What a “yes” is, exactly</h2>
        <ul
          aria-label="Definitions of a yes"
          style={{
            listStyle: "none",
            margin: "10px 0 0",
            padding: 0,
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 24,
          }}
        >
          {result.defs.map((d) => (
            <li
              key={d.lead}
              data-testid="def"
              style={{ fontSize: 12, lineHeight: 1.5, color: "var(--color-neutral-300)" }}
            >
              <strong style={{ color: "var(--color-text)" }}>{d.lead}</strong>
              {" — "}
              {withCode(d.text)}
            </li>
          ))}
        </ul>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 24,
          marginTop: 24,
        }}
      >
        <div
          data-testid="walls"
          style={{
            background: "var(--color-surface)",
            borderRadius: "var(--radius-md)",
            boxShadow: "var(--shadow-sm)",
            padding: "14px 16px",
          }}
        >
          <h3 style={KICKER}>{result.wallsKicker}</h3>
          <p style={BODY}>{result.walls}</p>
        </div>
        <div style={{ border: "1px solid var(--color-neutral-800)", padding: "14px 16px" }}>
          <h3 style={KICKER}>{result.notMeasuredKicker}</h3>
          <ul
            aria-label="Not measured"
            style={{
              ...BODY,
              listStyle: "none",
              padding: 0,
              columnCount: 2,
              columnGap: 24,
            }}
          >
            {result.notMeasured.map((t) => (
              <li
                key={t}
                data-testid="not-measured"
                style={{ breakInside: "avoid", marginBottom: 6 }}
              >
                {t}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </SectionShell>
  );
}
