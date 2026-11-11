import { useMemo } from "react";
import { flipsOnHeat, heat } from "../derive/format";
import { deriveTimeofday, type TodCell } from "../derive/timeofday";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const LABEL_W = 132;
/** The table's border-spacing: the gap at each cell edge, outside and between. */
const SPACING = 3;

function Cell({ cell }: { cell: TodCell }) {
  const tip = useTip();
  const text = [cell.tip.title, ...cell.tip.lines].join(". ");
  const fg = flipsOnHeat(cell.p) ? "var(--color-bg)" : "var(--color-neutral-200)";
  return (
    <td style={{ padding: 0 }}>
      <button
        type="button"
        aria-label={text}
        onMouseMove={(e) => tip.show(e, cell.tip)}
        onMouseLeave={tip.hide}
        onFocus={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          tip.show({ clientX: r.left, clientY: r.top }, cell.tip);
        }}
        onBlur={tip.hide}
        style={{
          display: "block",
          width: "100%",
          height: 46,
          border: 0,
          textAlign: "left",
          font: "inherit",
          borderRadius: 5,
          padding: "5px 6px 4px",
          boxSizing: "border-box",
          background: cell.thin ? "transparent" : heat(cell.p),
          boxShadow: cell.thin ? "inset 0 0 0 1px var(--color-neutral-800)" : undefined,
          color: cell.thin ? "var(--color-neutral-400)" : fg,
        }}
      >
        <div style={{ fontSize: 12, fontWeight: 600 }}>{cell.big}</div>
        <div style={{ fontSize: 9 }}>{cell.sub}</div>
      </button>
    </td>
  );
}

export function TimeofdayView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveTimeofday(data, state), [data, state]);
  if (result.empty)
    return <SectionShell name={result.section} label="Does the time of day matter?" />;
  return (
    <SectionShell name={result.section} label={result.title}>
      <h2>{result.title}</h2>
      <p>{result.intro}</p>
      <div
        data-testid="tod-brackets"
        style={{
          position: "relative",
          height: 34,
          // The hour columns start after the table's edge gap, the label column, and 1 gap more,
          // and end 1 edge gap before the table's right edge.
          marginLeft: LABEL_W + 2 * SPACING,
          marginRight: SPACING,
        }}
        aria-hidden="true"
      >
        {result.bands.map((b, bi) =>
          b.pieces.map((pc, pi) => {
            const color = b.zone === "ET" ? "var(--color-neutral-400)" : "var(--color-accent-400)";
            return (
              <div
                key={`${b.zone}${pc.l}`}
                style={{
                  position: "absolute",
                  left: `${pc.l}%`,
                  width: `${pc.w}%`,
                  top: 20,
                  height: 8,
                  boxSizing: "border-box",
                  border: `1px solid ${color}`,
                  borderBottom: "none",
                  borderRadius: "3px 3px 0 0",
                }}
              >
                {pi === 0 && (
                  <span
                    style={{
                      position: "absolute",
                      top: bi === 0 ? -10 : -20,
                      left: 0,
                      fontSize: 9,
                      color,
                      whiteSpace: "nowrap",
                    }}
                  >
                    {b.label}
                  </span>
                )}
              </div>
            );
          }),
        )}
      </div>
      <table
        aria-label={result.title}
        style={{ width: "100%", borderSpacing: SPACING, tableLayout: "fixed" }}
      >
        <thead>
          <tr>
            <th scope="col" style={{ width: LABEL_W }}>
              <span className="sr-only">Row</span>
            </th>
            {result.heads.map((h) => (
              <th
                key={h}
                scope="col"
                style={{ fontSize: 10, fontWeight: 400, color: "var(--color-neutral-400)" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row) => (
            <tr key={row.label}>
              <th scope="row" style={{ fontSize: 12, fontWeight: 400, textAlign: "left" }}>
                <div style={{ color: row.weekend ? "var(--color-accent-300)" : undefined }}>
                  {row.label}
                </div>
                <div style={{ fontSize: 10, color: "var(--color-neutral-500)" }}>{row.sub}</div>
              </th>
              {row.cells.map((c, i) => (
                <Cell key={result.heads[i]} cell={c} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 11, color: "var(--color-neutral-400)", display: "grid", gap: 4 }}>
        {result.legend.map((l, i) => (
          <span key={l} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {i === 0 && (
              <i
                data-testid="tod-legend-gradient"
                aria-hidden="true"
                style={{
                  display: "inline-block",
                  width: 72,
                  height: 8,
                  borderRadius: 2,
                  background:
                    "linear-gradient(to right, var(--color-neutral-900), var(--color-accent-400))",
                }}
              />
            )}
            {l}
          </span>
        ))}
      </div>
      {result.verdict !== "" && <p data-testid="tod-verdict">{result.verdict}</p>}
    </SectionShell>
  );
}
