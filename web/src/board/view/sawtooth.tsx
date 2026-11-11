import { type KeyboardEvent, useMemo, useState } from "react";
import { deriveSawtooth, type SawRow, type SawYLabel, sawTipAt } from "../derive/sawtooth";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const MUTED = { color: "var(--color-neutral-600)", fontSize: 9 } as const;

function Chart({ row, yLabels }: { row: SawRow; yLabels: readonly SawYLabel[] }) {
  const tip = useTip();
  const [cur, setCur] = useState<number | null>(null);
  const label = `${row.region}: ${row.count}; ${row.note}`;

  const onMove = (e: { clientX: number; clientY: number; currentTarget: Element }) => {
    const box = e.currentTarget.getBoundingClientRect();
    const x = box.width > 0 ? ((e.clientX - box.left) / box.width) * 1000 : 0;
    tip.show(e, sawTipAt(row, x));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (row.points.length === 0) return;
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    e.preventDefault();
    const next = Math.min(Math.max((cur ?? -step) + step, 0), row.points.length - 1);
    setCur(next);
    const p = row.points[next];
    const box = e.currentTarget.getBoundingClientRect();
    if (p) tip.show({ clientX: box.left + (p.x / 1000) * box.width, clientY: box.top }, p);
  };

  return (
    <div
      role="img"
      aria-label={label}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: the arrow keys read the checks one by one
      tabIndex={0}
      onMouseMove={onMove}
      onMouseLeave={tip.hide}
      onBlur={() => {
        setCur(null);
        tip.hide();
      }}
      onKeyDown={onKey}
      style={{
        position: "relative",
        height: 56,
        borderLeft: "1px solid var(--color-neutral-900)",
        borderBottom: "1px solid var(--color-neutral-900)",
      }}
    >
      {yLabels.map((y) => (
        <span
          key={y.label}
          aria-hidden="true"
          style={{
            ...MUTED,
            position: "absolute",
            left: 3,
            top: `${y.top}%`,
            transform: "translateY(-100%)",
            lineHeight: 1,
          }}
        >
          {y.label}
        </span>
      ))}
      <svg
        aria-hidden="true"
        viewBox="0 0 1000 100"
        preserveAspectRatio="none"
        width="100%"
        height="100%"
        style={{ display: "block" }}
      >
        <path
          d={row.path}
          fill="none"
          stroke="var(--color-accent-2-400)"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

export function SawtoothView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveSawtooth(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label="When could a block start?" />;
  return (
    <SectionShell name={result.section} label={result.heading}>
      <h2 style={{ fontSize: 15, margin: 0, fontWeight: 600 }}>{result.heading}</h2>
      <p style={{ color: "var(--color-neutral-400)", fontSize: 12, margin: "6px 0 14px" }}>
        {result.intro}
      </p>
      {result.rows.map((row) => (
        <div
          key={row.region}
          style={{
            display: "grid",
            gridTemplateColumns: "110px 1fr 200px",
            gap: 12,
            alignItems: "center",
            marginBottom: 10,
          }}
        >
          <div style={{ fontSize: 11 }}>
            <div>{row.region}</div>
            <div style={MUTED}>{row.count}</div>
          </div>
          <Chart row={row} yLabels={result.yLabels} />
          <div style={{ fontSize: 11, color: "var(--color-neutral-300)" }}>{row.note}</div>
        </div>
      ))}
    </SectionShell>
  );
}
