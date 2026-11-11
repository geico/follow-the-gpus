# 0004. The dashboard is a static TypeScript page fed by a DuckDB CLI snapshot

- Status: accepted, superseded in part by [ADR 0026](0026-the-board-view-is-react-with-shadcn-ui-in-the-nocturne-dark-theme.md) (the view layer: React and shadcn/ui replace "no UI framework" and the "React" rejection) and [ADR 0027](0027-the-board-reads-windows-from-a-duckdb-request-handler.md) (the data path: a read-only DuckDB request handler and a static export replace the CLI snapshot step, decision items 2 and 3)
- Date: 2026-10-11

## Context

The dashboard shows the region x SKU x time heat map of [spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) for a conference talk, with 1 panel per `probe_type` and each cloud on its native scale (spec 0002, the purpose line and sections 4.6 and 7.5). It runs on a developer laptop. It reads the JSONL records from `file://`, `s3://`, and `az://` stores with the user's own cloud login. AWS rows are in S3 and Azure rows are in Blob, so a view of both clouds reads 2 stores. The rig does not have to be alive.

The repo is handed out to CNCF developers at a conference. They clone it, run `make init`, and run `make web/run`. `mise` installs the pinned tools.

The first guess was TypeScript and React with DuckDB-Wasm in the browser. A comparison tested that guess against 5 other stacks and 3 more that nobody listed.

The research found 1 hard fact that shapes the design: of the DuckDB clients, only native DuckDB reads the stores with the local credential chain. DuckDB-Wasm has no `aws` and no `azure` extension build, cannot read `~/.aws` or the Azure CLI token, and needs a CORS rule on the bucket. Thus a browser-only page cannot read the stores. Every option that works has 1 native DuckDB step on the laptop: a server process or a snapshot step.

Two options passed every must-have: Streamlit with the DuckDB Python package, and a static TypeScript page with a DuckDB CLI snapshot.

## Decision

1. The dashboard is a static page in TypeScript, built with Vite. It has no server at view time. It uses no UI framework and no chart library. The page draws the heat maps as SVG.
2. (Superseded by [ADR 0027](0027-the-board-reads-windows-from-a-duckdb-request-handler.md): the cut-over removed the snapshot step, `web/scripts/sync.sh`, and `web/sql/cells.sql`.) A snapshot step reads the stores with the pinned `duckdb` CLI. One SQL file reads the JSONL records with `read_json` and the credential chain secrets for S3 and Azure. It aggregates the rows into 1-hour cells and writes 1 JSON file per `probe_type` plus a `meta.json` into `web/public/data/`. The page reads these files. It runs no SQL.
3. (Superseded by ADR 0027: `STORE`, `WEB_OUT`, `WEB_SYNC_INTERVAL`, and `make web/sync` are removed; `make web/run` and `make web/export` read the database of `make data/db`.) `make web/run STORE="<url> [<url> ...]"` runs the snapshot, then serves the page on localhost. `make web/build` runs the snapshot and writes `web/dist/`, a folder that any static file host serves. The default `STORE` is `data/sim/` when the prober wrote synthetic data there, else the committed synthetic store `web/sample/`. Thus a fresh clone shows heat maps offline.
4. [Spec 0006](../spec/0006-dashboard-spec.md) states the requirements: the file layout, the make targets, the mise pins, the panels, the filters, the colour scales, and the "Done when" list for the build.

Of the first guess, the decision keeps TypeScript. It rejects DuckDB-Wasm in the browser, because it cannot read the stores. It rejects React, because the page does not need it.

## The 3 strongest reasons

