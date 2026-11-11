import { type CSSProperties, type ReactNode, useId, useMemo, useState } from "react";
import { AnswerIcon, type AnswerIconName } from "../../components/AnswerIcon";
import {
  type CellFill,
  deriveNow,
  NOW_TITLE,
  type NowBlockRow,
  type NowCell,
  type NowKind,
  type NowPlaceRow,
  type NowRow,
} from "../derive/now";
import type { SectionProps } from "./props";
import { SectionShell } from "./shell";
import { useTip } from "./tooltip";

// Spec 0006 section 11.9 gives the design. The view draws what
// `deriveNow` returns and computes no number and no caption (section 10.2). Inline styles set no
// `all` and no `outline`, so the stylesheet's `:focus-visible` ring stays on the row buttons.

const COLS = "190px 150px minmax(0,1fr) 230px 24px";
const GRID: CSSProperties = {
  display: "grid",
  gridTemplateColumns: COLS,
  gap: 18,
  alignItems: "center",
};
const MUTED = "var(--color-neutral-500)";
const HOVER_BG = "color-mix(in oklch, var(--color-surface) 70%, transparent)";
const OPEN_BG = "color-mix(in oklch, var(--color-surface) 50%, transparent)";
const RULE = "color-mix(in oklch, var(--color-neutral-900) 60%, transparent)";
const HATCH =
  "repeating-linear-gradient(45deg, var(--color-neutral-600) 0 1.5px, transparent 1.5px 4px)";

/** The colour of each answer icon; the icon is unique to its kind (section 11.9, design spec). */
const ICON_COLOR: Readonly<Record<AnswerIconName, string>> = {
  "check-circle": "var(--color-accent-300)",
  clock: "var(--color-neutral-200)",
  "calendar-blank": "var(--color-accent-2-300)",
  "x-circle": "var(--color-neutral-300)",
  "lock-simple": "var(--color-neutral-400)",
  "minus-circle": "var(--color-neutral-600)",
};

/** "ph-fill ph-check-circle" (the derive icon class) to the name of the bundled icon. */
function iconName(cls: string): AnswerIconName {
  const name = (cls.split(" ").pop() ?? "").replace(/^ph-/, "");
  return name in ICON_COLOR ? (name as AnswerIconName) : "minus-circle";
}

function IconWord({ icon, size, children }: { icon: string; size: number; children: ReactNode }) {
  const name = iconName(icon);
  const color = ICON_COLOR[name];
  return (
    <span style={{ display: "flex", alignItems: "center", gap: 6, color }}>
      <AnswerIcon name={name} size={size} color={color} />
      {children}
    </span>
  );
}

const FILL: Readonly<Record<CellFill, string>> = {
  all: "var(--color-accent-400)",
  some: "var(--color-accent-400)",
  no: "var(--color-neutral-600)",
  hatch: HATCH,
  none: "transparent",
};

/** The nearest cell to a pointer x, from the real cell boxes. */
function nearest(box: Element, x: number): number {
  let best = -1;
  let dist = Number.POSITIVE_INFINITY;
  for (let i = 0; i < box.children.length; i++) {
    const b = box.children[i]?.getBoundingClientRect();
    if (b === undefined) continue;
    const d = x < b.left ? b.left - x : Math.max(0, x - b.right);
    if (d < dist) {
      dist = d;
      best = i;
    }
  }
  return best;
}

