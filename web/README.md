# Dashboard

The board shows where and when GPU capacity was obtainable across clouds, from the records of the prober. `index.html` is the board, built with React and shadcn/ui. A request handler reads a local DuckDB file and answers the board's API. The requirements are in [spec 0006](../spec/0006-dashboard-spec.md). The stack decisions are [ADR 0026](../decisions/0026-the-board-view-is-react-with-shadcn-ui-in-the-nocturne-dark-theme.md) and [ADR 0027](../decisions/0027-the-board-reads-windows-from-a-duckdb-request-handler.md).

## The board

`index.html` is the entry of the board ([spec 0006](../spec/0006-dashboard-spec.md), ADR 0026). The source of the board is `src/board/` (`derive/` holds the pure functions, `view/` the React components), `src/data/` (the `DataSource` client), and `src/components/ui/` (shadcn source). The board reads `api/meta.json` and `api/window/<hz>/<page>.json`. The Vite dev server answers both from a local DuckDB file with the API plugin in `server/`. There is no second server and no cloud call.

## Run it

The board needs no login. Only the sync step reads a cloud store, with your own login. From the repo root, after `make init`:

```sh
make dev       # syncs the rows, rebuilds the database, starts the board
mise run dev  # the same as make dev
mise run web    # the board only, on the local data (no sync, no login)
```

`make dev` copies your rows and starts the board. It reads `AWS_DATA_BUCKET` and `AZURE_DATA_ACCOUNT` from `tofu output` of your stacks when they are unset. It prints no name and no npm noise. Vite prints its normal banner (the Local URL) and its errors. If the AWS login is expired, it prints one line with the `aws sso login --profile $AWS_PROFILE` command and shows the data that is local. `mise tasks` lists the tasks.

The newest row time of each cloud is printed before the Vite banner, which shows the URL `http://127.0.0.1:4173/`. Vite runs in the foreground of your terminal, so its keys work (for example `h` + Enter). Ctrl-C stops the board and returns to the prompt with no make error line. The make targets stay: `make web/run` installs the npm packages with `npm ci` on the first run, builds the database (`make web/db`), and starts the dev server. Ctrl-C stops the server. The server reads the database file again after `make web/db` replaced it, so a new sync needs no restart. Set only one of the two variables if you use one cloud. The credentials and the sync rules are in "Local mirror", below.

`make web/db` builds the database `WEB_DB` from the mirror `DB_MIRROR` (default `data/live/`) with `make data/db`. When the mirror holds no row file, it builds `data/sample.duckdb` from `web/sample/` (see "Offline run"), and never overwrites `data/ftg.duckdb`.

| Target | Does |
|---|---|
| `make web/run` | `make web/db`, then the dev server on `127.0.0.1:$(WEB_PORT)`; prints `source live` or `source sample` |
| `make web/db` | builds the database `WEB_DB`; no network |
| `make web/build` | the static page in `web/dist/` |
| `make web/export` | builds `WEB_DB` and the page, then writes `api/meta.json` and every `api/window/<hz>/<page>.json` into `web/dist/`; no login. |
| `make web/standalone` | runs `web/export`, then writes 1 self-contained HTML file (default `web/dist/standalone.html`, or `OUT=<path>`) that opens by double-click from `file://` with no network; prints its path |
| `make web/preview` | serves `web/dist/` with `vite preview` (no server code) on `127.0.0.1:$(WEB_PORT)` and prints the URL; run `make web/export` first, else it stops and says so |
| `make web/test` | the unit tests (Vitest) |
| `make web/lint` | Biome (lint and format) and `tsc --noEmit` |
| `make web/sample` | writes `web/sample/` again |
| `make web/clean` | removes `dist/` and `node_modules/` |

| Variable | Default | Meaning |
|---|---|---|
| `DB_MIRROR` | `data/live` of the main checkout | the mirror that `web/db` reads |
| `WEB_DB` | `data/ftg.duckdb`, or `data/sample.duckdb` for the sample | the database file |
| `WEB_PORT` | `4173` | the dev server port |

If `WEB_PORT` is in use, `make web/run` stops with a message. Set another port, for example `make web/run WEB_PORT=4180`.

## Store URLs

The board reads no store. Only `make data/sync` reads a cloud store (S3 or Azure Blob), with your own login, and copies the rows to the mirror `data/live/`. The sections below tell which permissions that step needs.

### AWS

Set `AWS_PROFILE` and `AWS_REGION`, as for the other make targets. The AWS identity needs:

- `s3:ListBucket` on the bucket. `aws s3 sync` lists the objects with the `ListObjectsV2` API.
- `s3:GetObject` on the objects under the prefix.

If the bucket uses SSE-KMS, the identity also needs to decrypt with the key. The sources are in [spec 0006](../spec/0006-dashboard-spec.md) section 4.2.

### Azure

