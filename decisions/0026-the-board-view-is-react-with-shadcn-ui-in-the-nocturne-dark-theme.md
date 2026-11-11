# 0026. The board view is React with shadcn/ui, themed with the Nocturne tokens, dark only

- Status: accepted (decision, 2026-10-29)
- Date: 2026-10-29

## Context

[ADR 0004](0004-dashboard-stack.md) chose a static TypeScript page with no UI framework and said "a later ADR can add [React] if the page grows". It has grown. The new board is 1 page with about 12 sections, 3 kinds of input (a native select, a horizon button group, a pager), a global tooltip, drill-down rows, and state in the URL hash. The design is a high-fidelity mock-up that uses the Nocturne CSS variables (`--color-*`, `--radius-*`, `--shadow-*`, `--font-*`). It is dark only.

[Spec 0006](../spec/0006-dashboard-spec.md) states the product behavior. This ADR states only the view stack. The data path is in [ADR 0027](0027-the-board-reads-windows-from-a-duckdb-request-handler.md).

## Decision

1. **Framework.** The view is React with TypeScript, built by Vite with `@vitejs/plugin-react` ([plugin docs](https://github.com/vitejs/vite-plugin-react/tree/main/packages/plugin-react)).
2. **Components.** shadcn/ui, only where a component matches the design. The CLI copies the source of each component into `web/src/components/ui/`, so the repo owns the code and no component library is a runtime dependency ([shadcn/ui introduction](https://ui.shadcn.com/docs)). The board needs 1 component: Tooltip (1 global tooltip). The Tier and Focus selectors are native `<select>` elements with the design's input class. The horizon control is a plain button group with `role="radiogroup"`; a shadcn ToggleGroup is allowed in its place only with every animation class and every custom focus-ring class removed. The design is final: it specifies native selects, no animation, and the design system's own `:focus-visible`. Add another shadcn component only when a section needs it and it matches the design. [Spec 0006](../spec/0006-dashboard-spec.md) section 11.7 is the source of truth for the controls, their options, and their behavior, and section 11.6 for the tooltip and the focus ring.
3. **ReUI where it fits.** [ReUI](https://reui.io) publishes components that are installed into the same folder through the shadcn CLI. Use a ReUI component only when shadcn has no equal one and the section needs it. Each use is named in the pull request. A ReUI component is copied source too, so it adds no package.
4. **Tailwind only as shadcn needs it.** shadcn components are styled with Tailwind classes and CSS variables ([shadcn/ui, theming](https://ui.shadcn.com/docs/theming)). Tailwind is used through its Vite plugin ([Tailwind CSS, Vite install](https://tailwindcss.com/docs/installation/using-vite)). Board sections are written with CSS variables and plain CSS (the design gives exact pixel values), not with Tailwind utility classes. Tailwind classes appear in `web/src/components/ui/` only.
5. **Font.** Inter is self-hosted with `@fontsource/inter` ([Fontsource, Inter](https://fontsource.org/fonts/inter)), weights 400, 500, and 600 imported by file (`@fontsource/inter/400.css`, and so on). The Google Fonts `@import` of the design `styles.css` is removed. The page makes no request to an external host at view time (spec 0006 section 1 item 6, decision R4).
6. **Theme.** Dark only. The Nocturne variables stay the source of truth. The shadcn variables are aliases of them (`--primary: var(--color-accent-500)`), declared once in the board stylesheet. There is no `.dark` class switch and no theme toggle (spec 0006 decision R3).
7. **Layer rule.** The derive layer (`web/src/board/derive/`) imports no React, no DOM API, and no component library. Only `web/src/board/view/` and `web/src/components/ui/` import React. Two checks enforce this (spec 0006 section 10.3): a Biome `noRestrictedImports` override for `src/board/derive/**` ([Biome, noRestrictedImports](https://biomejs.dev/linter/rules/no-restricted-imports/)) forbids the imports, and a `tsconfig.json` in that folder with a `lib` that has no `DOM` makes a DOM global such as `document` a type error. The Biome rule cannot see a global, so the second check is needed.

### Version pins

Exact versions, no ranges, read from the npm registry on 2026-10-29 (`npm view <package> version`; [registry](https://www.npmjs.com)).

| Package | Pin | Kind |
|---|---|---|
| `react` | 19.3.1 | dependency |
| `react-dom` | 19.3.1 | dependency |
| `@vitejs/plugin-react` | 6.1.2 | dev dependency |
| `tailwindcss` | 4.3.3 | dev dependency |
| `@tailwindcss/vite` | 4.3.3 | dev dependency |
| `shadcn` (CLI) | 4.23.0 | dev dependency |
| `@fontsource/inter` | 5.3.0 | dependency |
| `@radix-ui/react-tooltip` | 1.4.1 | dependency |
| `class-variance-authority` | 0.7.1 | dependency (shadcn helper) |
| `clsx` | 2.1.1 | dependency (shadcn helper) |
| `tailwind-merge` | 3.7.0 | dependency (shadcn helper) |

These pins equal `web/package.json` on main since the board shell merged. If `web/package.json` and this table ever differ, `web/package.json` is right and this table follows it. If the CLI of that version generates imports from the single `radix-ui` package (registry version 1.8.1) in place of `@radix-ui/react-tooltip`, the change pins that one package instead and records the change in its pull request. Either way the pins are exact. The Vite pin (8.3.4) and the Vitest pin (5.0.3) are as in `web/package.json`.

### Nocturne to shadcn variables

Source: the Nocturne `styles.css` of the design system, and the variable list in [shadcn/ui, theming](https://ui.shadcn.com/docs/theming). The shadcn name is on the left of an alias; the Nocturne value is the right side. Where the design names a use, the use column says it.

| shadcn variable | Nocturne source | Use |
|---|---|---|
| `--background` | `--color-bg` | board ground |
| `--foreground` | `--color-text` | body text |
| `--card` | `--color-surface` | chips, cards |
| `--card-foreground` | `--color-text` | |
| `--popover` | `--color-surface` | Tooltip surface, if shadcn's Tooltip reads it |
| `--popover-foreground` | `--color-text` | |
| `--primary` | `--color-accent-500` | filled accents, bar fill |
| `--primary-foreground` | `--color-bg` | text on a filled accent |
| `--secondary` | `--color-neutral-900` | |
| `--secondary-foreground` | `--color-neutral-200` | |
| `--muted` | `--color-neutral-900` | tracks |
| `--muted-foreground` | `--color-neutral-500` | notes, intros |
| `--accent` | `--color-accent-900` | selected horizon button |
| `--accent-foreground` | `--color-accent-200` | text on the selected horizon button |
| `--destructive` | `--color-neutral-400` | no Nocturne source; unused by the Tooltip, set inside the palette so no off-palette color exists |
| `--border` | `--color-neutral-800` | 1 px borders |
| `--input` | `--color-neutral-800` | input border, as the design's input class |
| `--ring` | `--color-accent-500` | focus ring value; the design's `:focus-visible` rule draws the ring |
| `--radius` | `--radius-md` (8 px) | shadcn derives its small and large radii from this value |

Nocturne variables with no shadcn peer (`--color-accent-2-*` for Capacity Block encodings, `--color-neutral-100` to `-700`, `--color-accent-100` to `-800`, `--radius-sm`, `--radius-lg`, `--shadow-*`, `--font-*`, `--space-*`) stay as they are and are used directly by board CSS. The deck variables (`--color-section*`) are not copied; the board does not use them. The global tooltip uses `--color-neutral-100` as ground and `--color-bg` as text, as the design says, so the copied Tooltip is edited to those variables.

## Alternatives considered

- **Keep ADR 0004: SVG and TypeScript, no framework.** Worked for 9 same-shape grids. The board has 12 different sections with shared state (horizon, page, tier, focus machine, an open drill-down row). Hand-written DOM updates for that are more code than React, and the design prototype is already a component tree. Rejected.
- **Another React component library (MUI, Chakra, Mantine).** Each ships its own theme system and runtime styling, and each is a runtime dependency that the repo cannot edit. Mapping the Nocturne pixel values onto them costs more than mapping them onto shadcn's plain CSS variables. Rejected.
- **Radix primitives with hand-written CSS and no shadcn.** Smallest dependency set, but the repo then writes and tests the Tooltip wiring itself, and shadcn already supplies it as editable source. Kept as the fallback in the cost to reverse.
- **Tailwind for all of the board.** The design gives exact pixel values and uses CSS variables, so utility classes add a translation step per rule. Rejected beyond what shadcn needs.
- **A light theme or a theme toggle.** The design is dark only and final. A light theme is a later change.
- **Vue or Svelte.** The existing logic is JavaScript for React-style rendering, and shadcn/ui and ReUI target React. Rejected.

## Consequences

- `web/package.json` grows by the packages above. The npm install of a fresh clone is larger. Pins are exact, so `npm ci` is repeatable.
- The repo owns the code under `web/src/components/ui/`. Upgrading shadcn does not change it unless someone re-runs the CLI. Biome lints it like other code.
- Tailwind adds a build step (the Vite plugin) and 1 CSS entry file. Board sections do not use it.
- The page has no request to an external host at view time, because Inter is local.
- The view is dark only. Contrast is the design's responsibility, not this ADR's.
- This ADR changes item 1 of the decision of [ADR 0004](0004-dashboard-stack.md) ("uses no UI framework and no chart library"). Charts stay hand-drawn SVG inside React components; no chart library is added.

## Cost to reverse

Low, by design. The derive layer has no React import, so a change of view stack rewrites only `web/src/board/view/` and `web/src/components/ui/`, and the golden tests of the derive layer still pass. The Nocturne variables are independent of shadcn, so removing shadcn removes only the alias block. The data client and the server (ADR 0027) do not change.
