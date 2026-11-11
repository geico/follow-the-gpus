import type { FocusEvent } from "react";
import { useMemo } from "react";
import { type BookRow, deriveBookahead } from "../derive/bookahead";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const LABEL = "Book ahead: how long a block, how soon";
const HEADS = ["machine", "region", "longest block offered", "it starts in"] as const;

function Row({ row }: { row: BookRow }) {
  const tip = useTip();
  const text = { title: row.title, lines: row.lines };
  const onFocus = (e: FocusEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    tip.show({ clientX: r.left, clientY: r.top }, text);
  };
  return (
    <tr
      tabIndex={0}
      aria-label={`${row.title}: ${row.lines.join(". ")}`}
      onMouseMove={(e) => tip.show(e, text)}
      onMouseLeave={tip.hide}
      onFocus={onFocus}
      onBlur={tip.hide}
    >
      <td
        style={{
          padding: "3px 8px 3px 0",
          fontSize: 13,
          fontWeight: 600,
          letterSpacing: "-0.01em",
          color: row.focus ? "var(--color-accent-300)" : "var(--color-text)",
        }}
      >
        {row.name}
      </td>
      <td style={{ padding: "3px 8px 3px 0", fontSize: 11, color: "var(--color-neutral-400)" }}>
        {row.region}
      </td>
      <td style={{ padding: "3px 8px 3px 0" }}>
        <div
          data-testid="book-track"
          style={{
            position: "relative",
            height: 14,
            borderRadius: 3,
            background: "var(--color-neutral-900)",
            overflow: "hidden",
          }}
        >
          <div
            data-bar
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              width: `${row.w}%`,
              background: "var(--color-accent-2-600)",
              borderRadius: 3,
            }}
          />
          <span
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              bottom: 0,
              padding: "0 5px",
              display: "flex",
              alignItems: "center",
              fontSize: 10,
              whiteSpace: "nowrap",
              color: `var(${row.labTone})`,
            }}
          >
            {row.lab}
          </span>
        </div>
      </td>
      <td style={{ padding: "3px 0", fontSize: 11, color: "var(--color-neutral-200)" }}>
        {row.lead}
      </td>
    </tr>
  );
}

export function BookaheadView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveBookahead(data, state), [data, state]);
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
      <table
        aria-label={LABEL}
        style={{
          width: "100%",
          tableLayout: "fixed",
          borderCollapse: "collapse",
          textAlign: "left",
        }}
      >
        <colgroup>
          <col style={{ width: 84 }} />
          <col style={{ width: 76 }} />
          <col />
          <col style={{ width: 70 }} />
        </colgroup>
        <thead>
          <tr>
            {HEADS.map((h) => (
              <th
                key={h}
                scope="col"
                style={{
                  padding: "0 8px 4px 0",
                  fontSize: 10,
                  fontWeight: 400,
                  color: "var(--color-neutral-500)",
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row) => (
            <Row key={`${row.name}|${row.region}`} row={row} />
          ))}
        </tbody>
      </table>
      <p
        style={{
          margin: "10px 0 0",
          fontSize: 11,
          lineHeight: 1.5,
          color: "var(--color-neutral-500)",
        }}
      >
        {result.foot}
      </p>
    </SectionShell>
  );
}
