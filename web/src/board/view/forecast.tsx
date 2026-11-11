import { type CSSProperties, useMemo } from "react";
import { deriveForecast, FORECAST_HEAD_LIKELY, FORECAST_HEAD_UNLIKELY } from "../derive/forecast";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";

const TITLE = "Does a high score predict a yes?";

const box: CSSProperties = {
  border: "1px dashed var(--color-neutral-800)",
  borderRadius: "var(--radius-md)",
  padding: "18px 20px 20px",
};
const kicker: CSSProperties = {
  margin: "0 0 10px",
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: "0.07em",
  textTransform: "uppercase",
  color: "var(--color-neutral-400)",
};
const boxGrid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 300px",
  gap: 32,
};
const headWord: CSSProperties = { fontWeight: 600, color: "var(--color-neutral-300)" };
const rowGrid: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "110px 1fr 1fr",
  gap: 12,
  alignItems: "center",
};
const track: CSSProperties = { display: "flex", alignItems: "center", gap: 8 };

function Bar({ w, color, label }: { w: number; color: string; label: string }) {
  return (
    <div style={track}>
      <div
        data-testid="forecast-track"
        style={{
          position: "relative",
          flex: 1,
          minWidth: 0,
          height: 12,
          borderRadius: 3,
          background: "var(--color-neutral-900)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: `${w}%`,
            background: color,
            borderRadius: 3,
          }}
        />
      </div>
      <span
        style={{
          width: 74,
          flex: "none",
          fontSize: 11,
          color: "var(--color-neutral-300)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
    </div>
  );
}

export function ForecastView({ data, state }: SectionProps) {
  const r = useMemo(() => deriveForecast(data, state), [data, state]);
  if (r.empty) return <SectionShell name={r.section} label={TITLE} />;
  return (
    <SectionShell name={r.section} label={TITLE}>
      <div style={box}>
        <p style={kicker}>{r.kicker}</p>
        <div style={boxGrid}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>{TITLE}</h2>
            <p
              style={{
                margin: 0,
                fontSize: 12,
                lineHeight: 1.5,
                color: "var(--color-neutral-500)",
              }}
            >
              {r.intro}
            </p>
            <div
              style={{ ...rowGrid, fontSize: 10, color: "var(--color-neutral-500)" }}
              aria-hidden="true"
            >
              <span />
              <span>
                {FORECAST_HEAD_LIKELY.slice(0, -"likely".length)}
                <b style={headWord}>likely</b>
              </span>
              <span>
                {FORECAST_HEAD_UNLIKELY.slice(0, -"not likely".length)}
                <b style={headWord}>not likely</b>
              </span>
            </div>
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
              {r.rows.map((row) => (
                <li
                  key={row.name}
                  style={rowGrid}
                  aria-label={`${row.name}: ${r.headHi} ${row.hi}; ${r.headLo} ${row.lo}`}
                >
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
                  <Bar w={row.hiW} color="var(--color-accent-500)" label={row.hi} />
                  <Bar w={row.loW} color="var(--color-neutral-500)" label={row.lo} />
                </li>
              ))}
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
      </div>
    </SectionShell>
  );
}
