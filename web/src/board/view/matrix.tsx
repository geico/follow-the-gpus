import { type CSSProperties, type FocusEvent, useMemo } from "react";
import { flipsOnHeat, HATCH, heat } from "../derive/format";
import { deriveMatrix, type MatrixCell } from "../derive/matrix";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const LABEL = "Where did the yes come from?";
const NEVER_RING = "inset 0 0 0 1px var(--color-neutral-900)";

/** The look of a cell by kind (section 11.21). No outline and no reset: the stylesheet ring stays. */
function cellStyle(c: MatrixCell): CSSProperties {
  const base: CSSProperties = { color: "var(--color-neutral-400)", boxShadow: NEVER_RING };
  switch (c.kind) {
    case "got":
      return {
        background: heat(c.p),
        color: flipsOnHeat(c.p) ? "var(--color-bg)" : "var(--color-text)",
      };
    case "block": {
      return {
        color: "var(--color-accent-2-200)",
        boxShadow: `inset 0 0 0 1px color-mix(in oklch, var(--color-accent-2-500) ${c.ringPct ?? 25}%, var(--color-neutral-800))`,
      };
    }
    case "quota":
      return { background: HATCH, color: "var(--color-neutral-300)" };
    default:
      return base;
  }
}

function Cell({ cell }: { cell: MatrixCell }) {
  const tip = useTip();
  const text = { title: cell.title, lines: cell.lines };
  const onFocus = (e: FocusEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    tip.show({ clientX: r.left, clientY: r.top }, text);
  };
  return (
    <td
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a keyboard user reaches each cell to read its tooltip
      tabIndex={0}
      aria-label={cell.label}
      data-kind={cell.kind}
      onMouseMove={(e) => tip.show(e, text)}
      onMouseLeave={tip.hide}
      onFocus={onFocus}
      onBlur={tip.hide}
      style={{
        height: 52,
        padding: "5px 6px 4px",
        borderRadius: 5,
        verticalAlign: "top",
        textAlign: "left",
        ...cellStyle(cell),
      }}
    >
      <div
        aria-hidden="true"
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          height: "100%",
        }}
      >
        <div>
          <div style={{ fontSize: 8, fontWeight: 600, letterSpacing: "0.06em", minHeight: 10 }}>
            {cell.tag}
          </div>
          <div
            style={{
              fontSize: cell.kind === "got" || cell.kind === "block" ? 12 : 10,
              fontWeight: 600,
            }}
          >
            {cell.big}
          </div>
        </div>
        {cell.days.length > 0 ? (
          <div style={{ display: "flex", alignItems: "flex-end", gap: 1.5, height: 12 }}>
            {cell.days.map((d, i) => (
              <span
                // biome-ignore lint/suspicious/noArrayIndexKey: the steps are positional and never reorder
                key={i}
                data-bar
                style={{
                  flex: 1,
                  height: `${d.h}%`,
                  background: "currentColor",
                  opacity: 0.55,
                }}
              />
            ))}
          </div>
        ) : null}
      </div>
    </td>
  );
}

const headStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 400,
  color: "var(--color-neutral-400)",
  textAlign: "left",
  padding: "0 0 4px",
};

export function MatrixView({ data, state, setState }: SectionProps) {
  const result = useMemo(() => deriveMatrix(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  const span = result.columns.length + 1;
  return (
    <SectionShell name={result.section} label={LABEL}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 500 }}>{result.title}</h2>
      <p
        style={{
          margin: "6px 0 14px",
          fontSize: 12,
          lineHeight: 1.5,
          color: "var(--color-neutral-500)",
          textWrap: "pretty",
        }}
      >
        {result.intro}
      </p>
      <table
        aria-label="Share of machines handed to us, by machine and region"
        style={{
          width: "100%",
          tableLayout: "fixed",
          borderCollapse: "separate",
          borderSpacing: 3,
        }}
      >
        <colgroup>
          <col style={{ width: 110 }} />
          {result.columns.map((c) => (
            <col key={`${c.cloud}|${c.region}`} />
          ))}
        </colgroup>
        <thead>
          <tr>
            <td />
            {result.heads.map((h) => (
              <th
                key={h.cloud}
                scope="colgroup"
                colSpan={h.n}
                style={{
                  ...headStyle,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  borderBottom: "1px solid var(--color-neutral-800)",
                }}
              >
                {h.cloud}
              </th>
            ))}
          </tr>
          <tr>
            <td />
            {result.columns.map((c) => (
              <th key={`${c.cloud}|${c.region}`} scope="col" style={headStyle}>
                {c.region}
              </th>
            ))}
          </tr>
        </thead>
        {result.groups.map((g) => (
          <tbody key={g.tier}>
            <tr>
              <th
                scope="rowgroup"
                colSpan={span}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  textAlign: "left",
                  textTransform: "uppercase",
                  color: "var(--color-accent-300)",
                  padding: "10px 0 4px",
                  borderBottom: "1px solid var(--color-neutral-900)",
                }}
              >
                {g.tier}
                <span
                  style={{
                    marginLeft: 8,
                    fontWeight: 400,
                    textTransform: "none",
                    color: "var(--color-neutral-500)",
                  }}
                >
                  {g.sub}
                </span>
              </th>
            </tr>
            {g.rows.map((row) => (
              <tr key={row.name}>
                <th
                  scope="row"
                  style={{ textAlign: "left", fontWeight: 400, verticalAlign: "top" }}
                >
                  <button
                    type="button"
                    title={row.skus}
                    aria-pressed={row.focus}
                    onClick={() => {
                      setState?.({ ...state, gpu: row.name });
                    }}
                    style={{
                      display: "block",
                      background: "none",
                      border: 0,
                      padding: 0,
                      margin: 0,
                      font: "inherit",
                      textAlign: "left",
                      cursor: "pointer",
                      fontSize: 13,
                      fontWeight: 600,
                      letterSpacing: "-0.01em",
                      color: row.focus ? "var(--color-accent-300)" : "var(--color-text)",
                    }}
                  >
                    {row.name}
                  </button>
                  <span
                    style={{ display: "block", fontSize: 10, color: "var(--color-neutral-500)" }}
                  >
                    {row.sub}
                  </span>
                </th>
                {row.cells.map((c, i) => (
                  <Cell key={result.columns[i]?.region ?? i} cell={c} />
                ))}
              </tr>
            ))}
          </tbody>
        ))}
      </table>
      <ul
        aria-label="Legend"
        style={{
          listStyle: "none",
          margin: "14px 0 0",
          padding: 0,
          display: "flex",
          flexWrap: "wrap",
          gap: "4px 18px",
          fontSize: 11,
          color: "var(--color-neutral-500)",
        }}
      >
        {result.legend.map((l) => (
          <li key={l.term}>
            <strong style={{ fontWeight: 600, color: "var(--color-neutral-300)" }}>{l.term}</strong>
            {l.text === "" ? "" : ` ${l.text}`}
          </li>
        ))}
      </ul>
    </SectionShell>
  );
}
