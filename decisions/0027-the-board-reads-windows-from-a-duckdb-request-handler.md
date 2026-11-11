# 0027. The board reads windows from a DuckDB request handler, with a static export

- Status: accepted (decision, 2026-10-29)
- Date: 2026-10-29

## Context

[ADR 0004](0004-dashboard-stack.md) fed the page with a snapshot step: the DuckDB CLI aggregated the store into JSON files in `web/public/data/`, and the page ran no SQL. The new board shows a window of probe data (a horizon and a page) and derives every number and caption from the rows in view. The whole history is more than a page needs, and the window changes with the horizon and page controls.

The data pipeline has changed since ADR 0004. `make data/rows` mirrors the store to local files with the user's own cloud login, and `make data/db` builds `data/ftg.duckdb` from them ([spec 0006](../spec/0006-dashboard-spec.md) section 5.5, [ADR 0025](0025-daily-compaction-runs-on-the-prober-vm.md)). A local database now exists, with no cloud login needed to read it.

The research behind ADR 0004 still holds: DuckDB-Wasm in a browser cannot use the local credential chain ([DuckDB-Wasm](https://duckdb.org/docs/current/clients/wasm/overview)). This ADR does not need cloud access in the page; it reads a local file.

## Decision

1. **One data boundary.** The page reads 2 kinds of response (a metadata document and a window of rows) through 1 `DataSource` client over HTTP GET. [Spec 0006](../spec/0006-dashboard-spec.md) is the source of truth for the API paths and the horizon and page parameters (section 7.1), the client (section 7.2), and the payload shape (sections 7.3 and 7.4).
2. **A request handler on `@duckdb/node-api`.** One handler answers these paths. It opens `data/ftg.duckdb` read-only (`access_mode` set to `READ_ONLY`, [DuckDB, configuration](https://duckdb.org/docs/current/configuration/overview)) with the Node binding ([DuckDB Node.js Neo client](https://duckdb.org/docs/current/clients/node_neo/overview)). The window SQL lives in `web/sql/`; spec 0006 owns what a window holds (sections 7.4 and 8.2) and the handler rules (section 8.1). A response holds only the window in view plus the rows the derivation reads outside it.
3. **Same handler in dev and under `make web/run`.** The handler is mounted by a Vite plugin with a `configureServer` hook ([Vite, `configureServer`](https://vite.dev/guide/api-plugin#configureserver)). `npm run dev` and `make web/run` both run the Vite dev server with this plugin, so there is 1 code path and no second server (spec 0006 section 8.3).
4. **No cloud credentials.** The handler reads only `data/ftg.duckdb`. The store logins stay in `make data/sync`, which fills the mirror (spec 0006 sections 4.2 and 4.3). `make web/run` builds the database from the mirror first (`web/db`, section 12) and needs no login. The data is as fresh as the last `data/db`; the server reads a replaced file on the next request (decision R1).
5. **A static export.** `make web/export` asks the handler for every response and writes the same paths as files to `web/dist/`, next to the built page. The folder works on any static file host, as in ADR 0004. The exported files equal the server responses. Spec 0006 section 9 is the source of truth for the export.
6. **Pin.** `@duckdb/node-api` 1.5.6-r.2, exact. The rule: the DuckDB version of the node binding equals the DuckDB CLI pin in `mise.toml` (1.5.6, used by `data/db`), so the binding opens the file that the CLI wrote. The `-r.N` suffix is the revision of the npm package built on that DuckDB release; 1.5.6-r.2 is the latest revision for DuckDB 1.5.6 and depends on `@duckdb/node-bindings` 1.5.6-r.2 (`npm view @duckdb/node-api@1.5.6-r.2 dependencies`, 2026-10-29; [package](https://www.npmjs.com/package/@duckdb/node-api), [source](https://github.com/duckdb/duckdb-node-neo)). The `latest` dist-tag (2.0.0-r.1) is not used because it is built on a different DuckDB release than the CLI. A change of the CLI pin changes this pin in the same pull request. A server test runs `SELECT version()` in the CLI and in the binding and asserts that the 2 results are equal (spec 0006 section 8.4 item 4).

## Alternatives considered

- **DuckDB-Wasm on Parquet in the page.** The page needs no server, and the export would be a folder of Parquet files. Rejected for now: Wasm DuckDB cannot read the local credential chain, a Parquet build step is new, and the browser must download the Wasm engine (34 MB, per ADR 0004) before the first paint. The data boundary allows it later: a third source can replace either of the 2 above.
- **Pre-built static JSON only.** Needs no server and no Node binding. Rejected: the number of horizon and page combinations is fixed at build time, so any change to a horizon or a payload field needs a full rebuild, and the "ask for the window in view" rule would be a rule of the build, not of the data path. It is still the export format in decision item 5.
- **The DuckDB CLI as a child process per request.** Same pin as `data/db`, no new package. Rejected as the main path: a process start per request. It stays the fallback if the Node binding fails on a platform (spec 0006 decision R2).
- **A separate server (Express, Fastify, or Python).** More code and a second process model for a localhost page. Vite already serves the page. Rejected.

## Consequences

- New dependency: `@duckdb/node-api`, which downloads a native binary per platform. On a platform without a binary the handler can call the CLI (spec 0006 decision R2).
- Under `make web/run` the page needs a running Node process. A static host needs `make web/export` output instead; this is the only server-less path.
- The snapshot step is removed at cut-over: `web/scripts/sync.sh`, `web/sql/cells.sql`, and the 9-panel page. `make web/sync`, `STORE`, `WEB_OUT`, and `WEB_SYNC_INTERVAL` of ADR 0004 go with them. Spec 0006 section 12 lists the cut-over.
- A server fault (for example a missing database) shows in the page as a load error, not a blank board. Spec 0006 section 11.6 states the text.
- Read-only open means the handler cannot change the database, and 2 handlers or a `data/db` run can read it at once as long as no one writes. A `data/db` run that replaces the file while the server runs needs no restart: the handler reads the new file on the next request (spec 0006 section 8.1 item 2). A refresh while the server runs, other than that, is out of scope (spec 0006 section 1).

## Cost to reverse

Low. The page depends only on the 2 paths and the payload shape. Replacing the handler with DuckDB-Wasm, a static folder, or another server changes the `DataSource` client or the server files (`web/server/`, `web/sql/`) and nothing in `web/src/board/derive/` or `web/src/board/view/`. The export already proves that a file source works.