1. **Credentials without a server.** The `duckdb` CLI runs native DuckDB. `CREATE SECRET (TYPE s3, PROVIDER credential_chain)` and `CREATE SECRET (TYPE azure, PROVIDER credential_chain)` use the AWS profile, SSO, or environment, and the Azure CLI login. One query reads an S3 store and a Blob store together. The CLI installs through mise from the aqua registry. It needs no Python environment, no C compiler, and no process at view time.
2. **A static folder for a conference.** `vite build` writes a static folder with the snapshot inside it. The same folder works from a laptop, from any static host, or from a USB stick. A person can see the heat maps from a link, with no clone and no cloud account. Streamlit, Dash, Grafana, and Mosaic need a running server for each viewer.
3. **Audience and project health.** In the 8 CNCF projects checked (7 graduated, 1 incubating), services are in Go and UIs in TypeScript, and most professional developers know SQL (judgment from these proxies). The stack is TypeScript plus SQL. Its parts are active: Vite 8.3.4 (2026-10-08) and DuckDB 1.5.6 (2026-09-28). The 2 frameworks with the same shape are not: Observable Framework had 0 commits in the 90 days before 2026-10-11, and the Evidence template prints "deprecated" since 2026-08-20.

## Rejected options

- **Streamlit or Dash with the DuckDB Python package.** The runner-up: the lowest effort, healthy, and `python` and `uv` are already pinned. But it needs a Python server process at view time and has no static export.
- **TypeScript and React with DuckDB-Wasm, browser only.** No `aws` or `azure` Wasm build, no credential chain, CORS on the bucket, a 34 MB Wasm file, and a file picker on each run.
- **Observable Framework.** The right pattern, a data loader that calls DuckDB, in a project in maintenance mode: 1 release in 12 months, 0 commits in 90 days.
- **Evidence.** The legacy static product is deprecated. The new Evidence Core is a hosted-warehouse product with no DuckDB connector.
- **Grafana with the DuckDB or Infinity data source.** A 454 MB server plus a 141 MB unsigned plugin. It is not in the mise or aqua registry, so a pin needs a tarball URL. Infinity reads no JSONL and no local files.
- **Mosaic and vgplot.** Active and strong, but pre-1.0 with breaking minor releases, and it needs a DuckDB server process at view time.
- **marimo, Rill, Go with the DuckDB Go driver.** The marimo Wasm export has no `httpfs`. Rill is in no registry and needs its project model. Go has no chart stack for this task and needs a C compiler.
- **React.** Not needed for 1 page with 1 filter row and 8 grids. A later ADR can add it if the page grows.

## Consequences

- New mise pin: `duckdb = "1.5.6"`. `node = "22"` is already pinned and gives `npm`. The change that edits `mise.toml` adds the pin.
- New make targets in `tasks/Makefile.web`: `web/deps`, `web/sync`, `web/run`, `web/build`, `web/test`, `web/lint`, `web/clean`, and the aliases `lint/web` and `test/web`.
- An attendee installs nothing by hand. `make init` installs `node` and `duckdb`. The first `make web/run` runs `npm ci` in `web/`. The first snapshot of an `s3://` or `az://` store downloads the DuckDB `httpfs`, `aws`, and `azure` extensions once. A `file://` store needs no network.
- To read a cloud store, an attendee needs an AWS profile with read access to the bucket, or `az login` and a data access role such as Storage Blob Data Reader on the container.
- (Superseded by ADR 0027: `WEB_SYNC_INTERVAL` is removed.) The page shows a snapshot, not a live query. `make web/run` refreshes the snapshot every `WEB_SYNC_INTERVAL` seconds (default 300). This matches the 5-minute probe cadence.
- (Superseded by ADR 0027: `web/sql/cells.sql` is removed; the window SQL lives in `web/sql/`.) All SQL lives in 1 file, `web/sql/cells.sql`. A change to the cell rule is a change to that file and to spec 0006.
- The build effort is higher than for Streamlit. The build change writes the filters, the SVG heat map, the legend, the tooltip, and the table view. The spec gives the exact rules, so an implementer does not guess.
- If the static page stops being a goal, a new ADR that picks Streamlit supersedes this one.