Run `az login`. Whoever deploys the Azure stack ([spec 0005](../spec/0005-azure-stack-spec.md), section 5.3) gets **Storage Blob Data Reader** on the `data` container with no extra step: the stack assigns it to the identity that runs `tofu plan` (or a plain `tofu apply`, with no saved plan). For any other reader, add their Entra object ID (a group ID is preferred) to the stack's `data_reader_principal_ids` variable before the deploy. The sources are in [spec 0006](../spec/0006-dashboard-spec.md) section 4.2.

### Both clouds in 1 view

Each cloud writes to its own store. The board reads 1 database, so give both clouds to `make data/sync`; it mirrors each cloud to `data/live/`, and `make web/run` builds 1 database from both:

```sh
make data/sync AWS_DATA_BUCKET=<bucket> AZURE_DATA_ACCOUNT=<account>
make web/run
```

### Local mirror

`make data/sync` (`tasks/Makefile.data`) copies each cloud's rows to `data/live/`. This is the logged-in step; `make web/run` then reads the mirror with no credentials:

```sh
make data/sync AWS_DATA_BUCKET=<bucket> AZURE_DATA_ACCOUNT=<account>
```

`make data/sync/aws` and `make data/sync/azure` mirror 1 cloud each. Set the real bucket and account names yourself. They copy the rows only, not the logs. A first run copies every row. A later run looks up the newest day of each probe type in the local copy, and lists and copies only that day and later days (`scripts/data-sync.sh`; [spec 0006](../spec/0006-dashboard-spec.md) section 4.3). It needs `aws` and `azcopy` (and `az` for Azure) and does not delete a local file. `data/` is git-ignored.

### Network

- `make web/run` and `make web/export` need no network, except the first run: `npm ci` downloads the npm packages once.
- The page makes no request to a host other than the one that serves it (fonts are self-hosted, ADR 0026).
- `make data/sync` needs the network and your cloud login ([spec 0006](../spec/0006-dashboard-spec.md) section 4.3).

## Logs

The prober and the sweeper write their own logs to the same store, under `experiment=<name>/logs/provider=<p>/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl` ([spec 0003](../spec/0003-prober-core-spec.md) section 8.2). The second path segment is the literal `logs`, not `provider=`, so the dashboard's row glob (`experiment=*/provider=*/probe_type=*/date=*/*.jsonl`, above) never reads a log object, and this build does not show logs as a panel. Each line is 1 JSON object mapped to the OpenTelemetry log data model (`Timestamp`, `SeverityText`, `Body`, `Attributes`), plus `run_id`, `experiment`, `provider`, and `host`.

Read them with their own `duckdb` query, for example a provider's errors of the last day:

```sh
duckdb -c "
  SET TimeZone = 'UTC';
  CREATE OR REPLACE SECRET s3 (TYPE s3, PROVIDER credential_chain);
  SELECT Timestamp, SeverityText, Body, run_id
  FROM read_json(
    's3://<bucket>/experiment=<name>/logs/provider=aws/date=*/*.jsonl',
    format = 'newline_delimited'
  )
  WHERE SeverityText = 'ERROR' AND Timestamp > now() - INTERVAL 1 DAY
  ORDER BY Timestamp;
"
```

For the local mirror (section "Local mirror", above; `data/sync` does not copy the logs prefix, so copy it by hand, for example with `aws s3 sync` or `azcopy copy` on the `experiment=<name>/logs/` prefix), point the glob at the local folder instead, for example `data/live/aws/experiment=<name>/logs/provider=aws/date=*/*.jsonl`.

## Offline run

A fresh clone has no `data/live/`. Thus `make web/run` builds `data/sample.duckdb` from the synthetic store `web/sample/` (made by `make web/sample`) and serves it, with no credentials. The first run needs the network once, for `npm ci`. `make web/db` alone builds the same file. To force the sample when a mirror exists, run `make web/run DB_MIRROR=/nonexistent`.

`web/sample/` holds 48 hours of rows from 2026-10-09T00:00:00Z in the mirror layout (spec 0006 section 13), every row `simulated: true`, for the 9 probe types. The values are synthetic. They are not measurements. `make web/sample` generates it from fixed strings with `scripts/make-sample.sh`, so a new run gives the same files. The folder is a build product, not hand-written.

## Files

| File | Holds |
|---|---|
| `index.html` | the entry of the board |
| `server/` | the API plugin: `api/meta.json` and `api/window/<hz>/<page>.json` from the DuckDB file |
| `sql/` | the window SQL, read-only (spec 0006 section 8) |
| `scripts/run.sh` | the dev server with the API plugin on `WEB_DB` |
| `scripts/export.ts` | writes the page and every API file into `dist/` |
| `scripts/make-sample.sh` | writes `sample/` |
| `src/board/derive/` | the pure derive functions |
| `src/board/view/` | the React components |
| `src/data/` | the `DataSource` client |
| `src/components/ui/` | shadcn/ui source |
