// The 1 global tooltip (spec 0006 section 11.6): a shadcn Tooltip whose anchor is a 1 px element at
// the cursor, 14 px right and 14 px down, flipped by Radix when it would pass the right edge.

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../../components/ui/tooltip";

export type TipText = { readonly title: string; readonly lines: readonly string[] };

type TipApi = {
  show(e: { clientX: number; clientY: number }, text: TipText): void;
  hide(): void;
};

const Ctx = createContext<TipApi>({ show: () => {}, hide: () => {} });

/** Sections call `show` on mouse move and `hide` on mouse leave. */
export function useTip(): TipApi {
  return useContext(Ctx);
}

/** Pair each line with a key that stays unique when 2 lines have the same text. */
function keyed(lines: readonly string[]): [string, string][] {
  const seen = new Map<string, number>();
  return lines.map((l) => {
    const n = seen.get(l) ?? 0;
    seen.set(l, n + 1);
    return [`${n}:${l}`, l];
  });
}

export function GlobalTooltip({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: TipText } | null>(null);
  // A hide waits 1 frame, and a show in that frame cancels it. A pointer that leaves 1 cell and
  // enters the next one (across a gap) then never unmounts the tooltip.
  const pending = useRef<number | null>(null);
  const cancel = useCallback(() => {
    if (pending.current !== null) cancelAnimationFrame(pending.current);
    pending.current = null;
  }, []);
  const show = useCallback<TipApi["show"]>(
    (e, text) => {
      cancel();
      setTip({ x: e.clientX, y: e.clientY, text });
    },
    [cancel],
  );
  const hide = useCallback(() => {
    cancel();
    pending.current = requestAnimationFrame(() => {
      pending.current = null;
      setTip(null);
    });
  }, [cancel]);
  useEffect(() => cancel, [cancel]);
  const api = useMemo(() => ({ show, hide }), [show, hide]);
  return (
    <TooltipProvider>
      <Ctx.Provider value={api}>
        {children}
        <Tooltip open={tip !== null}>
          <TooltipTrigger asChild>
            <span
              aria-hidden="true"
              style={{
                position: "fixed",
                left: tip?.x ?? 0,
                top: tip?.y ?? 0,
                width: 1,
                height: 1,
                // The anchor sits at the cursor: with pointer events it would take the hover from
                // the cell, and the cell would hide the tooltip.
                pointerEvents: "none",
              }}
            />
          </TooltipTrigger>
          <TooltipContent side="bottom" align="start" sideOffset={14} alignOffset={14}>
            <div style={{ fontWeight: 600 }}>{tip?.text.title}</div>
            {keyed(tip?.text.lines ?? []).map(([key, l]) => (
              <div key={key}>{l}</div>
            ))}
          </TooltipContent>
        </Tooltip>
      </Ctx.Provider>
    </TooltipProvider>
  );
}