/** A strip: 1 cell per round or day, height as the encoding, tooltips on pointer and on focus. */
function Strip({
  cells,
  marks,
  label,
}: {
  cells: readonly NowCell[];
  marks: readonly { l: number; label: string }[];
  label: string;
}) {
  const tip = useTip();
  const [cur, setCur] = useState(-1);
  const showCell = (el: Element, k: number) => {
    const c = cells[k];
    const b = el.children[k]?.getBoundingClientRect();
    if (c === undefined || b === undefined) return;
    setCur(k);
    tip.show({ clientX: b.left + b.width / 2, clientY: b.bottom - 14 }, c.tip);
  };
  return (
    <span style={{ position: "relative", display: "block" }}>
      {marks.map((m) => (
        <span
          key={m.label}
          title={m.label}
          aria-hidden="true"
          style={{
            position: "absolute",
            left: `${m.l}%`,
            top: -2,
            bottom: -2,
            borderLeft: "1px dashed var(--color-neutral-400)",
            pointerEvents: "none",
          }}
        />
      ))}
      {/* biome-ignore lint/a11y/useSemanticElements: a strip is 1 focus stop; the arrow keys move between its cells and the tooltip names each one */}
      <span
        // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard focus shows the tooltip (spec 0006 section 11.6)
        tabIndex={0}
        role="group"
        aria-label={label}
        data-strip=""
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${cells.length}, minmax(0,1fr))`,
          gap: 1,
          height: 16,
          alignItems: "end",
          borderRadius: 2,
        }}
        onMouseMove={(e) => {
          const k = nearest(e.currentTarget, e.clientX);
          const c = cells[k];
          if (c === undefined) tip.hide();
          else tip.show(e, c.tip);
        }}
        onMouseLeave={tip.hide}
        onFocus={(e) => showCell(e.currentTarget, cells.length - 1)}
        onBlur={() => {
          setCur(-1);
          tip.hide();
        }}
        onKeyDown={(e) => {
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
          e.preventDefault();
          const next = Math.min(
            cells.length - 1,
            Math.max(0, (cur < 0 ? cells.length - 1 : cur) + (e.key === "ArrowLeft" ? -1 : 1)),
          );
          showCell(e.currentTarget, next);
        }}
      >
        {cells.map((c, k) => (
          <i
            // biome-ignore lint/suspicious/noArrayIndexKey: the cells of a strip are positional
            key={k}
            style={{
              display: "block",
              height: c.h,
              minHeight: 0,
              borderRadius: 1,
              background: FILL[c.fill],
            }}
          />
        ))}
      </span>
    </span>
  );
}

function Pill({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        flex: "none",
        width: 40,
        padding: "2px 5px",
        fontSize: 9,
        fontWeight: 600,
        letterSpacing: "0.06em",
        textAlign: "center",
        borderRadius: 3,
        background: "var(--color-neutral-900)",
        color: "var(--color-neutral-300)",
      }}
    >
      {children}
    </span>
  );
}

function Tag({ color, border, children }: { color: string; border: string; children: ReactNode }) {
  return (
    <span
      style={{
        fontSize: 9,
        fontWeight: 600,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        color,
        border: `1px solid ${border}`,
        borderRadius: 3,
        padding: "1px 4px",
      }}
    >
      {children}
    </span>
  );
}

const RIGHT: CSSProperties = { fontSize: 12, color: "var(--color-neutral-300)", lineHeight: 1.3 };

function PlaceLine({ p, row, marks }: { p: NowPlaceRow; row: NowRow; marks: NowRow["marks"] }) {
  return (
    <div style={{ ...GRID, padding: "5px 0", borderTop: `1px solid ${RULE}` }}>
      <span style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 6, minWidth: 0 }}>
        <Pill>{p.cloud}</Pill>
        <span style={{ minWidth: 0, lineHeight: 1.2 }}>
          <span
            style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 500 }}
          >
            {p.region}
            {p.first && (
              <Tag color="var(--color-accent-300)" border="var(--color-accent-700)">
                try first
              </Tag>
            )}
          </span>
          <span style={{ display: "block", fontSize: 10, color: MUTED }}>{p.sub}</span>
        </span>
      </span>
      <span style={{ fontSize: 12, fontWeight: 500 }}>
        <IconWord icon={p.nowIcon} size={14}>
          {p.nowTxt}
        </IconWord>
      </span>
      <Strip cells={p.cells} marks={marks} label={`${row.name} ${p.cloud} ${p.region} rounds`} />
      <span style={RIGHT}>
        <b style={{ fontWeight: 600, color: `var(${p.rateTone})` }}>{p.rate}</b>{" "}
        <span style={{ color: MUTED }}>{p.rateTxt}</span>
        <span style={{ display: "block", fontSize: 11, color: MUTED }}>{p.last}</span>
      </span>
      <span />
    </div>
  );
}

function BlockLine({ b }: { b: NowBlockRow }) {
  return (
    <div style={{ ...GRID, padding: "5px 0", borderTop: `1px solid ${RULE}` }}>
      <span style={{ display: "flex", alignItems: "center", gap: 8, paddingLeft: 6 }}>
        <Pill>AWS</Pill>
        <span style={{ lineHeight: 1.2 }}>
          <span
            style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 500 }}
          >
            {b.region}
            {b.first && (
              <Tag color="var(--color-accent-2-300)" border="var(--color-accent-2-700)">
                soonest
              </Tag>
            )}
          </span>
          <span style={{ display: "block", fontSize: 10, color: MUTED }}>
            Capacity Block · booked ahead
          </span>
        </span>
      </span>
      <span style={{ fontSize: 12, fontWeight: 500 }}>
        <IconWord icon={b.icon} size={14}>
          {b.word}
        </IconWord>
      </span>
      <span style={{ fontSize: 12, lineHeight: 1.35, color: "var(--color-neutral-200)" }}>
        {b.offer}
      </span>
      <span style={RIGHT}>
        <b style={{ fontWeight: 600, color: `var(${b.rateTone})` }}>{b.rate}</b>{" "}
        <span style={{ color: MUTED }}>{b.rateTxt}</span>
        {b.lead !== "" && (
          <span style={{ display: "block", fontSize: 11, color: MUTED }}>{b.lead}</span>
        )}
      </span>
      <span />
    </div>
  );
}

const KEY: { label: string; bg: string }[] = [
  { label: "got all we asked", bg: "var(--color-accent-400)" },
  {
    label: "got some",
    bg: "linear-gradient(to top, var(--color-accent-400) 50%, transparent 50%)",
  },
  {
    label: "cloud said no capacity",
    bg: "linear-gradient(to top, var(--color-neutral-600) 2px, transparent 2px)",
  },
  { label: "our quota blocked it", bg: HATCH },
];

function Detail({ row, id }: { row: NowRow; id: string }) {
  return (
    <div
      id={id}
      style={{
        background: OPEN_BG,
        borderRadius: "0 0 var(--radius-md) var(--radius-md)",
        padding: "10px 8px 14px",
        marginBottom: 6,
      }}
    >
      <div
        style={{
          ...GRID,
          alignItems: "end",
          fontSize: 11,
          color: MUTED,
          paddingBottom: 6,
        }}
      >
        <span style={{ paddingLeft: 6 }}>Every place we asked</span>
        <span>Latest round</span>
        <span style={{ position: "relative", display: "block", height: 16 }}>
          {row.axis.map((t) => (
            <span
              key={t.label}
              style={{
                position: "absolute",
                left: `${t.l}%`,
                bottom: 0,
                paddingLeft: 3,
                whiteSpace: "nowrap",
                borderLeft: "1px solid var(--color-neutral-800)",
                lineHeight: 1,
              }}
            >
              {t.label}
            </span>
          ))}
        </span>
        <span>Rounds with a yes · last yes</span>
        <span />
      </div>
      {row.places.map((p) => (
        <PlaceLine key={`${p.cloud} ${p.region}`} p={p} row={row} marks={row.marks} />
      ))}
      {row.blocks.map((b) => (
        <BlockLine key={b.region} b={b} />
      ))}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "6px 16px",
          fontSize: 11,
          lineHeight: 1.5,
          color: MUTED,
          padding: "10px 6px 0",
        }}
      >
        {KEY.map((k) => (
          <span key={k.label} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
            <i
              aria-hidden="true"
              style={{
                display: "inline-block",
                width: 7,
                height: 14,
                borderRadius: 1,
                background: k.bg,
              }}
            />
            {k.label}
          </span>
        ))}
        <span>blank = not asked</span>
        {row.note !== "" && <span style={{ color: "var(--color-neutral-600)" }}>{row.note}</span>}
      </div>
    </div>
  );
}

const KIND_COLOR: Readonly<Record<NowKind, string>> = {
  yes: "var(--color-accent-300)",
  soon: "var(--color-neutral-200)",
  book: "var(--color-accent-2-300)",
  no: "var(--color-neutral-300)",
  quota: "var(--color-neutral-400)",
  none: "var(--color-neutral-600)",
};

function Machine({ row, focus, onToggle }: { row: NowRow; focus: boolean; onToggle: () => void }) {
  const [hover, setHover] = useState(false);
  const id = useId();
  return (
    <div>
      <button
        type="button"
        aria-expanded={row.open}
        aria-controls={row.open ? id : undefined}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onClick={onToggle}
        style={{
          ...GRID,
          width: "100%",
          margin: 0,
          padding: "10px 8px",
          border: 0,
          borderTop: "1px solid var(--color-neutral-900)",
          borderRadius: "var(--radius-sm)",
          background: row.open ? OPEN_BG : hover ? HOVER_BG : "transparent",
          font: "inherit",
          color: "inherit",
          textAlign: "left",
          cursor: "pointer",
        }}
      >
        <span style={{ display: "block", minWidth: 0, lineHeight: 1.25 }}>
          <span
            style={{
              display: "block",
              fontSize: 15,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              color: focus ? "var(--color-accent-300)" : "var(--color-text)",
            }}
          >
            {row.name}
          </span>
          <span style={{ display: "block", fontSize: 11, color: MUTED, marginTop: 2 }}>
            {row.shape}
          </span>
        </span>
        <span style={{ display: "block", minWidth: 0, lineHeight: 1.25 }}>
          <span
            style={{ display: "flex", fontSize: 15, fontWeight: 600, color: KIND_COLOR[row.kind] }}
          >
            <IconWord icon={row.icon} size={17}>
              {row.word}
            </IconWord>
          </span>
          <span style={{ display: "block", fontSize: 11, color: MUTED, marginTop: 2 }}>
            {row.qual}
          </span>
        </span>
        <span style={{ display: "block", minWidth: 0, lineHeight: 1.35 }}>
          <span style={{ display: "block", fontSize: 13, color: "var(--color-neutral-100)" }}>
            {row.plan}
          </span>
          {row.planSub !== "" && (
            <span style={{ display: "block", fontSize: 11, color: MUTED, marginTop: 2 }}>
              {row.planSub}
            </span>
          )}
          {row.hasChips && (
            <span style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 7 }}>
              {row.chips.map((c, k) => (
                <span
                  key={c.label}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    fontSize: 11,
                    fontWeight: 500,
                    padding: "3px 8px",
                    borderRadius: 999,
                    border: `1px solid ${k === 0 ? "var(--color-accent-600)" : "var(--color-neutral-800)"}`,
                    background: k === 0 ? "var(--color-accent-900)" : "transparent",
                    color: k === 0 ? "var(--color-accent-200)" : "var(--color-neutral-300)",
                  }}
                >
                  {c.label}
                  <span
                    style={{
                      fontWeight: 400,
                      color: k === 0 ? "var(--color-accent-400)" : MUTED,
                    }}
                  >
                    {c.rate}
                  </span>
                </span>
              ))}
            </span>
          )}
        </span>
        <span style={{ display: "block", minWidth: 0, lineHeight: 1.25 }}>
          <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <b
              style={{
                fontFamily: "var(--font-heading)",
                fontWeight: 500,
                fontSize: 18,
                letterSpacing: "-0.02em",
                color: `var(${row.depTone})`,
              }}
            >
              {row.depBig}
            </b>
            <span style={{ fontSize: 11, color: MUTED }}>{row.depTxt}</span>
          </span>
          <span
            aria-hidden="true"
            style={{
              position: "relative",
              display: "block",
              height: 4,
              margin: "6px 0 5px",
              borderRadius: 2,
              background: "var(--color-neutral-900)",
              overflow: "hidden",
            }}
          >
            <i
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: `${row.depW}%`,
                background: `var(${row.depBar})`,
              }}
            />
          </span>
          <span style={{ display: "block", fontSize: 11, color: "var(--color-neutral-400)" }}>
            {row.depWait}
          </span>
        </span>
        <span aria-hidden="true" style={{ fontSize: 12, color: MUTED, textAlign: "center" }}>
          {row.chev}
        </span>
      </button>
      {row.open && <Detail row={row} id={id} />}
    </div>
  );
}

export function NowView({ data, state, setState }: SectionProps) {
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const result = useMemo(() => deriveNow(data, state, open), [data, state, open]);
  if (result.empty) return <SectionShell name="now" label={NOW_TITLE} />;
  // A click toggles the row and sets the focus machine through the state callback (sections 10.2 and 11.9).
  const toggle = (name: string) => {
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    if (name !== state.gpu) setState?.({ ...state, gpu: name });
  };
  return (
    <SectionShell name="now" label={NOW_TITLE}>
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <div style={{ minWidth: 0 }}>
          <h2
            style={{
              margin: 0,
              fontSize: 18,
              fontWeight: 500,
              letterSpacing: "-0.01em",
              lineHeight: 1.3,
            }}
          >
            {result.title}
          </h2>
          <p
            style={{
              margin: "4px 0 0",
              fontSize: 12,
              lineHeight: 1.5,
              color: MUTED,
              textWrap: "pretty",
            }}
          >
            Latest round <span style={{ color: "var(--color-neutral-300)" }}>{result.asOf}</span>.
            Every {result.cadence} we ask every place for a real machine; a yes means the cloud
            handed it over. Click a machine for every place and round.
          </p>
        </div>
        <div>
          <div
            style={{
              ...GRID,
              fontSize: 11,
              color: MUTED,
              padding: "0 8px 8px",
              borderBottom: "1px solid var(--color-neutral-800)",
            }}
          >
            <span>Machine</span>
            <span>Right now</span>
            <span>Where · what to do</span>
            <span>Over {result.winLabel}</span>
            <span />
          </div>
          {result.groups.map((g) => (
            <div key={g.name}>
              {g.show && (
                <div
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "baseline",
                    padding: "14px 8px 4px",
                  }}
                >
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      letterSpacing: "0.07em",
                      textTransform: "uppercase",
                      color: "var(--color-neutral-400)",
                    }}
                  >
                    {g.name}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--color-neutral-600)" }}>{g.sub}</span>
                </div>
              )}
              {g.rows.map((r) => (
                <Machine
                  key={r.name}
                  row={r}
                  focus={r.name === state.gpu}
                  onToggle={() => toggle(r.name)}
                />
              ))}
            </div>
          ))}
        </div>
        <p
          style={{
            margin: 0,
            maxWidth: 900,
            fontSize: 12,
            lineHeight: 1.5,
            color: MUTED,
            textWrap: "pretty",
          }}
        >
          A place is a cloud plus a Region. AWS Regions combine every zone we asked into one answer,
          the best zone. A Capacity Block is bought ahead; it counts as “now” only when it starts
          within 1 hour. Nothing here is a forecast or a listing.
        </p>
      </div>
    </SectionShell>
  );
}
