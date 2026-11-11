import { type CSSProperties, type KeyboardEvent, useMemo, useState } from "react";
import { deriveTrend, type TrendRow } from "../derive/trend";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

const LABEL = "Better or worse?";
const MUTED: CSSProperties = { color: "var(--color-neutral-500)", fontSize: 10 };

type Bands = readonly { readonly l: number; readonly w: number }[];
type Marks = readonly { readonly l: number; readonly label: string }[];

function Backdrop({ weekend, starts }: { weekend: Bands; starts: Marks }) {
  return (
    <>
      {weekend.map((b) => (
        <span
          key={`w${b.l}`}
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${b.l}%`,
            width: `${b.w}%`,
            background: "color-mix(in srgb, var(--color-neutral-900) 70%, transparent)",
          }}
        />
      ))}
      {starts.map((s) => (
        <span
          key={`s${s.l}`}
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            left: `${s.l}%`,
            borderLeft: "1px dashed var(--color-neutral-500)",
          }}
        />
      ))}
    </>
  );
}

function Spark({ row, weekend, starts }: { row: TrendRow; weekend: Bands; starts: Marks }) {
  const tip = useTip();
  const [cur, setCur] = useState<number | null>(null);
  const n = row.tips.length;
  const stroke = row.block ? "var(--color-accent-2-400)" : "var(--color-accent-400)";
  const fill = row.block
    ? "color-mix(in srgb, var(--color-accent-2-600) 25%, transparent)"
    : "color-mix(in srgb, var(--color-accent-500) 22%, transparent)";

  const showDay = (i: number, at: { clientX: number; clientY: number }) => {
    const t = row.tips[i];
    if (t) tip.show(at, t);
  };
  const onMove = (e: { clientX: number; clientY: number; currentTarget: Element }) => {
    const box = e.currentTarget.getBoundingClientRect();
    const x = box.width > 0 ? (e.clientX - box.left) / box.width : 0;
    showDay(Math.min(Math.max(Math.floor(x * n), 0), n - 1), e);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (step === 0 || n === 0) return;
    e.preventDefault();
    const next = Math.min(Math.max((cur ?? -step) + step, 0), n - 1);
    setCur(next);
    const box = e.currentTarget.getBoundingClientRect();
    showDay(next, { clientX: box.left + ((next + 0.5) / n) * box.width, clientY: box.top });
  };
  const label = `${row.name}: ${row.sub}, previous ${row.a}, this ${row.b}, change ${row.d}`;

  return (
    <div
      role="img"
      aria-label={label}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: the arrow keys read the days one by one
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
        height: 34,
        borderBottom: "1px solid var(--color-neutral-900)",
      }}
    >
      <Backdrop weekend={weekend} starts={starts} />
      <svg
        aria-hidden="true"
        viewBox="0 0 1000 100"
        preserveAspectRatio="none"
        width="100%"
        height="100%"
        style={{ display: "block", position: "relative" }}
      >
        <path d={row.area} fill={fill} stroke="none" />
        <path
          d={row.path}
          fill="none"
          stroke={stroke}
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {row.dot === undefined ? null : (
        <span
          data-testid="trend-dot"
          aria-hidden="true"
          style={{
            position: "absolute",
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: stroke,
            left: "50%",
            top: `${100 - row.dot}%`,
            transform: "translate(-50%, -50%)",
          }}
        />
      )}
    </div>
  );
}

export function TrendView({ data, state }: SectionProps) {
  const result = useMemo(() => deriveTrend(data, state), [data, state]);
  if (result.empty) return <SectionShell name={result.section} label={LABEL} />;
  const grid: CSSProperties = {
    display: "grid",
    gridTemplateColumns: "130px 1fr 250px",
    gap: "6px 16px",
    alignItems: "center",
  };
  const cols: CSSProperties = { display: "grid", gridTemplateColumns: "1fr 1fr 1fr" };
  return (
    <SectionShell name={result.section} label={LABEL}>
      <h2 style={{ fontSize: 15, margin: 0, fontWeight: 600 }}>{LABEL}</h2>
      <p style={{ color: "var(--color-neutral-400)", fontSize: 12, margin: "6px 0 14px" }}>
        {result.intro}
      </p>
      <div style={grid}>
        <div />
        <div style={{ ...MUTED, position: "relative", height: 14 }}>
          {result.axis.map((t) => (
            <span key={t.label} style={{ position: "absolute", left: `${t.l}%` }}>
              {t.label}
            </span>
          ))}
        </div>
        <div style={{ ...cols, ...MUTED }}>
          <span>{result.heads.prev}</span>
          <span>{result.heads.this}</span>
          <span>{result.heads.change}</span>
        </div>
        {result.rows.map((row) => (
          <div key={row.name} style={{ display: "contents" }}>
            <div>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: row.name === state.gpu ? "var(--color-accent-300)" : "var(--color-text)",
                }}
              >
                {row.name}
              </div>
              <div style={MUTED}>{row.sub}</div>
            </div>
            <Spark row={row} weekend={result.weekend} starts={result.starts} />
            <div style={{ ...cols, fontSize: 13 }}>
              <span>{row.a}</span>
              <span>{row.b}</span>
              <span style={{ fontWeight: 600, color: `var(${row.tone})` }}>{row.d}</span>
            </div>
          </div>
        ))}
      </div>
      <p style={{ ...MUTED, margin: "8px 0 0" }}>{result.note}</p>
    </SectionShell>
  );
}
