---
title: "Dashboard spec: the GPU Availability Board"
date: 2026-10-11
---

# 0006. Dashboard spec: the GPU Availability Board

The dashboard is the GPU Availability Board: 1 page that answers 1 question for a person who needs GPU capacity. "Can I get this GPU machine now, anywhere, and if not, what do I do?" The board replays the probe rows of the rig (the cadence comes from the config: 2 hours by default, 1 hour as an example, spec 0002 D2; AWS and Azure, the US regions of the experiment) and computes every number, caption, threshold, and chart from the rows in view. No caption is written for one run. The board replaces the 9-panel heat map page of the earlier version of this spec.

The board has 2 boundaries, so that each side can change alone:

- **The data boundary.** The page reads 2 HTTP paths, `GET <base>/api/meta.json` and `GET <base>/api/window/<hz>/<page>.json`, through 1 `DataSource` client (section 7). A local server answers the paths from DuckDB (section 8). A static export writes the same paths as files (section 9).
- **The logic boundary.** Pure TypeScript functions in `web/src/board/derive/` compute every number and caption (section 10). They import no DOM API, no React, no component library, and no server code. The view in `web/src/board/view/` only draws what they return.

The view stack is ADR 0026. The data path (a server on DuckDB plus a static export) is ADR 0027. [ADR 0004](../decisions/0004-dashboard-stack.md) is superseded in part by them. The visual design (colours, type, spacing, chart geometry, copy templates, and interactions) is owned by the design tool; this spec states it as design specs (section 11). This spec states each requirement. An implementer must not need any other document.

## 1. Scope

1. The board must run from a fresh clone after `make init`, with `make web/run`, on macOS and Linux, with no manual install. `web/run` installs the npm packages itself on the first run.
2. The page reads only the 2 paths of section 7. The server reads only the local DuckDB file of section 5.5 (`data/ftg.duckdb`, or the sample database of section 13). The server holds no cloud credential and makes no network call. The rows reach the local mirror with `make data/sync` (section 4.3), which uses the user's own cloud login.
3. It must run offline on the generated sample (section 13), with no network, after the first `make web/run` installed the npm packages.
4. `make web/export` must write a static folder (`web/dist/`) that any static file host serves, with the same paths as the server (section 9).
5. The rig does not have to be alive.
6. The page makes no request to a host other than its own origin at view time. The font is local (decision R4), and so are the icons of section 11.9 (decision R12).
7. At a width of 360 px, the page does not scroll horizontally. The board keeps a minimum width of 960 px and scrolls horizontally inside its own wrapper (decision R5).

Out of scope for this version: authentication, a hosted deploy of `web/dist/`, a light theme (decision R3), a layout for widths below 960 px, and a refresh of the data while the server runs other than the next request after `make data/db` (decision R1).

## 2. Stack

| Part | Choice | Version pin |
|---|---|---|
| Language | TypeScript | `typescript` in `web/package.json`, exact version |
| Build and dev server | Vite | `vite` in `web/package.json`, exact version ([Vite guide](https://vite.dev/guide/)) |
| Runtime | Node | `node = "22"` in `mise.toml` (already pinned) |
| View | React, shadcn/ui, and ReUI components where they fit, themed with the Nocturne CSS variables (ADR 0026) | exact versions in `web/package.json` |
| Font | Inter, self-hosted with `@fontsource/inter` ([Fontsource Inter](https://fontsource.org/fonts/inter/install)) | exact version in `web/package.json` (decision R4) |
| Server database client | `@duckdb/node-api` ([DuckDB Node.js Neo client](https://duckdb.org/docs/stable/clients/node_neo/overview)) | exact version whose engine is the DuckDB CLI pin: `1.5.6-r.2` for `duckdb = "1.5.6"` ([npm versions](https://www.npmjs.com/package/@duckdb/node-api?activeTab=versions)); a change of 1 pin changes both (decision R2) |
| Data build | DuckDB CLI | `duckdb` in `mise.toml` (1.5.6), used by `make data/rows` and `make data/db` |
| Package manager | npm, which comes with Node | `web/package-lock.json`, installed with `npm ci` |
| Tests | Vitest, with the `jsdom` environment for view tests | exact versions in `web/package.json` |
| Lint and format | Biome | `@biomejs/biome` in `web/package.json`, exact version |
| Charts | none; the view draws SVG and CSS | |

Every tool runs through `mise exec --` ([ADR 0002](../decisions/0002-makefiles-follow-cloud-posse-conventions.md)).

## 3. File layout

```text
web/
  README.md                 # how to run; the mirror; the offline run; the export
  package.json              # exact versions; scripts: dev, build, preview, test, lint, typecheck, export
  package-lock.json
  tsconfig.json
  biome.json                # includes the derive-layer import rule (section 10.3)
  vite.config.ts            # base: './'; the API plugin of section 8
  index.html
  server/                   # the request handler and the Vite plugin (section 8)
  sql/                      # the window SQL (section 8.2), 1 file per payload part
  scripts/
    run.sh                  # web/run (section 12)
    export.ts               # web/export (section 9)
    make-sample.sh          # writes web/sample/ (section 13)
  src/
    data/                   # the DataSource client (section 7.2) and the payload types
    board/
      main.tsx              # bootstrap
      styles.css            # the Nocturne tokens
      derive/               # pure functions: every number and caption (section 10.1)
        tsconfig.json       # lib without DOM (section 10.3)
      view/                 # 1 component per board section (section 10.2)
    components/ui/          # shadcn/ui and ReUI components (ADR 0026)
  sample/                   # generated synthetic rows, mirror layout (section 13)
  test/
    board/                  # 1 test file per board section, against the fixture
    fixtures/board/         # the synthetic payload fixture (section 10.4)
    server/                 # server tests on a fixture database (section 8.4)
tasks/
  Makefile.web              # section 12
```

Implementer's choice: the split of files inside `server/`, `src/data/`, `derive/`, and `view/`, as long as the folders above exist and each board section of section 11 has its own derive file, view file, and test file.

## 4. Store, mirror, and credentials

### 4.1 Store key layout and the row glob

The prober writes each row object under `experiment=<name>/provider=<p>/probe_type=<t>/date=<D>/<file>.jsonl` ([spec 0003](0003-prober-core-spec.md) section 4.2). Rows written before the data move are under `run_mode=<m>/provider=<p>/probe_type=<t>/date=<D>/`; the reader rule of ADR 0011 section 5 reads them (section 5.1). The data layer reads only files whose path follows this layout and ends in `*.jsonl` (or the daily files of section 4.4).

The raw Spot Advisor feed objects (`*_raw_*.json`, spec 0002 section 4.8) do not end in `*.jsonl`, so they are not read as rows. The prober's and the sweeper's own logs (`experiment=<name>/logs/provider=<p>/date=*/*.jsonl`, [spec 0003](0003-prober-core-spec.md) section 8.2) are not read either: the second path segment of a logs object is the literal `logs`, which the second segment of the row layout, a literal `provider=`, never matches. A daily file (`experiment=<name>/daily/v1/...`, section 4.4) is read only by the daily-file rule of section 5.5, never as raw rows. The board shows no logs; `web/README.md` gives a `duckdb` query to read them on their own.

### 4.2 Credentials

The board needs no credential. Only `make data/sync` (section 4.3) reads a store, with the user's own login. AWS: the `aws` CLI with a profile that can read the bucket (the default profile, or the profile that `AWS_PROFILE` names). The listing uses `ListObjectsV2`, and for it "You must have permission to perform the `s3:ListBucket` action." ([ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)). Thus the AWS identity needs `s3:ListBucket` on the bucket and `s3:GetObject` on the objects under the prefix ([GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html)). Azure: `az login` and `azcopy`, and the signed-in identity needs the `Storage Blob Data Reader` role on the `data` container ([Assign a role for blob data access](https://learn.microsoft.com/en-us/azure/storage/blobs/assign-azure-role-data-access)). The deployer of the Azure stack gets that role with no manual role assignment, because the stack assigns it to the identity that runs `tofu plan` (or a plain `tofu apply`); any other reader goes in the stack's `data_reader_principal_ids` variable before the deploy ([spec 0005](0005-azure-stack-spec.md), section 5.3). `web/README.md` must say this, and must say that `make web/run` and `make web/export` need no login.

### 4.3 Local mirror

`tasks/Makefile.data` mirrors each cloud's rows to a local folder, so the dashboard reads `file://` stores with no cloud round trip per snapshot and no credentials once the mirror is current: `make data/sync/aws` runs `scripts/data-sync.sh aws` into `data/live/aws/`, `make data/sync/azure` runs `scripts/data-sync.sh azure` into `data/live/azure/`, and `make data/sync` runs the target of each cloud that has a deployment at the same time (`make -j2`; a failure on either cloud fails the target). **Store names from the deploy outputs.** The targets read the bucket name (output `bucket_name` of the AWS stack) and the account name (output `data_storage_account` of the Azure stack) with `tofu output -raw` ([spec 0014](0014-config-and-deploy-spec.md) section 4). Nobody types a store name. **One cloud.** A user who deploys one cloud has the output of one stack: `make data/sync` prints 1 line for each cloud it skips (`data/sync: no aws deployment (no output bucket_name in terraform/aws); skipping aws`) and syncs the other. With neither output, it prints a message and exits 2. **Rows only.** The script walks the key layout `<top>/provider=<p>/probe_type=<t>/date=<D>/<file>` of section 4.1, where `<top>` is `experiment=<name>` or the old `run_mode=<m>`, and so reads only the row prefixes and skips every other prefix, for example `code/` (the code zips of the AWS stack). It never lists or copies the logs prefix (`experiment=<name>/logs/`, [spec 0003](0003-prober-core-spec.md) section 8.2): no local data tool reads it. The raw feed objects (`*_raw_*.json`) sit under the row prefixes, so the mirror copies them; the `*.jsonl` glob of section 4.1 does not read them. **Only new objects.** For each `<top>/provider=<p>/probe_type=<t>/` the script finds the newest `date=<D>` folder in the local copy, then lists and copies that day and every later day. It never lists or compares an older day: a prober writes an object once, under the day of its time, and never rewrites it, so a day before the newest local day is complete. With no local folder for a probe type, the script copies every day of it. A late object on a day before the newest local day is not fetched; the operator gets it by deleting that day's local folder and running the target again. AWS: `aws s3api list-objects-v2 --delimiter / --prefix <type prefix> --start-after <type prefix>date=<newest>` returns the child prefixes (the days), because the keys sort by date inside a probe type ([ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html): `StartAfter`, `Delimiter`); `aws s3 sync` copies each of those days ([`aws s3 sync`](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html) copies an object when the destination does not have it, or when its size or time differs). Azure: `az storage blob list --prefix <type prefix> --delimiter /` returns the days ([az storage blob list](https://learn.microsoft.com/en-us/cli/azure/storage/blob#az-storage-blob-list)), and `azcopy copy --recursive --overwrite=false` copies each day from the newest local one on ([azcopy copy](https://learn.microsoft.com/en-us/azure/storage/common/storage-ref-azcopy-copy)). **Many requests at once.** S3 and Azure Blob have no call that returns many objects, so a download is 1 request for each object. For each top the script lists the prefixes (tops, providers, probe types) one after the other, and runs the day listing of each probe type, and the daily listing and copy of section 4.4, at the same time (1 background job each). It then copies the raw days of each probe type one day at a time, oldest first (the listed days in order, then the days of a bad pair of section 4.4 that the listing did not return, because those are older than the newest local day), and stops that probe type at its first failure (else a later day could succeed after an older one failed, the next run would list from the later day, and the failed day would be lost); the probe types run at the same time. AWS runs 1 `aws s3 sync` for each day prefix. Azure runs 1 `azcopy copy` of the top with `--include-path` for each probe type and day ([azcopy copy](https://learn.microsoft.com/en-us/azure/storage/common/storage-ref-azcopy-copy)). A day with a complete sidecar (section 4.4) is not listed for copy and no call is made for it. A higher request count for each transfer on top of that was measured on 2026-10-27 and is slower, not faster: with about 2,100 new AWS objects, `s3.max_concurrent_requests` of 10 (the default) took 19 s twice, and 100 took 23 s and 30 s; with about 960 new Azure objects, `AZCOPY_CONCURRENCY_VALUE` of 16 took 24 s and 26 s, and 256 took 42 s. So the script leaves both tool defaults, and changes no user config file. Neither target deletes a local file: an object deleted in the cloud stays in the mirror, and the logs that an earlier version of the target copied stay too. Each target stops with a message when its stack has no output for the store name; this repo holds no real bucket or account name. `data/` is git-ignored. The names come from the outputs of the AWS stack ([spec 0004](0004-aws-stack-spec.md) section 7) and the Azure stack ([spec 0005](0005-azure-stack-spec.md) section 6), read in the stack folders of the main checkout. `scripts/data-sync.sh` keeps its `AWS_DATA_BUCKET` and `AZURE_DATA_ACCOUNT` interface: make sets them from the outputs, and the test drives the script directly. `scripts/test-data-sync.sh` (`make test/data-sync`) checks the row and day rules against a fake `aws`, `az`, and `azcopy`: a re-run with no new object copies nothing and lists no older day, a new object on the newest day is copied, and no log is copied. `make web/run` reads the database built from `data/live/` (section 12).

### 4.4 Daily files in the mirror

The prober VM packs each closed UTC day of 1 probe type into 1 data file and 1 sidecar manifest in the store: `experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst` and `<probe_type>_<D>.jsonl.zst.manifest.json` (ADR 0025). The job writes the sidecar last, so a sidecar marks that probe type and day complete. A day folder holds about 14 small files. The key has no provider, because each store belongs to 1 cloud. `scripts/data-sync.sh` (section 4.3) uses the files so that a refresh lists a few folders and fetches a few files in place of hundreds of row objects. The folder and file names are variables at the top of the script (`daily_root`, `daily_data_suffix`, `daily_manifest_suffix`, `daily_bad_suffix`), the only place that names them. For each `experiment=<name>/` top the script does 4 steps; the old `run_mode=` tops have no daily folder and keep the rule of section 4.3.

1. **Copy the day folders.** One delimiter listing returns only the `date=` folder names under `daily/v1/` (no object is listed). The script copies each day from the newest complete local day on, that day again included, with 1 background job for each day on AWS and 1 `azcopy copy --include-path` for all the days on Azure (the only batched copy), at the same time as the raw day listing of step 3: `aws s3 sync` ([`aws s3 sync`](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)) or `azcopy copy --recursive --overwrite=ifSourceNewer` ([azcopy copy](https://learn.microsoft.com/en-us/azure/storage/common/storage-ref-azcopy-copy)), which copy only the files the mirror lacks or that changed. The newest complete local day is the newest local day folder that holds a sidecar. A day older than that is not listed again, except a day that holds a renamed (bad) sidecar with no good sidecar beside it: the script copies that day again by its prefix, on both clouds, and its files replace the local ones even when the local file is newer (`azcopy copy --overwrite=true`, `aws s3 sync --exact-timestamps`; without the flag, [`aws s3 sync`](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html) ignores a same-size file unless the local one is newer than the store object, and with `--exact-timestamps` it ignores it only when the times are equal), so a damaged local file does not stay damaged. A first run copies every day. The mirror keeps the store layout: `data/live/<cloud>/experiment=<name>/daily/v1/...`.
2. **Check each local sidecar.** A (day, probe type) pair is complete when the sidecar is there, and the size and the SHA-256 of the data file are the sidecar's `file_bytes` and `file_sha256` ([spec 0003](0003-prober-core-spec.md) section 4.3; the fields are from `prober/ftg_prober/compact.py`). A data file with no sidecar is not complete, and the script does nothing to it. A pair whose sidecar the data file does not match (a missing or unreadable file, or a different size or hash) is **bad**: the script prints a warning to stderr, renames the local sidecar to `<sidecar>.bad` (it replaces an older file of that name), and goes on. The sync does not fail for a bad pair. With no sidecar, the reader of section 5.5 ignores the pair.
3. **Copy raw rows for the pairs with no complete sidecar.** The rule of section 4.3 stays: for each probe type the script lists the raw days from the newest local raw day on and never lists or compares an older day. Of the listed days it copies the raw objects of each day where that probe type has no complete sidecar: the open day, a day the job has not compacted yet, and a day whose build failed. It does not copy the raw objects of a pair with a complete sidecar. A bad pair is copied whatever its age, also when its day is older than the newest local raw day. A day that completes a sidecar after a newer day has one is not copied again by step 1 until the day holds a bad sidecar; meanwhile the raw objects of that pair are used.
4. **Never delete.** The script deletes no local file and no store object, and it changes no store object. A pair can have both a daily file and raw objects in the mirror (a day copied before it was compacted). The reader uses the daily file and skips those raw objects (section 5.5). A day that gets a late raw object after its daily file was built keeps the daily file in the mirror until the job rebuilds it; the sync does not compare the raw object list of a complete pair.

`scripts/test-data-sync.sh` (`make test/data-sync`) checks this against the same fake `aws`, `az`, and `azcopy` as section 4.3 (the fakes print what the real commands print and write what they write): a pair with a good sidecar copies no raw object, and the daily files of every day are copied; the open day copies its raw objects; a data file that does not match its sidecar warns, renames the sidecar to `.bad`, and copies the raw objects of that pair only, also for a day older than the newest local raw day; a re-run copies no day before the newest complete day; and no file in the mirror or in the store is deleted. `make test/db` checks the reader of section 5.5 on this layout.

## 5. Data layer

### 5.1 Record columns and reader rules

The data layer reads the 15 record fields of [spec 0003](0003-prober-core-spec.md) section 4.1 plus the legacy `run_mode`, with these types: `ts_utc` `TIMESTAMPTZ`, `run_id` `VARCHAR`, `run_mode` `VARCHAR`, `simulated` `BOOLEAN`, `provider`, `region`, `zone`, `probe_type`, `sku`, `placement_group_mode` `VARCHAR`, `n_requested` and `n_placed` `INTEGER`, `outcome` and `error_code` `VARCHAR`, `latency_ms` `INTEGER`, and `details` `JSON` (so that differing keys between probe types do not conflict). The session time zone is UTC (`SET TimeZone = 'UTC'`), so every date and hour below is UTC unless the rule names America/New_York or America/Los_Angeles.

- **Old-row reader rule (ADR 0011 section 5).** `simulated` is `coalesce(simulated, run_mode = 'local', false)`: a row written before ADR 0011 has `run_mode` and no `simulated`.
- **Dedup across the data move (spec 0003 section 9A).** The same object can exist at both its old `run_mode=` key and its new `experiment=` key for a short time. A key's base file name (`<ts>_<target>_<uuid>.jsonl`, spec 0003 section 4.2) is unique on its own, so 2 files that share a base name are 1 object. The reader keeps 1 file per base name and prefers the `experiment=` copy.

### 5.2 Rows the board reads

1. **Probe types.** The board reads the rows of 9 probe types: `aws_odcr`, `az_capacity_reservation`, `az_vmss_flex` (the provisioning probes, section 5.3); `aws_cb_offering`, `aws_cb_longest` (section 5.4); `aws_spot_score`, `az_spot_score`, `aws_spot_price`, `az_spot` (section 5.4). It ignores every other probe type (`aws_cb_earliest`, `aws_spot_advisor`, `az_precheck`).
2. **Simulated rows (decision R7).** The server drops every row with `simulated` true. If the database holds no row of the 9 types with `simulated` false, the server uses the simulated rows instead and sets `meta.simulated` to true (section 7.3). The page then shows the banner "No real rows in this store. Showing simulated rows." above the intro. A database built from the generated sample (section 13) holds only simulated rows, so the offline run always shows a board with the banner.
3. **Hours.** `t0` is the start of the UTC hour of the earliest `ts_utc` over the rows of sections 5.3 and 5.4 that make sweeps and Capacity Block records. When the rows that item 2 keeps make no sweep and no Capacity Block record (for example, only the Spot rows are real and the provisioning rows are simulated), `t0` uses the earliest `ts_utc` of all the rows that item 2 keeps. `t0` is null only when item 2 keeps no row. For any record, `h` = floor((`ts_utc` − `t0`) / 1 hour), an integer, and `t` = (`ts_utc` − `t0`) / 1 hour, rounded to 2 decimals. `h` can be negative for a Spot score row before `t0`.

### 5.3 Sweeps from the provisioning rows

A **sweep** is the result of 1 provisioning entry in 1 round, over every zone that the entry asked ([ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md), [ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md)). The board builds it in 2 steps: 1 result for each zone search, then the rows-to-sweep rule, which combines the zone searches of 1 entry and 1 round.

**Zone search.** The search key is (`run_id`, `provider`, `probe_type`, `region`, `zone`, `sku`, `details.n_config`): 1 call can search several entries, 2 entries of 1 target with a different `n` are 2 searches, and an `aws_odcr` entry runs 1 search for each zone of the Region that offers the type ([spec 0002](0002-gpu-capacity-tracker-spec.md) section 4.9). The prober searches only the zones that offer the type, so a zone that does not offer it writes no row: it is not asked, and it is never a quota wall. A row with a null `zone` makes 1 zone search too: an Azure regional row (the platform picks the zone), or an `aws_odcr` Region row (`zone` and `details.zone_id` null), which the prober writes when its pre-check finds no zone that offers the type (`restricted`) or fails (`error`), and after which it asks no zone (`prober/ftg_prober/aws/odcr.py`). An `aws_odcr` row written before the probe covered every zone has the 1 configured zone of its entry in `zone` and no `details.zone_id`. These rows are not part of any zone search:

- a row with `details.cleanup` = `true` (the `aws_odcr` cleanup, spec 0002 section 4.9; it is not an attempt);
- an `aws_odcr` row with `outcome` = `restricted` and a non-null `zone`: an old row, "not asked" (decision R10, section 6.2).

The **halving rows** of a zone search are its rows without `details.search_direction` = `"up"`. The **first attempt** is the halving row with the smallest `details.search_step` (a row without a step counts as 1), then the earliest `ts_utc`. Each zone search has these values:

| Value | Rule |
|---|---|
| `asked_z` | the largest `n_requested` of the halving rows (the first count, C), or 0 when null |
| `result_z` | the search result (`search_result`, ADR 0009): null when no halving row has `outcome` `success`, `partial`, `no_capacity`, or `none`; else the largest `n_requested` of a halving row with `outcome` = `success`, or for an old `partial` row its `n_placed`, or 0 |
| `state_z` | when `result_z` is not null (the zone **answered**): `2` when `result_z` = `asked_z` > 0; `1` when 0 < `result_z` < `asked_z`; `0` when `result_z` = 0. Else the first attempt's `outcome` mapped by the table of section 6.1 |
| `nMax_z` | the largest `n_requested` with `outcome` = `success` over all rows of the zone search, upward rows included; else `result_z`, or 0 |

A zone search where the halving search placed fewer than it asked (0 < `result_z` < `asked_z`) is a yes, state 1. The result of the search is the largest success (ADR 0009), and a yes means the cloud handed us capacity.

**Rows to sweep** (with both counts of rule 5):

1. **Zone result.** Each zone search has the values of the table above. A row with a null `zone` makes 1 zone search (the paragraph above); rule 6 says which zone searches count as an asked zone.
2. **Group.** The round key is `run_id`: all the zones of 1 Region lane in 1 cycle share 1 `run_id` ([spec 0003](0003-prober-core-spec.md) section 6B). `run_id` is always a UUID: each row has the UUID of its probe call ([spec 0003](0003-prober-core-spec.md) section 4.1, rule 2). A row with a null `run_id` breaks that rule; the board does not stop on it (section 6.4) and uses its hour `h` as its round key. The hour is finer than the cadence of the provisioning probes (the config sets it: 2 h by default, 1 h as an example, spec 0002 D2), so 1 cycle is 1 round at either cadence and the board does not assume one cadence. 2 cycles of 1 entry never share 1 such round. The zone searches with the same (`provider`, `probe_type`, `region`, `sku`, `details.n_config`, round key) make 1 group, and 1 group is 1 sweep.
3. **Answered.** A zone answered when its `result_z` is not null.
4. **State.** When 1 or more zones answered, the state is the best `state_z` of the answered zones (2, then 1, then 0). A zone with an error, a timeout, or a skip next to an answered zone does not change the state. When no zone answered, the state is the first of these that applies to a zone of the group: `restricted_or_no_capacity` gives `-3`, its own state (section 6.1); `quota` gives `-1` (the quota is per Region); an Azure `restricted` row gives `-1` (section 6.2); an `aws_odcr` Region `restricted` row (`zone` null) gives `-4`, "not offered in this Region", its own caption and no quota wall (section 6.2); else `-2` (`error`, `timeout`, `cap`, or another value), which the board drops, as before.
5. **Counts.** `asked` is the largest `asked_z` of the answered zones. The sweep carries 2 counts:
   - `gotZoneMax` is the largest `result_z` of the answered zones: the most that 1 zone placed. A single request lands in 1 zone, so this is what a single request gets. It is the headline `got` of every section of section 11.
   - `gotZoneSum` is the sum of `result_z` over the answered zones: what a fleet that spreads across the zones gets. The zones of a Region are separate, isolated locations ([Regions and Zones](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-regions-availability-zones.html)) with separate capacity pools, and a reservation cancelled in 1 zone frees nothing in another, so the results of the zones of 1 round, which run one after the other a few seconds apart (spec 0003 section 6B), are in effect independent. The sum is a lower bound ("at least"), not the capacity of the Region: each zone's halving search stops at the count asked (ADR 0009).

   `nMax` is the largest `nMax_z` of a zone of the group.
6. **Coverage.** The sweep carries the number of zones asked (`zones`) and the number of zones with a yes (`zonesYes`). `zones` counts each zone search of the group that asked a zone: an `aws_odcr` Region row (`zone` null) asks no zone and counts 0; every other zone search, an Azure regional row included, counts 1. The place tooltip shows "Yes in {zonesYes} of {zones} zones" and, when 2 or more zones said yes, "{gotZoneSum} across {zonesYes} zones" (section 11.9).
7. **Method change.** Earlier, `aws_odcr` asked 1 configured zone per Region. On an old row with a `zone`, `restricted` meant "not offered in that 1 zone": it is "not asked" (rule of the zone search above) and never a quota wall. A Region `restricted` row is "not offered in this Region" (rule 4, state `-4`), never a quota wall. `meta.zoneStartH` (section 7.3) is the start of the per-zone probe. The open rows of section 11.9, the trend (section 11.13), and the definitions (section 11.22) mark it, because the AWS rates before and after it come from different methods.

The sweep record has these fields:

| Field | Value |
|---|---|
| `h`, `t` | of the earliest first attempt of the group's zone searches (section 5.2 item 3) |
| `cloud`, `region`, `sku`, `probe` | `provider`, `region`, `sku`, `probe_type` |
| `asked` | rule 5; with no answered zone, the largest `asked_z` of the group, or 0 |
| `gotZoneMax` | rule 5, the most that 1 zone placed (the headline `got`); 0 with no answered zone |
| `gotZoneSum` | rule 5, the sum of `result_z` over the answered zones (a lower bound); 0 with no answered zone |
| `state` | rule 4 |
| `nMax` | rule 5; else `gotZoneMax` |
| `reason` | `"quota"` or `"restricted"` when the state is `-1` and that outcome set it; else null. The server never copies free text into the payload. |
| `err` | the `error_code` of the first attempt of the zone search that set the state (the earliest first attempt among the zone searches with that state), or null |
| `zones` | rule 6: the number of zone searches of the group that asked a zone; an `aws_odcr` Region row (`restricted` or `error`, `zone` null) counts 0 |
| `zonesYes` | the number of zone searches of the group with `result_z` > 0 |
| `zoneList` | `aws_odcr` sweeps only, and only when a zone search of the group has a non-null `zone`: 1 entry `{zone, got}` for each such zone search, sorted by `zone`; `got` is `result_z`, or null when the zone did not answer. The key is absent on every other sweep (an Azure sweep, an `aws_odcr` Region row). The headline `got` stays `gotZoneMax` |

### 5.4 Capacity Block checks, Spot scores, and Spot prices

1. **Capacity Block checks** (`aws_cb_offering`, rows with `outcome` other than `error`). 1 place per (`sku`, `region`). Each row is 1 check: `[h, found, leadDays, durationH, fee, horizonDays, nRequested, leadH]`. `found` is 1 for `success`, else 0. `leadDays` = (`details.start` − `ts_utc`) in days, rounded to 2 decimals, for a `success` row with `details.start`; else null. `durationH` = `details.duration_hours`, `fee` = `details.upfront_fee`, `horizonDays` = `details.start_window_days`, each null when absent or 0. `nRequested` = `n_requested`, or 1 when null. `leadH` = (`details.start` − `ts_utc`) in hours, rounded to 4 decimals, for a `success` row with `details.start`; else null. AWS returns 0 or more offerings per call ([DescribeCapacityBlockOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)), and the probe writes 1 row per offering or 1 `none` row ([spec 0008](0008-aws-probes-spec.md) section 3), so 1 call can give several checks in 1 hour.
2. **Longest blocks** (`aws_cb_longest`, rows with `outcome` other than `error`), in the same place: `[h, found, earliestLeadDays, longestH, earliestH, earliestStartDate]`, from `details.earliest_start` (lead in days, 2 decimals, null when absent), `details.longest_duration_hours`, `details.earliest_duration_hours`, and the date part (`YYYY-MM-DD`) of `details.earliest_start` (or null).
3. **AWS Spot scores** (`aws_spot_score`, `outcome` = `success`, an integer `details.score`). Key `<sku>|<region>`, with `sku` as stored (a request with more than 1 type has a comma in `sku` and so forms its own key, which no machine reads). 1 point per hour `h`: `[h, mean score]`, the mean rounded to 2 decimals. The score is 1 to 10 ([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)).
4. **Azure Spot scores** (`az_spot_score`, regional rows: `zone` null). Key `<sku>|<region>`. 1 point per hour: `[h, high, medium, low, other]`, the count of rows whose `details.score` is `High`, `Medium`, `Low`, or anything else (also no score). Azure gives `High`, `Medium`, or `Low` ([Spot Placement Score](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).
5. **Spot prices** (`aws_spot_price` and `az_spot`, rows with a numeric `details.price` above 0, any `outcome`). Key `<sku>|<provider>`. The daily mean over all rows of that UTC date (all regions and zones), rounded to 4 decimals, in USD per machine-hour; null for a date with no such row. AWS gives 1 record per price change ([DescribeSpotPriceHistory](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)); Azure gives the latest price with an effective date, which in the stored rows is the first of a month ([spec 0009](0009-azure-spot-signals-spec.md) section 3.1).

Each list is sorted: checks and longest rows by `h`, then by the other fields in order; score points by `h`.

### 5.5 Full-resolution data layer

`make data/rows` and `make data/db` (`tasks/Makefile.data`) build the data layer from the local mirror of section 4.3. Nothing they write is committed: `data/` is git-ignored, and the files hold unscrubbed provider data. The board server reads the database (section 8).

1. **Daily files.** `scripts/compact-rows.sh <mirror> <rows dir>` (the make target passes `data/rows` as `ROWS_DIR`) writes `date=YYYY-MM-DD/<provider>_<probe_type>.jsonl.gz`, 1 file for each UTC day (the `date=` key), provider, and probe type. A file holds the lines of the source files exactly as stored, in file-name order: no field is changed and no row is dropped, for all probe types (also `aws_cb_earliest`, `aws_cb_longest`, and `az_precheck`). A day is built in a sibling folder and swapped in whole, so a killed run never leaves a day that looks complete. A source file under both layouts counts once, preferring the `experiment=` copy, the rule of section 5.1. The test is on the folders of the path under the mirror root: a folder name that starts with `experiment=` counts, a file name or the mirror root does not (the same test as `compact.py`). The script rebuilds a day only when the checksum of its source file names and sizes (`date=…/.sig`) changed, and always rebuilds the current UTC day. Every test on a path (`experiment=`, `provider=`, `daily/`) is on the path under the mirror root, so a mirror root that contains `experiment=` reads the same. At the start the script removes the work folders of a killed run, not those of a run that is still alive (the PID is in the folder name). A mirror argument of `/` (or only slashes) exits 2 before any scan.
   **Cloud daily files.** The store may hold a daily file for a finished day: `experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst`, with a sidecar `<probe_type>_<D>.jsonl.zst.manifest.json` next to it that has `row_count` (the number of newline bytes of the decompressed file, so a blank line counts). The reader rule: `scripts/compact-rows.sh` trusts a daily file only when the manifest has a parsable `row_count` above 0 and DuckDB decompresses the file to exactly that many lines. A file that fails the check (count mismatch, empty file, failed decompress, or sidecar without `row_count`) gets a warning that names the file, and the raw objects are used; when DuckDB returns an error, the warning prints it as returned. A daily file with no sidecar is not read and gets no warning: its raw objects are used (the sync of section 4.4 does not treat that pair as complete). A trusted file is copied unchanged to `date=D/<provider>_<probe_type>.<experiment>.jsonl.zst`. It skips only the raw objects of the same (day, provider, probe type) under the same `experiment=<name>/`; the raw objects of another experiment stay. The legacy `run_mode=` raw objects of that key are skipped too: each has an `experiment=` copy with the same base name (the data move of [spec 0003](0003-prober-core-spec.md) section 9A; no `run_mode=` object lacked a twin in the local mirror on 2026-10-27). The key holds no provider: the provider is the cloud folder of the mirror (`<mirror>/<provider>/experiment=<name>/daily/v1/...`, the layout of section 4.3), and a daily file outside a cloud folder is ignored. The `daily/` prefix is never read as raw rows: the test is on the path under the mirror root, so a mirror path that contains `daily` works. The daily file is referenced as a copy of the `.zst`, not re-packed to gzip: DuckDB `read_json` reads both `.gz` and `.zst` ([DuckDB: reading compressed JSON](https://duckdb.org/docs/stable/data/json/loading_json)). `make data/rows` runs the script through `mise exec --`, so the pinned DuckDB does the check. A mirror with no daily file builds as before. The sync that fills the mirror is section 4.4.
2. **Database.** `scripts/build-db.sh <rows dir> <file>` (the make target passes `data/ftg.duckdb` as `DB_FILE`; both scripts accept relative or absolute paths) reads the daily files (`.jsonl.gz` and `.jsonl.zst`) with `read_json` (which reads gzip and zstd) and writes the table `rows`: the 15 record fields of [spec 0003](0003-prober-core-spec.md) section 4.1 plus the legacy `run_mode` as typed columns (the types of section 5.1, except that `n_requested` and `n_placed` are `BIGINT`, so that a large count cannot fail the build (`latency_ms` stays `INTEGER`); `ts_utc` is `TIMESTAMPTZ` and `details` is `JSON`), with `simulated` resolved by the reader rule of section 5.1 (`run_mode` stays as stored). The database is written to `<file>.tmp.<pid>` and renamed, so 2 builds at the same time do not share a work file. The table has 1 row for each stored row and no bucket. It also has 1 view for each probe type, `v_<probe_type>`. A failed build keeps the last file.
3. **Tests.** `make test/db` checks, on a generated mirror, that the row count of every day and probe type is equal in the mirror (deduplicated), the daily files, and the database; that a day with a trusted daily file counts once (the same experiment's raw and the `run_mode=` objects skipped, another experiment's raw kept), that a daily file with a count mismatch, a truncated or empty file, or a manifest without `row_count` falls back to the raw objects with a warning (a daily file with no manifest falls back without one), that a mirror root that contains `experiment=` counts each row once, and that both clouds work; that a run with no new input rewrites no daily file; and that the current day is rebuilt.

## 6. Outcomes and the `details` contract

### 6.1 Outcome to sweep state

The first attempt's `outcome` gives the `state_z` of a zone search that did not answer (section 5.3). Rule 4 of section 5.3 gives the state of the sweep from its zone searches.

| `outcome` | `state` | Board meaning |
|---|---|---|
| `success`, `partial` | `2` or `1` by the rule of section 5.3 | the cloud handed us all, or some, of what we asked |
| `no_capacity`, `none` | `0` | asked; the cloud said no capacity |
| `quota` | `-1` | our quota blocked the question |
| `restricted` (Azure probes) | `-1` | a size restriction on our subscription blocked the question (section 6.2) |
| `restricted_or_no_capacity` | `-3` | Azure answered `SkuNotAvailable`, and the probe cannot tell a size restriction from no capacity ([spec 0010](0010-azure-provisioning-probes-spec.md) section 6; spec 0002 section 4.5). Its own state with its own caption: not dropped, and no quota wall. |
| `restricted` (`aws_odcr` Region row, `zone` null) | `-4` | not offered in this Region (section 6.2) |
| `restricted` (`aws_odcr` old row with a `zone`) | no sweep | not asked (section 6.2) |
| `timeout`, `error`, `cap`, any other value | `-2` | no answer; the board drops the sweep from every count |

### 6.2 `restricted` (decision R10)

- **`aws_odcr`.** The prober writes 1 `restricted` row for the Region (`zone` and `details.zone_id` null) when its pre-check finds no zone of the Region that offers the instance type, and then makes no attempt (spec 0002 section 4.9, `prober/ftg_prober/aws/odcr.py`). The pre-check is `DescribeInstanceTypeOfferings`, which "Lists the instance types that are offered for the specified location" ([DescribeInstanceTypeOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html)). The type is not sold there, so no quota increase changes the answer, and no capacity question was asked. **Decision:** a Region `restricted` row is a sweep with state `-4`, "not offered in this Region", with its own caption: the matrix tooltip says "AWS does not offer this type in this Region." (section 11.21). It is no quota wall and no capacity answer. An older `aws_odcr` `restricted` row has a `zone`: it meant "not offered in the 1 configured zone". It is "not asked": it is no sweep (section 5.3), and it is no quota wall.
- **Azure (`az_capacity_reservation`, `az_vmss_flex`).** `restricted` is the size restriction of the pre-check ([spec 0010](0010-azure-provisioning-probes-spec.md) section 4). Microsoft: "If a SKU isn't available for your subscription in a location or zone that meets your business needs, submit a SKU request to Azure Support." ([SKU not available errors](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)). It is a request to the cloud, like a quota increase, so it stays state `-1`, with `reason` `"restricted"`, and the quota walls name it (section 11.22).

### 6.3 The `details` contract (moved)

The table of `details` keys that each probe writes is in [spec 0003](0003-prober-core-spec.md) section 4.4. The board reads only the keys of section 6.4.

### 6.4 The `details` keys the board reads

| Probe types | Keys | Section |
|---|---|---|
| provisioning probes | `search_step`, `search_direction`, `n_config`, `cleanup` | 5.3 |
| `aws_odcr` | `zone_id` | 7.3 (`zoneStartH`) |
| Capacity Block offerings | `start`, `duration_hours`, `upfront_fee`, `start_window_days` | 5.4 |
| longest Capacity Block | `earliest_start`, `longest_duration_hours`, `earliest_duration_hours` | 5.4 |
| Spot scores, both clouds | `score` | 5.4 |
| Spot prices, both clouds | `price` | 5.4 |

A missing key or a key of the wrong type gives null in the record, never an error: the board must not stop on bad data.

## 7. Data boundary

### 7.1 Paths

The page reads exactly 2 paths, relative to `<base>`, the folder of the page (Vite `base: './'`, [Vite build](https://vite.dev/guide/build.html)):

| Path | Body |
|---|---|
| `GET <base>/api/meta.json` | the meta object of section 7.3 |
| `GET <base>/api/window/<hz>/<page>.json` | the window object of section 7.4 |

`<hz>` is `day`, `week`, `month`, or `all`; every horizon reads `day`. `<page>` is the 0-based index of the page in the page list of section 11.2, in decimal with no leading zero. The server (section 8) and the static export (section 9) answer the same paths with the same bytes. A third source, for example DuckDB-Wasm on Parquet files, can replace either one if it answers the same paths.

### 7.2 The `DataSource` client

`web/src/data/` has 1 client, `DataSource`, with 2 methods: `meta()` and `window(hz, page)`. Each fetches its path relative to the page's base URL and parses the JSON. The client keeps each response in memory for the life of the page, so going back to a page sends no new request. No other code of `web/src/` calls `fetch`. The derive layer gets the parsed objects, never the client.

### 7.3 `meta.json`

Over all rows that the board reads (section 5.2), not only a window:

| Field | Type | Value |
|---|---|---|
| `t0` | ISO 8601 string | hour 0 (section 5.2 item 3) |
| `tEnd` | ISO 8601 string | the latest `ts_utc` of the rows that set `t0` (the same rule of section 5.2 item 3, with its fallback) |
| `nh` | integer | ceil((`tEnd` − `t0`) / 1 hour) + 1 |
| `dates` | array of `YYYY-MM-DD` | every UTC date from the date of `t0` to the date of `tEnd`, both included, 1 entry for each date (the first date is the date of `t0`; the last date is the date of `tEnd`, also when the time of day of `tEnd` is before that of `t0`) |
| `weekends` | integer | the number of `dates` whose 12:00 UTC falls on a Saturday in America/New_York |
| `cadenceH` | object `{launch, cb}` | `launch`: the median gap in hours (the upper median, element floor(n/2) of the sorted gaps, rounded to 2 decimals) between consecutive sweeps (`t`) of 1 (cloud, region, sku); `cb`: the same over the distinct hours `h` of the Capacity Block checks of 1 (region, sku), so several checks in 1 hour make 1 point and no gap of 0; null with no gap |
| `probeStartH` | object, `"<cloud>:<probe>"` to number | the smallest sweep `t` of each provisioning probe type |
| `zones` | array of strings, sorted | the distinct non-null `zone` values of the `aws_odcr` attempt rows with `h` < `zoneStartH` (all such rows when `zoneStartH` is null): the zones of the 1-zone method |
| `cbHorizonDays` | array of integers, ascending | the distinct `details.start_window_days` of the `aws_cb_offering` rows |
| `regions` | array of `"<cloud>\|<region>"`, sorted | every (cloud, region) with a sweep or a Capacity Block place |
| `rows` | integer | the number of rows of the 9 probe types that the board reads (section 5.2) |
| `sweeps` | integer | the number of sweeps |
| `firstH` | object, `sku` to integer | the smallest sweep `h` of each SKU, any state |
| `cbFirstH` | object, `sku` to integer | the smallest `h` of the 1-day, 1-machine checks of each SKU (section 11.3) |
| `upStartH` | integer or null | the smallest sweep `h` with `nMax` > `asked` (the doubling start) |
| `zoneStartH` | integer or null | the smallest `h` of an `aws_odcr` row with a non-null `details.zone_id`: the start of the per-zone probe (section 5.3 rule 7); null with no such row |
| `simulated` | boolean | true when the board shows simulated rows (section 5.2 item 2) |
| `now` | object `{sweeps, places, cb}` | the latest answer of every machine, over the whole data, for section 11.9; below |

`now` holds what section 11.9 reads outside any window: its answer comes from the latest round in the data, not from the window in view. With the cadence `c` of section 7.4:

- `sweeps`: for each `sku`, the sweeps (the shape of section 5.3) with a state other than −2 whose round floor(`h` / `c`) is the largest such round of that `sku`; sorted as in section 7.4.
- `places`: 1 entry `{sku, cloud, region, probe, n, na, yes, nu, no, lastYesH}` for each (`sku`, `cloud`, `region`) with a sweep of a state other than −2, sorted by `sku`, `cloud`, `region`. The counts are the place counts of section 11.3 item 2 over the whole data: `n` (sweeps with state ≥ −1), `na` (state −1), `yes` (`got` > 0), `nu` (state −3), `no` (state −4). `probe` is that of the first sweep; `lastYesH` is the largest `h` of a sweep with `got` > 0, or null.
- `cb`: 1 entry `{sku, region, h, leadH, durH, start, longestH}` for each Capacity Block place (section 5.4), sorted by `sku`, `region`. `h` is the largest `h` of the place's 1-day, 1-machine checks (section 11.3 item 4), or null. Of the found checks at that `h`, the one with the smallest `leadH` gives `leadH`, `durH` (its `durationH`), and `start` (its `details.start`, an ISO 8601 string); all 3 are null when no check at `h` was found. `longestH` is the largest `longestH` of the found `aws_cb_longest` rows at the largest `h` of the place's longest rows, or null.

`firstH`, `cbFirstH`, `upStartH`, `zoneStartH`, `simulated`, and `now` are not in the design's data file. The first 4 hold the reads of the design's derivation that reach outside any window (the first ask of a machine, the doubling start, the start of the per-zone probe), so that a window payload is enough; `simulated` sets the banner of section 5.2 item 2. A database with no row of the 9 types gives `{"t0": null, "simulated": false}`, and every window path answers 404.

### 7.4 Window payload

For page `<page>` of horizon `<hz>`, with `H0` and `H1` of section 11.2, and the cadence `c` = `meta.cadenceH.launch`, or 2 when null or 0:

- `L` = min(`H0`, max(0, 2 × `H0` − `H1`)), the start of the range before the page that the trend compares with (section 11.13). It is the page length counted back from `H0`, not the previous page of the list.

| Field | Value |
|---|---|
| `hz`, `page` | the request |
| `H0`, `H1`, `L` | integers, section 11.2 and above |
| `sweeps` | every sweep with `L` ≤ `h` < `H1`: every SKU (also a control SKU that no machine names) and every state (also `-2`, `-3`, and `-4`), in the shape of section 5.3 (with `gotZoneMax`, `gotZoneSum`, `zones`, `zonesYes`, and, for an `aws_odcr` sweep with a named zone, `zoneList`), sorted by `h`, `t`, `cloud`, `region`, `sku`, `probe` |
| `cb` | 1 entry `{sku, region, checks, longest}` for each Capacity Block place of the whole data, also a place with no record in range, sorted by `sku`, `region`; `checks` and `longest` hold the records with `L` ≤ `h` < `H1` (section 5.4) |
| `spot` | `{"<sku>\|<region>": [[h, score], …]}`, the points with `H0` − `c` ≤ `h` < `H1`; a key with no point is left out |
| `azspot` | `{"<sku>\|<region>": [[h, high, medium, low, other], …]}`, the same range |
| `price` | `{dates, series}`: `dates` is `meta.dates`; `series` maps `"<sku>\|<provider>"` to 1 value per date (section 5.4 item 5). The whole span: 1 number per series per day. |

The `all` horizon has 1 page, the whole data set, and it is the default view. Its payload grows with the span.

The derive layer joins `meta` and the window into the design's data shape `{meta, sweeps, cb, spot, azspot, price}` and runs on it (section 10.1). The join gives each sweep the design's field `got` = `gotZoneMax` (section 5.3 rule 5), so every rule of section 11 that reads `got` reads the most that 1 zone placed; only the tooltips that name `gotZoneSum` read the sum.

### 7.5 Encoding

UTF-8 JSON, keys in the order of the tables above, numbers as JSON numbers, `null` for a missing value, no `NaN`. The same database gives the same bytes on every request, so the static export can equal the server byte for byte.

## 8. Server

### 8.1 Handler

`web/server/` holds 1 request handler: it takes a path and returns a status, a content type, and a body. It answers the 2 paths of section 7.1 and nothing else.

1. It opens the database file named by `WEB_DB` (section 12) with `@duckdb/node-api` and the setting `access_mode = 'READ_ONLY'` ("Access mode of the database (`AUTOMATIC`, `READ_ONLY` or `READ_WRITE`).", [DuckDB configuration](https://duckdb.org/docs/stable/configuration/overview)). It never writes the file.
2. It reads the file again on the next request after `make data/db` replaced it (decision R1). Implementer's choice: open the file for each request, or keep the payloads in memory keyed by the file's size and modification time.
3. It computes the page list with the same derive function as the page (section 11.2). The server may import the derive layer; the derive layer never imports the server.
4. It holds no cloud credential, loads no cloud SDK, and makes no network call. It needs no DuckDB extension that is not built in.
5. Responses: `200` with `Content-Type: application/json` for a known path; `404` with `{"error": "<reason>"}` for any other path, a bad `<hz>`, or a `<page>` out of range; `503` with `{"error": "no database at <path>; run make data/db"}` when the file is missing. The path in the message is relative to the repo.

### 8.2 Window SQL

The SQL of sections 5.3, 5.4, 7.3, and 7.4 lives in `web/sql/`, 1 file for each part (for example `meta.sql`, `sweeps.sql`, `cb.sql`, `spot.sql`, `azspot.sql`, `price.sql`). The handler passes `t0`, `L`, `H0`, `H1`, and the cadence as bound parameters, never by string concatenation. Each query reads the table `rows` of section 5.5. `sweeps.sql` groups in 2 levels: the zone searches, then the groups of section 5.3 rule 2. Implementer's choice: whether a query returns JSON from DuckDB or rows that the handler serializes, as long as section 7.5 holds.

### 8.3 Dev and run

1. `web/server/` also exports a Vite plugin. Its `configureServer` hook mounts the handler on `<base>api/` ([Vite plugin API](https://vite.dev/guide/api-plugin.html)). `vite.config.ts` adds the plugin, so `npm run dev` serves the page and the API on 1 origin.
2. `make web/run` runs the same Vite dev server with the same plugin, bound to `127.0.0.1` on `WEB_PORT` (section 12). There is no second server.

### 8.4 Tests

`make web/test` runs these with no network and no credential:

1. **Fixture database.** The test writes fixture rows in the mirror layout (section 4.1) and builds the database with the real `scripts/compact-rows.sh` and `scripts/build-db.sh` (section 5.5), not a hand-made table.
2. **Payloads.** For the fixture, `meta.json` and each window path equal the expected payloads written in the test; each rule of sections 5.3 and 5.4 has a fixture case (a full yes, a halving yes, an upward search, a cleanup row, an `aws_odcr` Region `restricted` row, an `aws_odcr` Region `error` row (`zones` 0), an old per-zone `aws_odcr` `restricted` row, an old `aws_odcr` row with its configured zone and no `details.zone_id`, a multi-zone `aws_odcr` Region with mixed zones, a multi-zone Region where 2 or more zones placed (`gotZoneSum` > `gotZoneMax`), an error zone next to an answered zone, an Azure `restricted` row, a `quota` row, a `restricted_or_no_capacity` row, a machine whose only sweeps are state −3 or −4, a simulated row next to real rows, a database of simulated rows only, and a database whose only real rows are Spot rows next to simulated provisioning rows (`t0` from the fallback of section 5.2 item 3)).
3. **The window loses nothing.** For each `<hz>` and `<page>` and for 2 tiers and 2 focus machines, the derive output on the window payload equals the derive output on a whole-data payload of the same database (every record, no range). This proves that section 7.4 holds every input the derive layer reads.
4. **Read-only and versions.** The handler cannot write the file (a write statement through its connection fails), and `SELECT version()` gives the same value through `@duckdb/node-api` and the pinned `duckdb` CLI.

## 9. Static export

`make web/export` writes a folder that works with no server:

1. It builds the database as `make web/run` does (section 12), then runs `vite build` into `web/dist/` with `base: './'`.
2. `web/scripts/export.ts` calls the handler of section 8.1 for `api/meta.json` and for every `api/window/<hz>/<page>.json` of every horizon and page (section 11.2), and writes each body to the same path under `web/dist/`.
3. Each written file equals the server's response body byte for byte (a test compares them on the fixture database).
4. `npm run preview` (`vite preview`, [Vite CLI](https://vite.dev/guide/cli.html)) serves `web/dist/`, and the board works from it under any sub-path.

`web/dist/` is git-ignored. It holds real counts, including the quota walls of the organization, so share it outside your machine only on purpose. No payload names a bucket, a storage account, an account ID, or a subscription ID.

### 9.1 Standalone file

`make web/standalone` writes 1 HTML file that opens from `file://` with no network, because a browser blocks `fetch` of a local JSON file. `web/scripts/standalone.ts` reads the folder of section 9 and inlines the built CSS and the module script, keeps only the Latin `woff2` Inter faces as `data:` URIs, and puts every `api/**/*.json` body, minified and with nothing dropped, in `window.__FTG`. A script before the module replaces `window.fetch` with a lookup in that object (200 with the body, else 404). The file holds no URL except the XML namespace and error-page strings of React: `w3.org`, `react.dev`, and the inert links in React's error text (`github.com`, `reactjs.org`, `developer.mozilla.org`, `bugs.webkit.org`), none of which is requested; a test checks this, that the notices block holds every bundled package name, that no latin-ext face is embedded, and the lookup. The file also holds the license texts of the bundled packages (`web/public/THIRD_PARTY_NOTICES.txt`) in 1 non-rendered `<script type="text/plain" id="third-party-notices">` block, with `</` escaped; the file stays 1 file. The URL check skips only that block. `OUT` sets the path (default `web/dist/standalone.html`). The same rule as section 9 applies to a share of the file: it holds real counts.

## 10. Logic boundary

### 10.1 Derive layer

`web/src/board/derive/` holds pure TypeScript functions. The input is the joined data (section 7.4), the view state (section 11.5), and the constants of section 11.1. The output is plain data: every number, label, caption, tooltip title and lines, and colour token name of section 11, in the field structure that the view draws. A function has no side effect and reads no clock, no `localStorage`, no `location`, and no DOM: the same input gives the same output. The core (catalogue, time model and pages, per-machine aggregation, formatters) is shared; each board section of section 11 has its own derive file.

**Singular (board-wide).** Every counted noun of every board text uses the singular when its count is 1: in a caption, an answer, a tooltip line, a table cell, and a footnote, and also after "of", "all", and "the". The words are "place", "region", "round", "check", "day", and "zone". Examples: "1 place", "1 of 1 place said yes", "1 of 1 region had a 1-day block", "1 of 1 round", "median of 1 check", "In the 1 round when no place handed us one". A count of 0 or 2 or more uses the plural. The derive layer builds each such text with 1 shared helper, `plural(n, noun)`, in `web/src/board/derive/format.ts`.

### 10.2 View layer

`web/src/board/view/` holds 1 React component per board section. A component calls its derive function (memoized on the inputs), draws the result, and calls back on clicks with a state change. Every section view gets the optional `setState` prop (`SectionProps`), which the app passes; a view changes the view state only through it and never writes the hash itself. The app follows the state to the hash with `replaceState` (section 11.5), so a focus change adds no history entry. It computes no number and writes no caption. A change of view stack rewrites only this folder and `web/src/components/`.

### 10.3 Lint rule and type check

1. `web/biome.json` has an `overrides` entry for `src/board/derive/**` with the rule `style/noRestrictedImports` ([Biome noRestrictedImports](https://biomejs.dev/linter/rules/no-restricted-imports/)). Its `patterns` forbid `react`, `react-dom`, `react/*`, any path into `view/`, `components/`, `server/`, or `data/`, `node:*`, and `@duckdb/*`.
2. `web/src/board/derive/tsconfig.json` sets `lib` without `DOM` ([TypeScript `lib`](https://www.typescriptlang.org/tsconfig/#lib)), and `make web/lint` type-checks it on its own. A DOM global such as `document` or `window` is not an import, so only this check finds it.

### 10.4 Derive tests (decision R11)

1. `web/test/fixtures/board/events.json` holds a seeded, synthetic fixture of events: 3 weeks, both clouds, every machine mode of section 11.3, a halving yes, a doubling start inside the span, Capacity Block jumps and creep, and Spot scores and prices. Its `t0` is 07:00 UTC, not an America/New_York midnight, so the tests show the hour and day rules of section 14 (C1, C2).
2. The expected values are written in the tests themselves, as numbers and captions, for the fixture. They cover each horizon and page, 3 tiers (`All` and 2 others), and 3 focus machines, including the tooltip title and lines of every hover target. They apply the corrections C1 to C8 of section 14. Section 11.9 ("Right now") has its own expected values on the same fixture, for each horizon of section 11.9 (`day` included) and page, with corrections C9 to C12 applied, and with `meta.now` (section 7.3) built from the fixture by the rule of section 7.3. The repo holds no stored output files of a reference derivation ("golden files") and no real data.
   `make web/fixture` writes `events.json` again with `web/scripts/gen-board-fixture.mjs`. The generator uses a fixed seed and no clock, so the file is byte for byte the same each time.
3. Each board section's test asserts that its derive output equals its expected values.

## 11. The board

The sections run top to bottom in the order 11.7 to 11.22. Sections 11.1 to 11.6 are shared rules. Each section from 11.7 on is 1 block that can be edited on its own. A "design spec" below is a value of the design (the design tool owns colours, type, spacing, and chart geometry); the view uses the Nocturne CSS variables named here and hard-codes no colour. In the caption templates, `{x}` is a value, `·` is a literal middle dot, and the quotes are not part of the text.

### 11.1 Machine catalogue

A row of the board is a machine, not a chip: an 8-GPU node and a single card are different products with different buyers. The catalogue is a constant of the derive layer, in this order:

| Machine | AWS type | Azure size | Tier |
|---|---|---|---|
| B200 ×8 | `p6-b200.48xlarge` | | Frontier node |
| H200 ×8 | `p5en.48xlarge` | | Frontier node |
| H100 ×8 | `p5.48xlarge` | | Frontier node |
| A100 ×8 | `p4d.24xlarge` | | Frontier node |
| H100 ×1 | `p5.4xlarge` | `Standard_NC40ads_H100_v5` | Single big GPU |
| A100 ×1 | | `Standard_NC24ads_A100_v4` | Single big GPU |
| RTX PRO 6000 | `g7e.2xlarge` | | Single big GPU |
| L40S | `g6e.xlarge` | | Mid · 48 GB |
| A10G | `g5.xlarge` | | Small · 16–24 GB |
| A10 | | `Standard_NV36ads_A10_v5` | Small · 16–24 GB |
| L4 | `g6.xlarge` | | Small · 16–24 GB |
| T4 | `g4dn.xlarge` | `Standard_NC4as_T4_v3` | Small · 16–24 GB |

The 4 `×8` types have 8 GPUs per machine; every other type has 1 ([AWS accelerated computing specifications](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html); Azure: [NCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series), [NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series), [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series), [NCas T4 v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series)). GPUs per machine = 8 for the 4 `×8` AWS types, else 1, read from the first type of the machine.

Tier subtitles: Frontier node "8 HBM GPUs, NVLink — 400B+ models, multi-node training"; Single big GPU "80–96 GB — 70B quantised, 30B at FP16, fine-tuning"; Mid · 48 GB "7–30B models"; Small · 16–24 GB "≤13B models". The memory ranges are the sources' GPU memory: 80 GB (H100 and A100), 94 GB (the Azure H100 NVL), 96 GiB (RTX PRO 6000), 44 GiB listed for the L40S, and 16 to 24 GB for the small cards.

Coasts: `us-east-1`, `us-east-2`, `eastus`, `eastus2` are East; `centralus`, `northcentralus`, `southcentralus` are Central; `us-west-2`, `westus`, `westus2`, `westus3` are West. Clouds print as "AWS" and "Azure".

Constants: `minAsks` = 8 (decision R8); the Capacity Block horizon is `meta.cbHorizonDays[0]`, or 14, printed "{n} days".

### 11.2 Time model, pages, and rounds

1. `T0` = `meta.t0`, `TEND` = `meta.tEnd`, `NH` = `meta.nh`, `CAD` = `meta.cadenceH.launch`, or 2 when null or 0.
2. **Pages.** `day`: UTC calendar days, from the day of `T0` while the day start is ≤ `TEND`. `week`: each page runs from a Monday 00:00 in America/New_York to the next Monday 00:00 there (168 hours, or 167 or 169 across a daylight-time change; correction C2). The first page holds `T0`; pages continue while their start is ≤ `TEND`. `month`: UTC calendar months, from the month of `T0` while the month start is ≤ `TEND`. `all`: 1 page from the UTC midnight of `T0` to `TEND` + 1 ms.
3. **Default page.** Coverage of a page = (min(end, `TEND`) − max(start, `T0`)) / (end − start). The default is the last page; while its coverage is below 0.5 and it is not the first page, the page before it. For `day` the default is the last page. When the view state has no page, the shell asks the source for this default page (never page 0), so every section frames the same page.
4. **Window.** `H0` = round((start − `T0`) / 1 h); `H1` = min(`NH`, ceil((end − `T0`) / 1 h)); if `H1` ≤ `H0`, `H1` = `H0` + 1. `H0` can be negative. A record is in the window when `H0` ≤ `h` < `H1`. `H1` uses ceil, not round, because the `all` page ends at `TEND` + 1 ms, which is not a whole hour: round would drop the hour that holds `TEND` (a record has `h` = floor((`TEND` − `T0`) / 1 h)) from [`H0`, `H1`), and the latest sweeps would be lost. For `week` and `month` the end is a whole hour, so ceil and round give the same `H1`.
5. **Grain.** `day`: 1 round. `week`: 1 round (`CAD` hours), named "round". `month`: 24 h, "day". `all`: 24 h if (`H1` − `H0`) / 24 ≤ 60, else 168 h, "week". `NB` = ceil((`H1` − `H0`) / grain) cells; `NR` = ceil((`H1` − `H0`) / `CAD`) rounds; a record's round index is floor((`h` − `H0`) / `CAD`).
6. **Page label.** `day`: "{Wed Oct 29} (UTC)" (weekday short, month short, day numeric, UTC). `all`: "all {number of meta.dates} days". `week`: "Week of Mon {Mon D of the start in America/New_York} (ET)". `month`: "{Month YYYY}" of the start in UTC.
7. **Hour of day and day boundaries (decision R6, corrections C1 and C2).** Every hour of day that the board prints or groups by is the real UTC hour of `T0` + `t` (or `T0` + `h`), never `t` mod 24. Weekend means Saturday or Sunday in America/New_York, for the hour `T0` + `h`.
8. **Empty window.** A section that tests for an empty window (intro, so what, stickiness, substitutes, forecast, matrix, definitions, backtest) counts only a record with `H0` ≤ `h` < `H1`; a record in the lookback range only does not. Stickiness, substitutes, forecast, definitions, and backtest count only a sweep. The intro, so what, and matrix count a sweep or a Capacity Block check. Section 11.9 has no empty state: its answer reads `meta.now`, and its window columns say "not asked over {window}". (`hasWindowSweep` and `hasWindowData` in `web/src/board/derive/inwindow.ts`.)

### 11.3 Per-machine aggregation

For every machine of the catalogue (all tiers, because the substitutes read every machine), over the window:

1. `S` = the window sweeps with `state` ≥ −1. The states −2 (no answer), −3 (`restricted_or_no_capacity`), and −4 (not offered in this Region) are no capacity answer and no quota answer (section 6.1); −3 and −4 show only in the places (item 2). A machine's sweeps `sw` = those of `S` whose `sku` is 1 of its types.
2. **Places.** 1 place per (cloud, region): `probe` (of the first sweep), `cells` (1 per round, null at first), `n` (sweeps), `na` (sweeps with state −1), `yes` (sweeps with `got` > 0), `full` (state 2), `got` and `asked` (sums over state ≥ 0), and `reason` (the first non-null reason of a −1 sweep). A place also counts the machine's window sweeps of state −3 (`nu`) and −4 (`no`); a place with only such sweeps has `n` = 0, and these sweeps set no cell and no other count. A −1 sweep sets its round cell to −1 only when the cell is null. Any other sweep sets the cell to the max of its state and the cell (a null or −1 cell counts as 0).
3. **Union.** For each round, the max over the places that have a cell; −1 only when every such place is −1; null when no place has a cell.
4. **Capacity Block places.** The 1-day, 1-machine checks: `nRequested` null or 1, and `durationH` null or 24. For each place of the machine's AWS types with at least 1 such check in the window: `n` (checks), `found`, `medLead` (median lead of the found checks), `nowFound` (the found checks with `leadH` under 1: the **now rule**), round `cells` (max over the round's checks of 1 for a found check with `leadH` under 1, else 0; null with no check), `leadH` and `durH` (per round: the smallest `leadH` of the found checks of the round, and the `durationH` of that check; null with no found check), `longestH` (max `longestH` of the found longest rows in the window, or null), `longestMedH` and `longestLead` (medians of those rows), `nLong`, and `fee` (median non-null fee of the found checks). The block union is the max over these places per round. A Capacity Block offer is a yes to "right now" only when it starts less than 1 hour after the check ([DescribeCapacityBlockOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html) gives the start of each offer). An offer that starts 1 hour or later is a found check but a 0 in the cell; the board shows it as its start and its length (section 11.9). `found`, `medLead`, and the other fields of this item still count every found check, because Book ahead (section 11.16) and the sawtooth read the full when-and-how-long view.
5. **Mode.** `now`: a sweep with state ≥ 0 exists. `quota`: sweeps exist and all are −1. `block`: no sweep, but a Capacity Block place exists. `none`: nothing. A machine whose only window sweeps are state −3 or −4 has mode `none`, but its places count them (`nu`, `no`, item 2), so its captions say "not offered" or "not available" as the matrix does, never "not asked" (sections 11.9, 11.10, and 11.21).
6. **Series.** `src` is the union for modes `now` and `quota`; for `block`, the block union with 1 shown as 2; else the union.
7. **Counts.** Over the rounds of `src` that are not null and not −1: `tried`, `yes` (value > 0), and the longest wait: the largest of the gap between 2 yeses (counted in round indexes, only when a no came between), the lead before the first yes, and the trail after the last yes. The lead counts from the first counted round to the first yes, and the trail from the last yes to the last counted round; a round that is null or −1 is not counted, so it never starts or ends a lead or a trail. With `tried` > 0 and `yes` = 0 there is no gap, lead, or trail: `waitH` is 0 and `waitKind` is null, and the board shows the "never, in {hrs(tried × CAD)} of asking" text of section 11.9. Otherwise the wait is 0 when that largest value is ≤ 1, else value × `CAD` hours, named "between two yeses", "since the last yes", or "before the first yes" (in that order of preference when equal).
8. `reqs` = the distinct `asked` of the machine's sweeps with state ≥ 0, ascending.
9. **First ask.** `famStart` = `meta.cbFirstH` of the first AWS type for mode `block`, else the smallest `meta.firstH` over the machine's types; infinite when absent. A cell is **pre-probe** when it is not asked and `H0` + (index + 1) × grain ≤ `famStart`.
10. **Folded cells.** At a grain above 1 round, a cell folds its rounds: `n` (rounds not null and not −1), `y` (of those, value > 0), `share` = 100 × `y` / `n`; with `n` = 0, a cell with a −1 round shows −1, else not asked.

### 11.4 Formatting and colour helpers

- Count: `en-US` grouping (`1,234`); null prints "–".
- Percent `ps(p)`: null "–"; 0 < p < 1 "<1%"; 99 < p < 100 "99%"; else round(p) + "%".
- Share `sh(a, b)`: 100 × a / b when b > 0, else null.
- Hours `hrs(h)`: h < 48 gives "{round(h)} h"; else "{round(h / 24, 1 decimal)} d".
- Median: the element floor(n / 2) of the sorted list; null for an empty list.
- Dates (UTC, `en-US`): day "13 Tue" (`Intl` weekday short and day numeric), date "Oct 13" (month short and day).
- `heat(p)`: `color-mix(in oklch, var(--color-accent-400) {round(8 + 92 × (p / 100)^0.8)}%, var(--color-neutral-900))`; transparent for null.
- `tone(p)`: null `--color-neutral-500`; ≥ 60 `--color-accent-300`; ≥ 25 `--color-neutral-200`; else `--color-neutral-400`.
- Hatch: `repeating-linear-gradient(45deg, var(--color-neutral-700) 0 1.5px, transparent 1.5px 4px)`.
- Text on a heat fill flips to `--color-bg` at p ≥ 45.
- The focus machine's name is `--color-accent-300` on every row of every section; other names are `--color-text`.

### 11.5 View state (decision R9)

| Key | Values | Default |
|---|---|---|
| `hz` | `day`, `week`, `month`, `all` | `all` |
| `page` | integer, or absent for the default page (section 11.2) | absent |
| `tier` | `All` or a tier name | `All` |
| `gpu` | a machine name | `H100 ×1` |

The state goes to `localStorage['ftg.v2.view']` as JSON and to the URL hash (`#hz=week&page=1&tier=All&gpu=H100%20%C3%971`, values URI-encoded, absent keys left out). The app writes the hash with `history.replaceState`, not `location.hash`, so a state change adds no history entry. On load the hash wins over `localStorage`; a hash that names a different `hz` and no `page` opens the default page, not the stored page of the other horizon. A change of `hz` clears `page`. A hash with an unsupported `hz` (for example `year`) ignores its `page` too, so the page of the wrong horizon never applies to the stored horizon. A `page` outside the page list of the horizon (for example 99 or -1) is clamped to the first or last page before the shell fetches the window, so the fetched page, the frames, and the pager buttons agree, and the state and the URL hash then hold the clamped page. A focus machine outside the tier snaps to the first machine of the tier. The open rows of section 11.9 are view state but are not stored. Section 11.9 follows this state and has no window state of its own (section 11.9, "Window"). There are no other controls; the design's tweak properties are not built (decision R8).

### 11.6 Layout, tooltip, loading, and accessibility

Design specs:

- Page background `#0f1019`, padding `clamp(16px, 3vw, 48px)`. The board: max width 1280 px, min width 960 px inside an `overflow-x: auto` wrapper (decision R5), background `--color-bg`, radius `--radius-lg`, `--shadow-sm`, padding 28 32 32 px, a column with a 30 px gap. Dark theme only (decision R3).
- Font Inter 400, 500, and 600, self-hosted (decision R4), `font-variant-numeric: tabular-nums`, antialiased. Sizes 8, 9, 10, 11, 12, 13, 14, 15, 17, 18, 20, 22 px. Letter-spacing −0.01em on names, −0.02em on big numbers, +0.06em or +0.07em on uppercase kickers.
- Section title: 15 px / 500, an optional 12 px `--color-neutral-500` note on the same baseline (gap 12 px), then a 12 px / 1.5 `--color-neutral-500` intro (`text-wrap: pretty`). An analysis section is a grid `minmax(0,1fr) 300px`, gap 32 px: the chart left, and the verdict right (12 px / 1.55 `--color-neutral-300`, padding-top 26 px).
- Machine names 13 px / 600.
- **Tooltip.** 1 global element with `role="tooltip"`, fixed, background `--color-neutral-100`, text `--color-bg`, 12 px / 1.45, padding 7 10 px, radius `--radius-md`, `--shadow-md`, max width 300 px, at the cursor + 14 px / + 14 px, flipped left when it would pass the right edge. A bold title line, then 1 line for each non-empty text. Each cell, bar, and sparkline shows it on mouse move and hides it on mouse leave. The hide waits 1 animation frame, and a show in that frame cancels it, so a pointer that moves from 1 cell to the next, across a gap, never unmounts the tooltip. The tooltip and its 1 px anchor take no pointer events. A strip row picks its cell from the pointer x, so a gap between 2 cells shows the nearest cell. The texts are the "Tooltip" lines of each section.
- **Loading.** Before the data arrives, the header and the controls render, and the sections stay empty. No spinner. A failed request shows 1 line with the path and the HTTP status in place of the sections. An empty store (section 7.3) shows "No rows in this store. Run make data/sync, then make web/run."
- **Banner.** With `meta.simulated` true, the line of section 5.2 item 2 shows above the intro, 13 px `--color-accent-300`.
- No animation. The design system's `:hover`, `:focus-visible` (2 px accent outline), and `::selection`; no custom focus ring. `aria-expanded` on the machine rows of section 11.9; `aria-label` on the pager buttons and the selects.

### 11.7 Header, horizon and pager, tier and focus

- Title "follow-the-gpus", then the span caption: "{date of T0} – {date of TEND}, {UTC year of T0} · {number of meta.dates} days · {meta.rows with grouping} probe rows · {meta.weekends} weekend{s}" ("weekend" when 1).
- **Horizon.** 4 buttons "Day", "Week", "Month", "All" in a group with `role="radiogroup"` (each `role="radio"`, `aria-checked`). A pick sets `hz` and clears `page`.
- **Pager.** "‹" (`aria-label="Earlier"`) and "›" (`aria-label="Later"`), disabled at the first and the last page, around the page label of section 11.2. The 2 buttons and the label form 1 non-wrapping group, so a wrap never separates them.
- **Tier.** A native `<select>` (`aria-label="Tier"`): "All machines" (value `All`), then the 4 tiers.
- **Focus.** A native `<select>` (`aria-label="Machine"`, title "Focus drives the substitutes panel, the time-of-day grid and the highlighted row elsewhere") of the machines of the tier.
- The machine list `gl` of every section is the machines of the tier, in catalogue order.

Design spec: a wrapping flex row, gap 14 px; each label and its control form 1 non-wrapping group (Horizon and its buttons, Tier and its select, Focus and its select), so a wrap never separates them. Title 13 px / 600, −0.01em; the span 13 px `--color-neutral-500`. "Horizon", "Tier", "Focus" labels 12 px `--color-neutral-400`. Segmented control: 1 px `--color-neutral-800` border, radius `--radius-md`; buttons 12 px / 500, padding 6 12 px; selected background `--color-accent-900`, text `--color-accent-200`; others transparent, `--color-neutral-400`. Pager buttons: 1 px `--color-neutral-800` border, padding 5 9 px; the label 12 px / 500, min width 150 px, centred. Selects: the design system input style, padding 6 32 6 12 px, weight 500.

### 11.8 Intro and headline chips

- **Intro.** "Every {hrs(CAD) with "h" written "hours"} we ask AWS and Azure, in {number of meta.regions} US regions, to hand us a real GPU machine. Showing {span}{partial}." `{span}` is "all {number of meta.dates} days" when `H1` − `H0` = `NH`, else "{d} day{s}, {date of the window start} – {date of the window end}" (when both dates are the same, "{d} day{s}, {date}", for example "1 day, Oct 29"), with window start = max(`T0`, `T0` + `H0` h), window end = min(`TEND`, `T0` + `H1` h − 1 ms), and `d` = max(1, ceil((end − start) / 1 day)). `{partial}` is " (a partial {hz}: the data starts or ends inside it)" when `hz` is not `all` and the page is not covered in full: the page start is before `T0`, or the page end (exclusive) is after `TEND` + 1 ms. A page that the data covers in full has no `{partial}`. Then the tier subtitle, or for `All`: "Rows are machines, not chips: an 8-GPU node and a single card are different products with different buyers."
- **Empty window.** A window with no sweep and no Capacity Block check shows no intro, banner, or chip (the section stays an empty labelled frame). Only a sweep or a check with `H0` ≤ `h` < `H1` counts; a record in the lookback range only does not.
- **Groups**, over `gl`, each shown only when it has a machine, in this order: "Yes almost always" (mode `now`, `yes` / `tried` ≥ 0.9); "Yes, sometimes" (0 < rate < 0.9); "Never, anywhere" (tried, no yes); "Quota-blocked" (mode `quota`); "Not asked in this window" (mode `none`, or mode `now` with `tried` = 0). A machine of mode `block` has no group and no chip: section 11.9 answers it as "Yes", "Not now", or "Book ahead" with its start.
- **Chips.** Rate chips (the first 2 groups) are sorted by rate, high first: big `ps(rate)`, a bar of the rate, sub "{yes} of {tried} rounds had a yes somewhere". Never: big "0%", sub "0 of {tried} rounds; no place handed us one". Quota-blocked: big "quota", sub "our own limit refused every ask in {n} region{s}; not a capacity answer" (n = places). Not asked: big "–", sub "no probes for this machine in the days shown".
- **Footnote.** "A yes means the cloud actually handed us the capacity — never a forecast, never a listing. Definitions at the bottom. Every caption on this board is computed from the data in view, not written for one run."

Design spec: intro 13 px `--color-neutral-500`, the subtitle `--color-neutral-400`. Group rows: grid `150px 1fr`, gap 16 px; tag 11 px / 600 uppercase +0.07em, padding-top 12 px, coloured `--color-accent-300`, `--color-neutral-300`, `--color-neutral-400`, `--color-neutral-500`, `--color-neutral-600` in group order. Chip 236 px wide, background `--color-surface`, radius `--radius-md`, padding 8 12 9 px, column gap 5 px: name 15 px / 600 left, big number 22 px / 500, −0.02em, in the group colour (never: `--color-neutral-400`; quota: `--color-neutral-400`; not asked: `--color-neutral-500`); a 5 px bar (track `--color-neutral-900`, fill `--color-accent-500`, radius 2 px) on rate chips only; sub 10 px / 1.35 `--color-neutral-500`. Footnote 13 px `--color-neutral-500`, max width 960 px.

### 11.9 Right now: "Can I get one right now, anywhere?"

The section answers 3 questions for each machine, in this order: can I get one right now (from the latest round in the data, never from the window in view), where do I go and what do I do, and how dependable that answer was over the section's window. A click opens every place, every round, and every Capacity Block Region of the machine. The collapsed rows have no legend: an icon, a word, and a qualifier carry each answer. The design replaces the strip, its legend, and the "block-only machine" category of the earlier design. Where the design conflicts with a data rule of this spec, this section keeps the data rule and marks the item "open (K{n})"; section 15 lists the conflicts.

**Inputs.** The answer and the suggestion read `meta.now` (section 7.3) only. The window columns and the open rows read the window of the section (below, "Window"). `CAD` is that of section 11.2. A place prints as "{cloud} {region}", the cloud as "AWS" or "Azure". `hrs` is that of section 11.4, with a value under 1 h printed as "{max(1, round(60 × h))} min" and a whole number of days printed with no ".0".

- **Latest round.** `nowH` = floor(max `h` of `meta.now.sweeps` / `CAD`) × `CAD`. A machine's latest round is the largest floor(`h` / `CAD`) of the `meta.now.sweeps` of its types; only the sweeps of that round count. Its **places** are the `meta.now.places` of its types, combined per (`cloud`, `region`) (the counts add up; `lastYesH` is the largest). The now state of a place is that of its sweeps in the latest round: the highest state ≥ 0, else −1, else null (not asked); its `got` and `asked` are those of the sweep that set it. The states −3 and −4 are no answer and no quota (section 6.1): the place shows "not available" or "not offered".
- **Best offer.** Of the `meta.now.cb` entries (the latest check of each Capacity Block place) of the machine's AWS types with a non-null `leadH` and with `h` fresh: `nowH` − `h` < 2 × `CAD`, that is, the check is less than 2 × `CAD` old, a sliding window and not a cycle boundary (the Capacity Block checks run in the hour before the provisioning round, so they never share its round), so a stale check never counts, and with non-null `durH` and `start` too, the one with the smallest `leadH`. The lead shown and tested is relative to now: `leadH` − (`nowH` − `h`), floored at 0, with the age `nowH` − `h` floored at 0 too, so a check newer than `nowH` never lengthens the lead and a check up to 2 × `CAD` old no longer prints "starts within 1 h" for a block that starts later than that from now, and the now test (< 1 h) uses the same relative lead; `start` is unchanged. A block whose start has passed is floored at 0 and reads "starts within 1 h", the word of the design; the board adds no "already started" word. Its start prints as "within 1 h" when `leadH` < 1 (the now rule of section 11.3 item 4), "in {hrs(leadH)}" when `leadH` < 24, else "{Sat Nov 1} (in {hrs(leadH)})" (weekday short, month short, day numeric, UTC, of `start`). Its length prints as "{hrs(durH)} long", plus ", up to {hrs(longestH)} later" when `longestH` > `durH`.
- **Yes-rate of a place** `rate(P)`: 100 × `yes` / (`n` − `na`) over the window (section 11.3 item 2); with `n` − `na` = 0 in the window, the same over the whole data (`meta.now.places`); else 0. The evidence line of the suggestion then says "over all the data" in place of "over {winLabel}" for that place.
- `ago(h)`: "this round" when `nowH` − `h` < `CAD`, else "{hrs(nowH − h)} earlier".

**Answer** (column "Right now"), per machine, the first that applies:

| # | Rule | Kind | Icon | Word | Qualifier |
|---|---|---|---|---|---|
| 1 | a place has a now state > 0 | yes | `check-circle` (fill) | Yes | "{k} of {n} places said yes" (k = places with a now state > 0, n = places with a now state) |
| 2 | the best offer starts within 1 h | yes | `check-circle` (fill) | Yes | "a block starts within 1 h" |
| 3 | the best offer has `leadH` < 24 | soon | `clock` | Not now | "block starts in {hrs(leadH)}" |
| 4 | a best offer exists | book | `calendar-blank` | Book ahead | "earliest {Sat Nov 1}" |
| 5 | at least 1 place has a now state, and every place with a now state has −1 | quota | `lock-simple` | Our quota | "all {n} places at our limit" |
| 6 | a place has a now state ≥ 0 | no | `x-circle` | No | "none of {n} places" (n = places with a now state ≥ 0) |
| 7 | else | none | `minus-circle` | by case, below | by case, below |

When no rule 1 to 6 applies, no place of the machine has a now state and the machine has no best offer. Rule 7 then has 3 cases. Let M be the `meta.now.places` entries of the machine (a place with a sweep of a state other than −2). Each machine matches exactly 1 case, in this order, and each case includes "and no earlier case":

- **7a. Not offered / Not available.** M is not empty and every place of M has a sweep in the latest round with state −3 or −4. Kind none, icon `minus-circle`, word "Not offered" when every such place is −4, else "Not available", qualifier "{n} places said so in the latest round". Its suggestion is "Not offered or not available in the latest round." and no evidence. It is never "Our quota" and never "No".
- **7b. No block now.** M is empty and the machine has a Capacity Block place (a `meta.now.cb` entry): it is a block-only machine. Kind none, icon `minus-circle`, word "No block now", qualifier "no Capacity Block offered in the latest round". Its suggestion is "We ask for it only as a Capacity Block." and no evidence.
- **7c. Not asked.** Every other machine: M is empty and the machine has no Capacity Block place (before its probe existed, or only −2 sweeps), or M has a place that is not −3 or −4 in the latest round (for example −3 or −4 mixed with a place with no sweep in the latest round). Kind none, icon `minus-circle`, word "Not asked", qualifier "no round yet" when M is empty, else "not asked in the latest round".

Cases of rule 7 and the rule each machine matches: all yes, rule 1; some yes, rule 1; all −1, rule 5; −1 mixed with no capacity (state 0), rule 6; all −3 or −4, 7a; −3 or −4 mixed with a place not asked in the latest round, 7c; block-only with an offer under 1 h, rule 2; block-only with an offer at 5 h, rule 3; block-only with no offer, 7b; only −2 sweeps, 7c; before the probe existed, 7c.

**Singular.** The board-wide rule of section 10.1 applies to every text of this section: the answer, the suggestion, the window column, the strip tooltip, and the open rows.

**Suggestion** (column "Where · what to do"): line 1 is the plan, line 2 the evidence. For kind yes by rule 1, the yes places are ordered by `rate`, high first, then by now state (2 before 1), then by the tie order below; the first is the try-first place.

**Tie order** (place rows, try-first place, and chips of this section): the answer rule first (the now state > 0 first, then `rate`, high first); for the try-first place and the chips, which hold yes places only, a full answer over a partial one (state 2 before 1); then cloud name; then the position of the place in the latest round of `meta.now.sweeps`, a place that is not in the latest round after every place that is, by region. The "last yes" place of the "No" text (largest `lastYesH`) breaks a tie by the steps of this order after the answer rule: cloud name, then the position in the latest round, then region.

- The `rate` of the evidence line counts only the rounds the place was asked (the rounds with `na` = 0), so the sentence says "of the rounds it was asked" and never "of rounds". The window column counts all rounds of the machine and can show a lower share.
- Yes, 1 place: "Only {place} — go there." / "It said yes in {ps(rate)} of the rounds it was asked over {winLabel}."
- Yes, several places: "Try {place} first." / "It said yes in {ps(rate)} of the rounds it was asked over {winLabel}; {k} more place{s} also said yes."
- Yes by rule 2: "Buy the Capacity Block in AWS {region} — starts {start}, {length}." / the on-demand line.
- Not now: "Wait for the Capacity Block in AWS {region} — starts {start}, {length}." / the on-demand line.
- Book ahead: "Book a Capacity Block in AWS {region} — starts {start}, {length}." / the on-demand line.
- The on-demand line: with places of now state ≥ 0, "On demand: none of {n} place{s} in the latest round; a yes in {ps(pct)} of rounds over {winLabel}." (the part after ";" only when `pct` is not null); else "We ask for it only as a Capacity Block." (correction C11).
- No: "Nothing anywhere. Last yes {ago(lastYesH)} in {place}." (the place with the largest `lastYesH`), or "Nothing anywhere. Never a yes in the data." / with `pct` ≥ 40 "A yes in {ps(pct)} of rounds over {winLabel} — ask everywhere again in {CAD} h."; with 0 < `pct` < 40 "A yes in only {ps(pct)} of rounds over {winLabel} — do not plan on it."; with `pct` = 0 "Never a yes in {tried} rounds over {winLabel}."; with `pct` null nothing.
- Our quota: "Raise our quota first." (or, when a latest-round −1 sweep has `reason` "restricted", "Ask for a quota increase or a size access request first.", correction C12) / "Every place we asked stopped at our own account limit, so the cloud was never asked about capacity."
- Not asked: "Not asked in the latest round." and no evidence.
- **Chips** (kind yes by rule 1 only): 1 pill per yes place in the order above, "{place} {ps(rate)}", at most 6, then "+{n} more".

**Over {winLabel}** (the window column), from the window sweeps by section 11.3 (places, union, counts): `pct` = 100 × `yes` / `tried`, null when `tried` = 0.

- A machine with no `meta.now.places` entry and a Capacity Block place (block-only): big `ps` of the block share, text "{nowFound} of {n} checks had a block starting within 1 h" summed over its places (section 11.3 item 4), bar of that share, and "earliest start usually {hrs(24 × median of the places' medLead)} out", or "no block within {horizon}" when no place has a `medLead`.
- `tried` = 0 and a round of the union is −1: "0%" / "quota blocked all {q} rounds" ({q} = the number of rounds of the union that are not null, over the window) / "no capacity answer possible".
- `tried` = 0 otherwise: "–" / "not asked over {winLabel}", or "asked; no capacity answer over {winLabel}" when a place has `nu` > 0 or `no` > 0 in the window (section 11.3 item 5); no wait line.
- Else: big `ps(pct)`, text "{yes} of {tried} rounds had a yes", bar of `pct`, and the wait line: `yes` = 0 "never, in {hrs(tried × CAD)} of asking"; a wait (section 11.3 item 7) "longest wait {hrs(waitH)} {between two yeses | since the last yes | before the first yes}"; else "never more than one round without".

**Machine column.** The name, and the shape "{g} GPU{s} · {clouds of its places, in order, joined by " + "}, {k} place{s}", or "{g} GPUs · AWS, Capacity Block only" for a block-only machine (g = GPUs per machine, section 11.1).

**Rows.** 1 row per machine of `gl` in catalogue order, under a tier kicker (tier name and subtitle, section 11.1) for each tier of `gl` that has a machine. The design's `groupByTier` and `sortBy` properties are not built (decision R8). Each row is a `<button>` with `aria-expanded`; a click, Enter, or Space toggles it and sets the focus machine through the state callback of section 10.2. Column heads: "Machine", "Right now", "Where · what to do", "Over {winLabel}".

**Open row.** Head: "Every place we asked", "Latest round", the axis, "Rounds with a yes · last yes".

- **Place rows**, every place of the machine, places with a now state > 0 first, then by `rate` (the window rate; with no round asked in the window, the rate over the whole data), high first, a tie by the tie order below: a cloud pill ("AWS" or "Azure"), the region with a "try first" tag on the try-first place, and the probe sub: `aws_odcr` and `az_capacity_reservation` "reservation for now" (correction C7, C11), `az_vmss_flex` "scale set · VMs really start". Latest round: the icon of the kind and "yes · all {got}" (state 2), "yes · {got} of {asked}" (state 1), "no capacity" (0), "our quota" (−1), "not available" (−3), "not offered" (−4), or "not asked". The strip (below). Right: `ps` of `yes` / (`n` − `na`) over the window in `tone`, with "{yes} of {n − na} rounds"; with `n` − `na` = 0, "quota" / "blocked all {na} rounds" when `na` > 0, else "–" / "not asked"; then "last yes {ago(lastYesH)}" or "never a yes in the data". **Zone rows**: the opened detail shows no zone rows yet. The zone breakdown stays in the place tooltip. Zone rows wait for a design pass and per-zone state in the payload.
- **Block rows**, 1 per Capacity Block place of the machine's AWS types: the places with an offer by `leadH`, low first, then the others. A "soonest" tag on the first, when the answer is kind soon, kind book, or kind yes by rule 2. Pill "AWS", the region, sub "Capacity Block · booked ahead". Latest check: the icon of yes, soon, or book by the rules of the answer, with "starts within 1 h" or "in {hrs(leadH)}"; with no offer, `x-circle` and "none within {horizon}". Offer text: "Earliest start {start} · {length} · checked {ago(h)}", or, with a fresh latest check that found nothing, "No Capacity Block offered within {horizon} at the latest check ({ago(h)})." (correction C11), or, with a latest check that found an offer but is not fresh (the rule of the best offer), "The latest check is too old to count ({ago(h)})." with the icon `minus-circle` and the word "too old to count" (never `x-circle` or "none within"), or, with a fresh check that has a lead but no `durH` or no `start`, "The latest check found an offer without a start or a length ({ago(h)})." with `minus-circle` and "start unknown" A check that found an offer is never described as "No Capacity Block offered". Right: `ps` of `nowFound` / `n` over the window, "{nowFound} of {n} checks had a block starting within 1 h", and "earliest start usually {hrs(24 × medLead)} out" when `medLead` is not null; with `n` = 0, "–" / "no checks over {winLabel}".
- **Strip** of a place: 1 cell per round on a `day` or `week` page (a 169-hour week included), else 1 cell per UTC day (correction C10). Height is the encoding, bottom-aligned. At round grain: state 2 full height, state 1 half, state 0 a 2 px tick, −1 full height with the hatch, not asked empty. At day grain, over the day's rounds that are not null and not −1 (`n`, of them `y` with a yes): `n` = 0 with a −1 round full hatch; `n` = 0 empty; `y` > 0 max(12, round(100 × `y` / `n`))% high; else the 2 px tick.
- **Tooltip** of a strip cell (section 11.6; the tooltip hides after 1 animation frame and takes the nearest cell from the pointer x): at round grain, title "{day} {HH}:00 UTC" with the real UTC hour (correction C9), line "got all we asked ({got} of {asked})", "got some ({got} of {asked})", "asked, cloud said no capacity", "our quota blocked the ask", or "not asked"; then the zone lines of the round's sweep (the sweep that set the cell: the first sweep of the place in that round with the highest state ≥ 0; section 5.3 rules 5 and 6): when it has `zones` > 1, the line "Yes in {zonesYes} of {zones} zones.", followed on the same line, after a space, by "{gotZoneSum} across {zonesYes} zones, at least: each zone stops at the count asked." when `zonesYes` > 1; and, when it has a `zoneList`, a last line "By zone: {zone} {got}, ….". At day grain, title "{Wed Oct 29}", line "{y} of {n} rounds had a yes", "our quota blocked every ask", or "not asked".
- **Axis and marker.** At round grain, a tick every 6 UTC hours ("HH:00") on a window of 24 h or less, else at each UTC midnight (the day format of section 11.4); at day grain, a tick at every seventh UTC midnight (the date format); a tick past 93% of the width is left out (correction C9). When `meta.zoneStartH` is inside the window, every strip of the open row has a dashed `--color-neutral-400` line at that hour, titled "AWS asks every zone from here".
- **Key line**, under the rows: "got all we asked", "got some", "cloud said no capacity", "our quota blocked it", "blank = not asked", each with its bar shape; then "Before {date of meta.zoneStartH} AWS was asked in one zone per Region." when `meta.zoneStartH` ≥ `H1`, or "Dashed line: AWS asks every zone from here." when it is inside the window (section 5.3 rule 7; correction C12).

**Window.** The section has no window control and no stored state of its own: it follows the board horizon and page of sections 11.5 and 11.7 (`hz` with `day`, `week`, `month`, `all`, and `page`). Pages, default page, and window are those of section 11.2 with `day` added. Only the window column and the open rows depend on the window; the answer and the suggestion always read the latest round. The shell fetches the window through the `DataSource` (section 7.2). `winLabel`: `all` "all {number of meta.dates} days", `day` "{Wed Oct 29}", `week` "the week of {Oct 27}" (the start, America/New_York), `month` "{October}" (UTC).

**Header and footer.** Title "Can I get one right now, anywhere?". Sub-line: "Latest round {asOf}. {Every} we ask every place for a real machine; a yes means the cloud handed it over. Click a machine for every place and round." with `{Every}` = "Every hour" when `CAD` is 1, else "Every {CAD} hours", and `asOf` = `T0` + `nowH` as "{Mon Oct 27 22:00} UTC" (weekday short, month short, day, 24-hour HH:MM, UTC). Footer: "A place is a cloud plus a Region. AWS Regions combine every zone we asked into one answer, the best zone. A Capacity Block is bought ahead; it counts as “now” only when it starts within 1 hour. Nothing here is a forecast or a listing."

Design spec: the section header is a wrapping flex row, gap 16 px: title 18 px / 500, −0.01em, line height 1.3; sub-line 12 px `--color-neutral-500` with `asOf` in `--color-neutral-300`; Every row (head, machine, open row) is a grid `190px 150px minmax(0,1fr) 230px 24px`, gap 18 px, padding 10 8 px. Column heads 11 px `--color-neutral-500`. Tier kicker 11 px / 600 uppercase +0.07em `--color-neutral-400`, subtitle `--color-neutral-600`. Machine row: hover background `--color-surface` at 70%, open background `--color-surface` at 50%. Name 15 px / 600, −0.01em; shape 11 px `--color-neutral-500`. Answer: icon 17 px and word 15 px / 600 in the colour of the kind (yes `--color-accent-300`, soon `--color-neutral-200`, book `--color-accent-2-300`, no `--color-neutral-300`, quota `--color-neutral-400`, none `--color-neutral-600`); qualifier 11 px `--color-neutral-500`. Icons are Phosphor (decision R12): `check-circle` in the fill weight, the others regular, `aria-hidden`; shape and word carry the meaning, colour only reinforces. Plan 13 px `--color-neutral-100`; evidence 11 px `--color-neutral-500`. Chips: pill 11 px / 500, padding 3 8 px, radius 999 px; the try-first chip border `--color-accent-600`, background `--color-accent-900`, text `--color-accent-200`, rate `--color-accent-400`; others border `--color-neutral-800`, text `--color-neutral-300`, rate `--color-neutral-500`. Window column: big 18 px / 500, heading font, −0.02em, in `tone(pct)` (block-only `--color-accent-2-300`); text beside it 11 px `--color-neutral-500`; bar 4 px, track `--color-neutral-900`, fill `--color-accent-500` (block-only `--color-accent-2-500`); wait line 11 px `--color-neutral-400`. Chevron "▸" / "▾" 12 px `--color-neutral-500`. Open row: head 11 px `--color-neutral-500`; cloud pill 9 px / 600, +0.06em, background `--color-neutral-900`, text `--color-neutral-300`, width 40 px; region 12 px / 500; tags 9 px / 600 uppercase +0.06em, radius 3 px, padding 1 4 px: "try first" `--color-accent-300` with a 1 px `--color-accent-700` border, "soonest" `--color-accent-2-300` with a 1 px `--color-accent-2-700` border; probe sub 10 px; latest-round icon 14 px. Strip cells 16 px tall, gap 1 px, bottom-aligned; got all and got some `--color-accent-400`; the tick `--color-neutral-600`; the hatch `repeating-linear-gradient(45deg, var(--color-neutral-600) 0 1.5px, transparent 1.5px 4px)`. Right column big in `tone`, block rows in `--color-accent-2-300` at ≥ 60%, else `--color-neutral-200`. Key line 11 px `--color-neutral-500`. Footer 12 px / 1.5 `--color-neutral-500`, max width 900 px.

### 11.10 "So what" card

1 line per machine of `gl`, the parts joined by spaces. The card is empty when the window has no sweep and no Capacity Block place. The parts, in this order:

1. Mode `none`: when a place of the machine has `no` > 0 or `nu` > 0 (section 11.3 item 2), only "No capacity answer in the window shown: {places joined by ", "}.", with the places ordered by the hour of their first sweep of state −3 or −4 in the window, earliest first, each place "{cloud} {region} not offered" when its `no` > 0, else "{cloud} {region} not available": the words of the matrix cells (section 11.21). Else only "No probes for this machine in the window shown." Mode `quota`: only "Ask for a quota increase first; every probe stopped at our own limit ({regions} regions)." with `{regions}` = the number of places of the machine whose sweep count (the place field `n`, section 11.3 item 2) is > 0; a place with only −3 or −4 sweeps has `n` = 0 and is not counted.
2. **On demand** (mode `now` and `tried` > 0), with `share` = `yes` / `tried` and the best place = the highest `yes` / (`n` − `na`): `share` ≥ 0.9 "Launch on demand; {cloud} {region} handed us {ps} of asks. Reserve nothing."; ≥ 0.4 "On demand works {ps(share)} of rounds if you ask everywhere; best single place {cloud} {region} only {ps}."; > 0 "On demand is a long shot: {ps(share)} of rounds even asking everywhere; longest drought {hrs(wait, or CAD when 0)}."; 0 "Nobody handed us one in {tried} rounds; do not plan on demand." The first 3 add the retry text.
3. **Retry text**, from the machine's stickiness row (section 11.11), each value used only with n ≥ 3. With P(yes after no) = b: b ≤ 10 " After a no, only {b} of next rounds said yes: a no means wait, not retry — check back next day, not next hour."; b ≥ 50 " After a no, {b} of next rounds said yes anyway: a no is noise, retry everywhere {hrs(CAD)} later."; else " After a no, {b} of next rounds said yes: retry everywhere every {hrs(CAD)}, but keep a fallback." Then, when a − b ≥ 20 points (a = P(yes after yes)): " A yes {hrs(CAD)} ago is worth more than any score: when you get one, take it — {a} of the time it is still there next round, median streak {streak}." The comparisons use the printed percent ("<1%" as 1). The a line needs both values (n ≥ 3 after a yes and after a no).
4. **Capacity Block**, with the best block place = the highest `nowFound` / `n`, then the highest `found` / `n`, then the lowest `medLead` (null as 99; so the card and the strip use the same now rule): with `nowFound` > 0 "For a sure thing, a Capacity Block in {region} started within 1 h on {ps(nowFound / n)} of checks."; with a found check but `nowFound` = 0 "No Capacity Block started within 1 h of a check. Book ahead: {ps(found / n)} of checks in {region} had a block, starting ~{hrs(medLead × 24), or –} out."; each plus, for the sawtooth machine (section 11.17), the regions with a found check: when some jumped " The next free slot got bought out from under us {in every region | in {j} of {k} regions} — book the moment you see a date you can live with; it will not wait.", else " The next free slot crept closer all window without anyone buying it: you can wait for a nearer date without losing it."; a block place with no found check "No check found a Capacity Block within {horizon} in any region." (the words "for sale" are gone: a block is booked ahead, and the reader must not read it as available now); no block place, mode `now`, `tried` > 0, and `share` < 0.9 "We check no Capacity Block for it, so there is no sure-thing path we could probe." (correction C6). A block place with no check in the window (`n` = 0) is not a block place; with `tried` = 0 (so no `share`) this part is empty.
5. **Substitute**, for the focus machine only, when it has a no round and the top substitute (section 11.15) has a share > 0: "When it says no, {substitute} was available in {ps} of those rounds — a different machine, if the job can take it."

Design spec: background `--color-surface`, radius `--radius-md`, `--shadow-sm`, padding 14 16 12 px. Kicker "So what — one line per machine, from the window in view", 11 px / 600 uppercase `--color-neutral-400`. Rows: grid `130px 1fr`, 12 px / 1.5 `--color-neutral-300`.

### 11.11 Stickiness: "Is capacity sticky? What the last round tells you about the next"

- Intro: "For consecutive rounds in view: how often the next round had a yes somewhere, split by whether the previous round did. A big gap means your own last probe is the best forecast you have."
- Rows: machines of `gl` with mode `now` and `tried` ≥ 6. Over the asked rounds of `src` (not null, not −1), for each round whose index is the previous asked round + 1: after a yes, count it and whether it is a yes (`a` with `n`); after a no, the same (`b` with `n`). Yes-streaks are runs of asked yes rounds; a not-asked round does not end a run. Median and p90 (element floor(0.9 × count) of the sorted runs) × `CAD`, printed with `hrs`.
- Columns: "yes again, after a **yes**", "yes, after a **no**", "yes-streak median · p90". Cells "{ps(a)} n={n}" and "{streak} · {p90}"; "–" when empty. Derive returns the streak caption (`streakText`); the view only draws it.
- Tooltip "{machine} · capacity is sticky?": "After a yes: {y} of {n} next rounds were also a yes", "After a no: {y} of {n} next rounds were a yes", "Yes-streaks: median {streak}, p90 {p90}".
- Verdict: no row "Not enough consecutive rounds in view to measure stickiness."; else, when some rows have a − b ≥ 20 points, "Your last probe is the best forecast you have for {names}: a yes {hrs(CAD)} ago is worth far more than any published score. ", then always "If you were just told no, the odds {hrs(CAD)} later are the right-hand number — plan the retry interval from that, not from hope."

Design spec: grid `130px 1fr 1fr 120px`, gap 6 12 px, heads 10 px `--color-neutral-500`. Heads: the words "yes" and "no" are bold (600) in `--color-neutral-300`. Bars 12 px, radius 3 px, track `--color-neutral-900`, fill `--color-accent-500` (after yes) and `--color-neutral-500` (after no). Labels 11 px `--color-neutral-300`, width 78 px, `n=` in `--color-neutral-600`.

### 11.12 Backtest: "Stay or follow? One region vs asking everywhere"

- Intro: "Replayed round by round. “Stay” parks in the single best region — picked with hindsight, the kindest possible case for staying put. “Follow” asks every region each round and takes any yes. Share of rounds with a machine in hand."
- Per machine of `gl` with mode `now`: rounds are floor(`h` / `CAD`) over the machine's sweeps with state ≥ 0; per round and place, 1 when a sweep got > 0. Skip the machine with fewer than 6 rounds. **Stay** = the largest yes count of 1 place (the first place on a tie); **follow** = rounds with any yes; **sole** = rounds with exactly 1 yes place; **rescued** = yes rounds where the stay place had no yes; **cover** = the greedy set cover of the yes rounds (each step the place with the most remaining yes rounds, the first on a tie); gain = follow − stay.
- Row: name with "{n} rounds · {places} places"; stay bar and `ps` with "{cloud} {region}" below; follow bar and `ps` with "any of {places} places"; gain "+{k} round{s}" or "none" (`--color-accent-300` when the gain is ≥ 10 points), with "{cover} to cover · {sole} sole-source". Heads: "**stay** · best single region", "**follow** · any region", "gain · regions to cover all".
- Tooltip "{machine} · stay vs follow · {n} rounds": "Stay: {place} alone (the best single place, picked with hindsight) said yes in {stay} of {n}", "Follow: asking all {places} places and taking any yes gave {follow} of {n} — {rescued} round{s} rescued by somewhere else", "{sole} round{s} had exactly one place with capacity; {cover} {place|places} {was|were} enough to cover every yes" ("1 place was enough", "N places were enough").
- Verdict: no row "Not enough rounds in view to backtest."; else either "Asking everywhere beat the best single region, even one chosen after the fact, for {name (gain), …}. Nobody picks that region in advance, so the real gap is wider. " (rows with a gain ≥ 10 points) or "In this window, asking everywhere did not beat parking in the right region for any machine — the most it added was {max gain} round{s}. The catch is knowing which region in advance: ". Then, over the rows with a gain < 10 points: follow ≥ 90% "{names} said yes nearly every round everywhere, so where you ask barely matters. "; 5% ≤ follow < 90% and cover 1 "For {names} a single region supplied every yes{ ({place}) when 1 machine} — only one place ever had it, so the other regions added nothing but also cost nothing to ask. "; cover > 1 "{names} needed {covers joined by –} regions to cover every yes, but the best one alone got most of them. "; follow < 5% "{names}: nobody had it, anywhere."

Design spec: grid `130px 1fr 1fr 150px`; the words "stay" and "follow" of the heads are bold (600) in `--color-neutral-300`; sub 10 px; stay bar `--color-neutral-500`, value 38 px wide, place 10 px `--color-neutral-600`; follow bar `--color-accent-500`; gain 12 px / 600, its sub 10 px.

### 11.13 Trend: "Better or worse?"

- **Days**: 24 h slices from `H0` to `H1` (the last one shorter). `hasPrev` = `H0` > 0 and `H0` − `L` ≥ 24.
- **Rate in a range** [a, b): mode `block`, per round floor(`h` / `CAD`) a yes when any 1-day check of the machine's types has `found` and a lead (`leadH`) under `NOW_LEAD_H` (1 h, the rule of section 11.3 item 4); else per round the max of (`got` > 0) over the machine's sweeps with state ≥ 0. Result: yes rounds and rounds.
- Rows: machines of `gl` with `tried` > 0. Sparkline: with 1 slice (a `day` page), the trend draws the value as a single dot at the slice, with no line and no area, and no dot for a day with no rate. The dot is not drawn in the SVG, because `preserveAspectRatio="none"` would stretch a circle; it is an HTML marker of 6 px × 6 px, `border-radius` 50%, the stroke colour of the row, centred at left (0.5 / 1) × 100% and top (100 − rate)% of the sparkline box (the spec author's reading: the design specs do not give the dot); for each day with a rate, the point x = (i + 0.5) / days × 1000, y = 100 − rate, in a 1000 × 100 box; a day with no round breaks the line, and the area closes to y = 100 at each break. Columns: previous `ps` of the rate in [`L`, `H0`) when `hasPrev`, else "–"; this `ps` of [`H0`, `H1`); change "+{n} pt" or "−{n} pt" (rounded absolute), "–" when either is missing; `--color-neutral-500` when |change| < 5, `--color-accent-300` when up, `--color-neutral-200` when down. Sub "yes somewhere", or "block within 1 h" for mode `block`.
- Heads: "previous {hz}" / "this {hz}" / "change"; for `all` "—" / "all data".
- **Method change** (section 5.3 rule 7). When `L` ≤ `meta.zoneStartH` < `H1`, the sparklines draw the dashed marker of section 11.9 at that hour when it is inside [`H0`, `H1`), and the note adds " · AWS asks every zone from {date of meta.zoneStartH}: AWS rates before and after it come from different methods". Axis: the date of every ceil(days / 6)-th day.
- Note: "{days} day{s} in view" when every day has a sweep (any SKU, any state), else "{days}-day {hz, or span for all}, {k} with data" (for `day`, "1 day, {k} with data"); then " · no earlier {hz} to compare with" when not `hasPrev` and `hz` is not `all`.
- Intro: "The line is each day’s share of rounds with a yes somewhere; shaded bands are weekends. The columns compare the whole {hz, or span} in view with the one before it."
- Tooltip "{machine} · {date of the day}": "{y} of {n} rounds · {ps}" or "Not asked that day."
- Detail: a round is floor(`h` / `CAD`), and a day counts the rounds of the sweeps or checks with an `h` in the day. A machine has a row when its counts (section 11.3 item 7) hold `tried` > 0. The days are `ceil((H1 − H0) / 24)`. The line path is "M{x} {y}" for the first point and after each break and "L{x} {y}" for the others, with 1 decimal. The area is "M{x} 100L{x} {y}…L{x} 100Z" for each unbroken run. The change sign is "−" when the unrounded change is below 0, else "+". The weekend bands are the runs of hours of [`H0`, `H1`) that fall on Saturday or Sunday in America/New_York, in percent of `H1` − `H0`. The method marker is at (`meta.zoneStartH` − `H0`) / (`H1` − `H0`) × 100, rounded to 4 decimals. The axis tick of day i is at i / days × 100. The word "span" names the range for `all` in the note and the intro. Keyboard: each sparkline is focusable, and the left and right arrow keys step through the days and show the tooltip.

Design spec: grid `130px 1fr 250px`, gap 6 16 px. Sparkline 34 px tall, SVG `viewBox="0 0 1000 100"`, `preserveAspectRatio="none"`, 1.5 px non-scaling stroke `--color-accent-400`, area `--color-accent-500` at 22%; mode `block` stroke `--color-accent-2-400`, area `--color-accent-2-600` at 25%. Weekend bands behind; bottom border 1 px `--color-neutral-900`. Values 13 px; change 600.

### 11.14 How many at once?

- Note: "After each full yes we ask again for double, up to a ceiling. Bars: share of asks (one per place per round) that handed us at least that many machines in one go." Then, with `meta.upStartH` set: when no window sweep has `nMax` > `asked`, " Doubling only began {date}, after this window: every round here asked the base size."; else when it is after `H0`, " Doubling began {date}, so earlier rounds in view only asked the base size."
- Rows: machines of `gl` with a sweep of state ≥ 0. Rungs 1, 2, 4, 8, 16, 32 up to the largest of `asked` and `nMax`; rung N = share of those sweeps with `nMax` ≥ N, drawn at least 3% tall when its count is > 0. Sub "{asks} asks · most at once: {largest nMax}".
- Tooltip "{machine} · at least {N} machine{s} at once": "{k} of {asks} asks handed us at least {N} · {ps}".

Design spec: the 3-up row is a grid of 3 equal columns, gap 28 px (this section, 11.15, 11.16). Rows grid `96px 1fr`; bars 42 px tall, `--color-accent-500`, an inset bottom rule `--color-neutral-800`, rung label 9 px above.

### 11.15 Substitutes: "When {gpu} said no, what said yes?"

- No rounds: the rounds where the focus machine's `src` is 0.
- Intro: the focus machine not in mode `now` "{gpu} has no on-demand asks in this window, so there is nothing to substitute for."; with no-rounds "In the {n} rounds when no place handed us a {gpu}, how often did some place hand us one of these instead? Share of those rounds."; else "{gpu} never had a round with no yes, so there was nothing to substitute."
- Rows: every other machine of the whole catalogue (not only the tier) with a sweep of state ≥ 0; share = yes / asked over the no rounds where its `src` is asked. The 6 highest shares (a missing share as 0), a tie kept in catalogue order. The rows show even when the intro says there is nothing to substitute: with no no-round every share is missing and every row reads "–". Value `ps` in `tone`, or "–". The derive result is `e.subs` (`rows` of `name`, `w`, `big`, `pp`, `tone`), which the "so what" card reads.
- Footer: "A substitute is a different machine. For restartable or small jobs this is the real menu at the moment you were told no."

Design spec: grid `110px 1fr 56px`, 10 px bar `--color-accent-500` on a track `--color-neutral-900`, radius 3 px; footer 11 px.

### 11.16 Book ahead: "Book ahead: how long a block, how soon"

- Rows: the focus machine first, then the catalogue (when the tier is not `All`, only machines of the tier); for each, its Capacity Block places with a `longestH`, longest first. Columns: machine, region, longest block (bar of days / scale; label "{round(days)} d" when ≥ 2 days, else "{longestH} h"), and "it starts in" "~{hrs(longestLead × 24)}" or "–". Scale = max(1, ceil(max(all longestH, 24) / 24)) days. The bar label is `--color-bg` when the bar is more than 25% wide, else `--color-neutral-200`.
- Note: "AWS Capacity Blocks, read per region for 1 machine. “Longest” is the longest duration AWS listed at any point in the window. Azure has no equivalent we can probe."
- Footer: "Bar scale: 0 to {scale} days. “It starts in” = median days from a check to the start of that longest block. " + ("Focus machine first." for `All`, else "Only this tier; switch Tier to All for the rest.") + bigger asks: for the in-window checks of 24 h for more than 1 machine, per machine of `gl` and count k, sorted by "{machine} ×{k}": "{machine} ×{k} machines: {ps} of {n} checks, starting ~{hrs(median lead × 24)} out" or "… never offered in {n} checks", joined by " · ", after " Bigger 1-day blocks were also asked for — " and ending in ".".
- Tooltip "{machine} · {region}": "Longest block listed for 1 machine: {days or hours} · typical listing {longestMedH as days (≥ 48 h) or hours} · starts ~{lead} out (median of {nLong} checks)", "{ps(found / n)} of {n} checks had a 1-day block starting within {horizon}{, soonest ~{hrs(medLead × 24)} out}", "Listed upfront fee for that 1-day block: ${round(fee)}" or "Fee not listed."

A Capacity Block is an offer AWS lists for a future start, not a purchase ([Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)); the probe only reads the offers ([spec 0008](0008-aws-probes-spec.md) section 3).

Design spec: table grid `84px 76px 1fr 70px`, heads 10 px "machine / region / longest block offered / it starts in"; bar 14 px `--color-accent-2-600` on a track `--color-neutral-900`, radius 3 px, with the label inside.

### 11.17 Sawtooth: "When could a block start? {machine}, check by check"

- Machine: the focus machine when it has a Capacity Block place in the window, else the first machine of `gl` that has one. No such machine: the section is not shown.
- Rows: every Capacity Block place of the machine's AWS types in the whole data (from the `cb` list), also one with no check in the window. Points: the in-window 1-day checks, x = (`h` − `H0`) / (`H1` − `H0`) × 1000, y = 100 − lead / scale × 92 for a found check with a lead, else a break. Scale = ceil(max(found leads in the window, 1) / 7) × 7 days; y labels "0 d", "{round(scale / 2)} d", "{scale} d". A jump is a lead more than 2 days above the previous found lead. Label "{region}" with "{found} of {n} checks found one"; note "{j} jump{s} out — the slot was bought", "the slot crept closer all window", or "no block within the horizon".
- Intro: "Days from each check to the earliest 1-day block AWS would sell for {machine}, per region. The "next free day" is a fixed calendar slot: the line creeps down as it approaches, then jumps when somebody buys it."
- Tooltip "{machine} · {region}" at the nearest check: "{day} {date}: next free 1-day block starts in {lead} days{ · ${round(fee)} upfront}" or "… no block within the horizon"; "No checks here." with none.
- Detail: the checks are the 1-day, 1-machine checks of section 11.3 item 5, in `h` order; `n` counts all of them, `found` those with `found` = 1. The scale reads the found leads of all rows. The path is "M{x} {y}" for the first point and after each break and "L{x} {y}" for the others, with 1 decimal; a jump compares with the previous found lead even across a break. A tooltip prints the lead as stored (for example "7.77"), the day as "{day label} {date label}" of the check's hour, and a check with no lead as "{day} {date}: no block within the horizon". With no jump and a found check the note is "the slot crept closer all window". Keyboard: the chart is focusable, and the left and right arrow keys step through the checks and show the tooltip.

Design spec: grid `110px 1fr 200px`; chart 56 px tall, left and bottom border 1 px `--color-neutral-900`, y labels 9 px `--color-neutral-600`, line `--color-accent-2-400`.

### 11.18 Time of day: "Does the time of day matter? {gpu}"

- Data: the focus machine's sweeps with state ≥ 0. The hour of a sweep is the real UTC hour of `T0` + `t` (decision R6); weekend per section 11.2 item 7.
- **Resolution**: the first of 1, 2, 4, 6 hours that is ≥ `CAD` where at least 80% of the (block, coast, weekend) groups that hold a sweep hold ≥ `minAsks` machines asked (sum of `asked`); else 6; 4 when there is no sweep. A sweep in a region with no coast (section 11.1) forms no group. Columns = 24 / resolution; heads "HH–HH" (or "HH" at 1 hour).
- Rows: 1 per coast with a sweep, "{coast} regions" / "weekdays" (weekday sweeps of that coast); then "All regions" / "weekends · {meta.weekends}" (all weekend sweeps; shown even with none), label `--color-accent-300`.
- Cell: got / asked summed; big `ps` or "–"; sub "{got} of {asked}" or "not asked"; thin when asked < `minAsks`: transparent with an inset 1 px `--color-neutral-800` ring.
- **Brackets** (correction C3): "ET 09–17" over 09:00–17:00 America/New_York and "PT 09–17" over 09:00–17:00 America/Los_Angeles, placed by the UTC offset of each zone at the window midpoint ([US daylight time](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst)): 13–21 and 16–24 UTC in daylight time, 14–22 and 17–01 in standard time.
- Intro: "Columns are hours of the day in UTC; rows split the regions by coast so a business-day drain shows up where it happens. Resolution is chosen from the data: {hourly | N-hour blocks}, the finest at which at least 80% of cells hold {minAsks}+ asks. Value = machines handed to us ÷ machines asked for." With no sweep: "{gpu} has no on-demand asks in this window."
- Legend: "0%…100% of machines asked for were handed to us"; "fewer than {minAsks} asked: too thin to read"; "columns are UTC · brackets mark 09–17 local business hours on each coast".
- Verdict, per coast row with ≥ 3 cells that are not thin (comparing printed percents; the first best and worst win): best 0 "{row}: never, at any hour"; best − worst ≥ 15 "{row}: best {head} UTC ({ps}), worst {head} UTC ({ps})"; else "{row}: flat within {n} points". Joined by " · ", then ". ET business hours are {a}–{b} UTC, PT {c}–{d} UTC." with the bracket hours of the window. Empty when no row qualifies.
- Tooltip "{gpu} · {row} · {head} UTC": "{got} of {asked} machines asked for were handed to us · {ps}" or "Not asked in this block.", plus "Fewer than {minAsks} asked: too thin to read." for a thin asked cell.

Design spec: cell 46 px, radius 5 px, padding 5 6 4 px, background `heat(p)`, text flips at 45; sub 9 px. Brackets 8 px tall, 1 px top and sides, radius 3 3 0 0; ET `--color-neutral-400`, PT `--color-accent-400`; labels 9 px. Legend: a 72 × 8 px swatch, radius 2 px, in front of the first line, with the gradient `--color-neutral-900` to `--color-accent-400` left to right. The corner head over the row labels has the visually hidden name "Row" (class `sr-only`, defined in the board CSS).

### 11.19 Forecast: "Does a high score predict a yes?"

- Kicker: "Forecast only — the clouds’ published Spot scores. Nothing in this box is a machine we got."
- Intro: "For every ask in view, the cloud’s Spot score for the same size and region in the {hrs(CAD)} before. AWS: placement score ≥ 6 is “likely”. Azure: a regional score of Medium or High."
- For each sweep of state ≥ 0 of a machine of `gl`: AWS, the mean of the score points with `h` in [`h` − `CAD`, `h`) of its key, likely when ≥ 6; Azure, High + Medium against Low in the same range, likely when more; skip with no point. Count the asks and the **full** yeses (state 2) of each bucket. Rows: machines with a counted ask. Values "{ps} of {n}", or "never ≥ 6" / "never < 6" with no ask in the bucket. Heads: "got the capacity when score said **likely**", "got it when score said **not likely**".
- The difference of a row is the share in the first bucket minus the share in the second; only a row with an ask in both buckets has one, and only such a row is named in the verdict. A group with no name leaves out its sentence. With no sweep in the window, the section is empty (the label only).
- Verdict: difference ≥ 15 points "A high score was a usable “yes” for {names}. "; |difference| < 15 "For {names} the score made little difference. "; ≤ −15 "For {names} it pointed the wrong way. "; then always "A low score was never a reliable “no”: it is a forecast for Spot, not a measurement of on-demand capacity. AWS: placement score ≥ 6 counts as likely; Azure: a regional score of Medium or High."

The score is a recommendation for Spot, not a capacity measurement ([How Spot placement score works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html); [Azure Spot Placement Score](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)).

Design spec: a box with a dashed 1 px `--color-neutral-800` border, radius `--radius-md`, padding 18 20 20 px. The kicker is first in the box, 11 px / 600, +0.07em, upper case (CSS `text-transform`), `--color-neutral-400`. Under it a grid `1fr 300px` holds the title, the intro, the heads, and the rows on the left and the verdict on the right: the title is inside the box. The words "likely" and "not likely" of the heads are bold (600) in `--color-neutral-300`. Rows grid `110px 1fr 1fr`; 12 px bars `--color-accent-500` and `--color-neutral-500` on a track `--color-neutral-900`, radius 3 px; labels 74 px.

### 11.20 Price: "Did price move with scarcity?"

- 1-day range (a `day` page, d1 − d0 = 1; the rule below for no price date wins when both apply): no row has 2 values, so there are no rows, the heads show 1 date, and the verdict is the no-row text.
- Days: index d0 = max(0, round((`T0` + `H0` h − first price date) / 1 day)); d1 = max(d0 + 1, min(dates, round((`T0` + `H1` h − first price date) / 1 day))). Heads: the dates of d0 and d1 − 1.
- Rows: for each machine of `gl` and each cloud with a type, the series of its first type of that cloud, sliced to [d0, d1), with ≥ 2 values. First and last value "${2 decimals}"; change = 100 × (last − first) / first, "+{1 decimal}%" or "−…" (`--color-neutral-500` when |change| < 3). Sparkline scaled to its own range: y = 8 + (100 − 100 × (v − low) / (high − low)) × 0.84, or the middle when flat. Cloud tag AWS `--color-neutral-900` / `--color-neutral-300`, Azure `--color-accent-900` / `--color-accent-200`.
- Intro: "Spot price for one fixed machine size per machine and cloud, daily mean in $ per machine-hour across the regions watched, over the horizon in view. If price tracked availability, the machines that said no would be climbing."
- The change is `--color-text` when |change| ≥ 3. The on-demand yes rate of a machine is `sh(yes, tried)` of its counts (section 11.3 item 7). With no price date in the window (no date index in [d0, d1) of the dates of the data: the page ends before the first price date or starts after the last one), the section is empty (the label only); this wins over the 1-day rule above.
- Verdict: no row "No Spot price series for this tier in the window."; largest |change| < 10 "No. Every Spot price here ended the {hz, or span} within {ceil(largest)}% of where it started"; else "Mostly no. The largest move was {machine} on {cloud} at {change}; every other price stayed within {ceil(next largest)}%". Then ", while on-demand yes rates for the same machines ran from {lowest} to {highest}." (over rows with a rate), or "."; then " Scarcity did not show up in Spot prices. Azure prices carry a monthly effective date — a list price, not a market. On-Demand and Capacity Block prices were not probed."
- Tooltip "{machine} · {cloud} Spot price · {type}": "Daily mean, $ per machine-hour across the regions watched: {date ${v} · …}", "Low ${low} · high ${high}", "Meanwhile {ps} of our on-demand rounds got a yes." or "No on-demand asks for this machine."

The Azure claim rests on the stored effective dates (section 5.4 item 5).

Design spec: grid `130px 48px 1fr 64px 64px 60px`; heads 10 px `--color-neutral-500`: "machine", "daily mean, scaled to its own range" over the sparkline, the 2 dates (right-aligned), and "change" (right-aligned); sparkline 22 px, stroke `--color-neutral-400`; change 600.

### 11.21 Matrix: "Where did the yes come from?"

- Columns: `meta.regions`, AWS first, then Azure, with group heads "AWS" and "Azure" over their counts. Row groups: the tiers of `gl`, each with a separator row (tier name and subtitle). Row name: a button that sets the focus machine, `title` = the machine's types joined by ", ", sub = its clouds joined by " + " in the order of its places (a Capacity Block place counts as AWS and comes after them), or "–". The button sets the focus machine through the state callback of section 10.2. The table has `th scope` heads (`colgroup`, `col`, `rowgroup`, `row`), and each cell is focusable and carries the tooltip text as its accessible name; the cells keep the stylesheet focus ring.
- Cell for (machine, cloud, region): a place with an answered sweep shows **GOT IT**, p = 100 × `got` / `asked` (0 with nothing asked), background `heat(p)`; else, for mode `block` and a Capacity Block place in the region, **BLOCK**, p = 100 × `nowFound` / `n` (the now rule, section 11.3 item 4), transparent with an inset ring `color-mix(in oklch, var(--color-accent-2-500) {round(25 + 75 × p / 100)}%, var(--color-neutral-800))`, text `--color-accent-2-200` (derive returns the mix percent as `ringPct`); else a place with `n` > 0 and `na` = `n` shows "quota" on the hatch; else a place with `no` > 0 shows "not offered", and else a place with `nu` > 0 shows "not available", each with no big number and the ring of a never-asked cell (section 6.1; neither is a quota wall); else never asked (inset 1 px `--color-neutral-900`). Big `ps(p)`.
- Mini bars: the day slices of section 11.13, grouped into ceil(days / 16)-day steps when there are more than 16; per step the same share (got / asked, or the checks that start within 1 h / checks); height max(8%, share), none for an empty step.
- Intro: "Rows are grouped by tier. Each cell: one machine in one region. Filled cells: of the machines we asked for there right now, the share handed to us. Outlined cells: a different question — the share of checks with a 1-machine, 1-day Capacity Block starting within 1 h. The small bars are the same share {day by day | in N-day steps} across the window."
- Legend: "**GOT IT** filled: share of machines asked for that were handed to us right now"; "**BLOCK** outlined: share of checks with a 1-day block starting within 1 h"; "our quota blocked the question"; "not offered: AWS does not sell this type in the region"; "not available: Azure said SkuNotAvailable"; "never asked".
- Tooltip "{machine} · {cloud} {region}" for a GOT IT or BLOCK cell, else "{machine} · {region}": "{got} of {asked} machines asked for were handed to us · {ps}" or "{nowFound} of {n} checks had a 1-day block starting within 1 h · {ps}", and "Over time: {date ps · …}"; a quota cell "Our account could not ask here: {reason, or quota}. Not a capacity answer."; a not-offered cell "AWS does not offer this type in this Region. Not a quota wall and not a capacity answer."; a not-available cell "Azure answered SkuNotAvailable in {nu} round{s}: a size restriction or no capacity, which the probe cannot tell apart. Not a quota wall."; never asked "Never asked." A GOT IT cell adds "Not offered in {no} round{s}." when `no` > 0 and "SkuNotAvailable in {nu} round{s}." when `nu` > 0, and an AWS GOT IT cell adds "Each round counts the most that 1 zone handed us, what a single request gets; the strip gives the sum across zones." (section 5.3 rule 5).

Design spec: grid `110px repeat({columns}, 1fr)`, gap 3 px. Cloud heads 11 px / 600 uppercase, bottom border `--color-neutral-800`; region heads 11 px `--color-neutral-400`; tier separators 11 px / 600 uppercase `--color-accent-300` with the subtitle, bottom border `--color-neutral-900`. Cell 52 px, radius 5 px, padding 5 6 4 px: tag 8 px / 600 +0.06em, big 12 px / 600, mini bars 12 px tall, gap 1.5 px.

### 11.22 Definitions, quota walls, not measured

- **What a “yes” is, exactly** (3 columns):
  - "**AWS, right now** — {method}. Granted means the machines were ours to start; we release it within a minute. Refused means `InsufficientInstanceCapacity`." `{method}` follows the method change of section 5.3 rule 7. With `meta.zoneStartH` null or ≥ `H1`: "a capacity reservation in zone {zone letters joined by / of meta.zones} of each region" (always the word "zone", as the design has it: "zone a of each region"). With `meta.zoneStartH` ≤ `H0`: "a capacity reservation in each zone of the region that offers the type, one zone after the other; a yes means at least 1 zone handed us the machines, and the count is the most that 1 zone handed us, what a single request gets; the strip tooltip also gives the sum across the zones, a lower bound". Else the second text, then ". Before {date of meta.zoneStartH}: in zone {zone letters joined by / of meta.zones} of each region only". (spec 0002 section 4.9; [On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html); [insufficient capacity](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html#troubleshooting-launch-capacity))
  - "**Azure, right now** — a capacity reservation, granted in full or not at all, or a scale set that really starts the VMs; both are deleted at once." (correction C7; [create a capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create), [spec 0010](0010-azure-provisioning-probes-spec.md) sections 7 and 8)
  - "**AWS, days ahead** — a Capacity Block check: is a 1-machine, 1-day block on offer, and does it start within 1 h (a yes for now)? Blocks that start later are in Book ahead, up to {horizon} out. An offer, not a purchase. Only in Book ahead and the outlined matrix cells."
- **For capacity planning: the quota walls.** Over `gl`, the places with `n` > 0 and `na` = `n` (section 11.3), grouped by cloud; a not-offered or not-available place is no quota wall (section 6.1): "{machine}: {cloud} {region}{ (restricted)}, …; …". Text: "Never reached the capacity question — our account’s quota or a size restriction on our subscription refused first. Each is a request to the cloud (a quota increase or a size access request), not a capacity fact. {list joined by " · "}." (correction C8, section 6.2), or "No quota walls for this tier: every place we asked gave a capacity answer."
- **Not measured — do not read it into this board**, 6 items: "How long you keep a machine once it starts"; "What a yes would have cost: On-Demand and Capacity Block prices were never probed, only Spot"; "Parties over {N} machines; blocks for more than 1 machine" (N = the largest `asked` or `nMax` of the sweeps with `H0` ≤ `h` < `H1`, any SKU, or 0; section 11.2 item 8); "Interconnected multi-node capacity (placement groups never varied)"; an AWS zones item by the method change of section 5.3 rule 7: with `meta.zoneStartH` null or ≥ `H1`, "Other availability zones: AWS reservations were asked in {meta.zones joined by ", "} only"; with `meta.zoneStartH` ≤ `H0`, "All that the zones of a region hold: each zone stops at the count we ask, so the sum across zones is only a lower bound"; else "Other availability zones before {date of meta.zoneStartH}: AWS reservations were asked in {meta.zones joined by ", "} only"; "Spot forecasts as availability — shown only in the dashed box".

Design spec: definitions on a `--color-surface` card, 3 columns, the error code in monospace 11 px. Below, a 2-column grid, gap 24 px: the walls card (`--color-surface`, radius `--radius-md`, `--shadow-sm`, padding 14 16 px) and the not-measured box (1 px `--color-neutral-800` border), each with an 11 px / 600 uppercase `--color-neutral-400` kicker; the not-measured items in 2 columns, 12 px / 1.5 `--color-neutral-300`.

## 12. Make targets

`tasks/Makefile.web` follows [ADR 0002](../decisions/0002-makefiles-follow-cloud-posse-conventions.md): `##` help lines, `/` namespaces, `?=` defaults, `.PHONY`, `$(MISE_EXEC)` for each tool, thin recipes that call scripts.

| Variable | Default | Meaning |
|---|---|---|
| `DB_MIRROR` | the main checkout's `data/live` (`tasks/Makefile.data`) | the mirror of section 4.3 |
| `WEB_SOURCE` | `live` when `DB_MIRROR` holds a row file or a daily file, else `sample` | which rows the board shows |
| `WEB_ROWS`, `WEB_DB` | `live`: `data/rows` and `data/ftg.duckdb`; `sample`: `data/sample/rows` and `data/sample.duckdb` | the daily files and the database; the sample never overwrites the real database |
| `WEB_PORT` | `4173` | the dev server port, bound to `127.0.0.1` |

| Target | Does |
|---|---|
| `web/deps` | `npm ci` in `web/`, only when `web/node_modules/` is missing or older than `web/package-lock.json` |
| `web/db` | `make data/db` with `DB_MIRROR` (or `web/sample` for `sample`), `ROWS_DIR=$(WEB_ROWS)`, and `DB_FILE=$(WEB_DB)` (section 5.5); no network |
| `web/run` | `web/deps` and `web/db`, then `web/scripts/run.sh`: the Vite dev server with the API plugin (section 8.3) on `WEB_PORT`, with `WEB_DB` in the environment. Prints the URL and the source (`live` or `sample`). With `WEB_QUIET` set (`make dev`, `mise run web`), `web/scripts/run.sh` prints no line of its own: Vite prints its normal banner with the Local URL, and its errors; `web/newest` then prints only the source and the newest row time of each cloud. Vite runs in the foreground of the terminal and keeps stdin, so its keys work. Ctrl-C (SIGINT to the foreground process group) stops it, and the script exits 0 on SIGINT, so `make web/run` prints no `Error 130`, `Interrupt`, or `***` line. |
| `dev` | `scripts/dev.sh`: runs `data/sync`, which reads the store names from the deploy outputs of this repo ([spec 0014](0014-config-and-deploy-spec.md) section 4), builds the database, and runs `web/run` with `WEB_QUIET`. It has no name cache (`data/.dev-names`): a `tofu output` reads only the state, so it starts in seconds. It keeps the line about an expired login, and it prints no store name. |
| `web/build` | `web/deps`, then `vite build` into `web/dist/` |
| `web/export` | `web/db` and `web/build`, then `web/scripts/export.ts` (section 9) |
| `web/standalone` | `web/export`, then `web/scripts/standalone.ts` (section 9.1); writes `OUT` (default `web/dist/standalone.html`) and prints its path |
| `web/preview` | `npm run preview` on `web/dist/` |
| `web/test` | `vitest run`: the derive tests, the view tests, and the server tests of section 8.4. No network, no credentials. |
| `web/lint` | `biome check web/`, `tsc --noEmit`, and the derive type check of section 10.3 |
| `web/sample` | `web/scripts/make-sample.sh` (section 13) |
| `web/clean` | removes `web/dist/` and `web/node_modules/`; it removes nothing under `data/` |
| `lint/web`, `test/web` | aliases of `web/lint` and `web/test`, for the `lint` and `test` aggregates |

The cut-over removes `web/sync`, `STORE`, `WEB_OUT`, `WEB_SYNC_INTERVAL`, `web/scripts/sync.sh`, `web/sql/cells.sql`, and the 9-panel page. A store is read only by `make data/sync`; the board reads only the database.

## 13. Offline run and sample

A fresh clone has no `data/`, because `.gitignore` ignores it. `make web/sample` writes a small synthetic store to `web/sample/`.

1. `web/sample/` is generated, not committed. It uses the mirror layout of section 4.3 (`aws/` and `azure/` folders, each `experiment=sample/provider=<p>/probe_type=<t>/date=<D>/*.jsonl`), every row `simulated: true`, for the 9 probe types of section 5.2. It covers 48 hours from 2026-10-09T00:00Z, both clouds, and the machine modes `now` (H100 ×1, A10G, T4, A10), `quota` (A100 ×8), `block` (H100 ×8, H200 ×8), and `none` (B200 ×8, a Region `restricted` row), with the SKUs of the catalogue of section 11.1. Every state of section 6.1 is there: a full yes, a halving yes, a halving no, `quota`, `restricted_or_no_capacity`, an `aws_odcr` Region `restricted` row (state −4) and an old per-zone `restricted` row, and an error. `aws_odcr` asks 1 zone with no `details.zone_id` before hour 24 and every zone of the Region with `details.zone_id` from hour 24 (`meta.zoneStartH` = 24), and goes up after some full yeses. It also has Capacity Block offerings with `start_window_days`, longest-block rows, and Spot scores and prices of both clouds. The 3-week span and the 2 weekends are not in the sample. Its total size is 2 MB or less.
2. `web/scripts/make-sample.sh` writes `web/sample/` with the pinned `duckdb` CLI and a fixed seed, so that 2 runs give the same files.
3. After `make web/sample`, `make web/run` on a fresh clone (`WEB_SOURCE` = `sample`) builds `data/sample.duckdb` with no network and shows every section with the banner of section 5.2 item 2.

## 14. Decisions

Design rulings R1 to R12 (for the board build):

| # | Decision | Reason | Cost if wrong |
|---|---|---|---|
| R1 | The server reads `data/ftg.duckdb`, which `make data/rows` and `make data/db` build from the mirror (section 5.5). It holds no cloud credential. `make web/run` builds the database first (section 12). | The mirror already handles the S3 and Azure logins. | Data is stale until the next `make data/db`; the server reads a new file on the next request. |
| R2 | `@duckdb/node-api` at an exact pin on the engine of the CLI pin; the database opens read-only; the window SQL lives in `web/sql/`. | The CLI pin stays for `data/db`; 1 engine version writes and reads the file. | If the binding fails on a platform, the handler can call the CLI. |
| R3 | Dark theme only; the theme control is removed. | The design is dark-only and final. | A light theme is a later issue. |
| R4 | Inter is self-hosted with `@fontsource/inter` at an exact pin; the stylesheet has no Google Fonts import. | Offline run; no external host at view time. | Low. |
| R5 | The board keeps `min-width: 960px` inside an `overflow-x: auto` wrapper. | Section 1 item 7 at 360 px. | Low. |
| R6 | Every hour of day is the real UTC hour of `t0` + `t`, not `t` mod 24 (section 11.2 item 7). | The design labels say UTC, but its hours count from `t0`, which need not be a UTC midnight; with `t0` at 04:00 UTC its columns were 4 h off. | That section differs from the design; the derive tests apply the same fix. |
| R7 | Simulated rows are dropped; a database of simulated rows only is shown with a banner (section 5.2 item 2). | The offline run must show a board. | Low. |
| R8 | `minAsks` is the constant 8; the design's tweak properties are not built. | 1 fixed board. | Low. |
| R9 | The view state goes in the URL hash and `localStorage['ftg.v2.view']` (section 11.5). | The design's rule. | Low. |
| R10 | An `aws_odcr` Region `restricted` row is state −4, "not offered in this Region", with its own caption; an old per-zone `aws_odcr` `restricted` row is "not asked"; Azure `restricted` stays −1 (section 6.2). | The type is not offered in the Region, so no request changes it; an Azure restriction is lifted by a request. | Low: neither AWS case is a quota wall. |
| R11 | The derive tests use a committed, seeded, synthetic fixture and expected values written in the tests (section 10.4); no real data is committed. | The repo is public. | Real-data parity is checked by hand. |
| R12 | The 6 icons of section 11.9 come from the Phosphor set ([Phosphor icons](https://github.com/phosphor-icons/homepage), MIT), at an exact pin, bundled with the page; implementer's choice: the React components of `@phosphor-icons/react` or the web font of `@phosphor-icons/web`. The design loads them from a public CDN, which section 1 item 6 forbids. | Offline run; no external host at view time (the same rule as R4). | Low. |

Corrections to the design. The derive tests apply each (section 10.4):

| # | Correction | Reason |
|---|---|---|
| C1 | The strip round tooltip and the time-of-day grid use the real UTC hour (R6). | The same 4 h error as R6. |
| C2 | Axis ticks fall on UTC midnights; weekend bands run from Saturday 00:00 to Monday 00:00 America/New_York; week pages run from Monday 00:00 to Monday 00:00 America/New_York. | The design stepped 24 h and 7 × 24 h from `t0`, right only when `t0` is an America/New_York midnight and no daylight-time change falls in the span. |
| C3 | The business-hour brackets and the time-of-day verdict use the UTC offsets of America/New_York and America/Los_Angeles at the window midpoint. | The design fixed daylight time; US daylight time ends on 2026-11-01 ([NIST](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst)). |
| C4 | Strip axis labels on a UTC Saturday or Sunday are `--color-accent-300` at round grain. | The design specifies the tint, but its test matched the start of "13 Sat", which never starts with "S". |
| C5 | Block-only chip: "we ask for this node only as a block", not "AWS offers this node only as a block". | The mode comes from what the prober asks, not from what AWS sells; no source says AWS sells these types only as blocks. |
| C6 | "We check no Capacity Block for it", not "AWS sells no Capacity Block for it". | The same reason as C5. |
| C7 | The Azure capacity reservation probe starts no VM: its drill-down sub is "reservation for now" / "asks were granted", and the definition says so. | Spec 0010 section 8: the probe creates and deletes a reservation; no VM uses it. |
| C8 | The quota walls say "a quota increase or a size access request". | An Azure size restriction is lifted by a SKU request ([SKU not available errors](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)), not by a quota increase. |
| C9 | Section 11.9 follows C1 and C2: its week pages run from Monday 00:00 to Monday 00:00 America/New_York, its axis ticks fall on UTC hours (every 6 h on a 1-day window, else UTC midnights), and its round label is the real UTC hour. | The design's reference page steps 7 × 24 h from 1 America/New_York midnight, puts ticks at multiples of the step from `t0`, and prints `h` mod 24 as the hour. |
| C10 | Section 11.9 uses ceil for `H1` (section 11.2 item 4) and 1 strip cell per round on every `day` and `week` page, a 169-hour week included. | The reference page rounds `H1` and switches to day cells above 168 h. |
| C11 | Section 11.9: the Azure capacity reservation sub is "reservation for now" (C7); a machine with no on-demand place reads "We ask for it only as a Capacity Block.", not "Not sold to us on demand — Capacity Blocks only." (C5, C6); a Region with no block reads "No Capacity Block offered within {horizon} …", not "… for sale …". | The same reasons as C5 to C7; the design is changed to plain words in place of "for sale". |
| C12 | Section 11.9: the quota plan names a size access request when a −1 sweep has `reason` "restricted" (C8), and the method note and marker read `meta.zoneStartH`, not a fixed date. | C8; section 5.3 rule 7. |

Decisions of this spec:

| # | Decision | Reason |
|---|---|---|
| D1 | A sweep's `got` is the search result, and a smaller success is state 1; `restricted_or_no_capacity` is its own state −3 with its own caption, not dropped and no quota wall (sections 5.3 and 6.1). | A yes means the cloud handed us capacity; the design's data build took the first attempt only and read a halving success as no, and it dropped `restricted_or_no_capacity`. |
| D2 | Cleanup rows are no sweep; the search key includes `zone` and `details.n_config`. | ADR 0009 amendment; 2 entries of 1 target are 2 searches. |
| D3 | `meta.rows` counts the rows of all 9 probe types the board reads, prices included. | 1 plain definition; the design counted no price rows. |
| D4 | The payload carries no free text from `details`; `reason` is `quota`, `restricted`, or null. | An exported folder must not leak a resource name. |
| D5 | The window payload adds `firstH`, `cbFirstH`, `upStartH`, and `zoneStartH` to the meta, and the range [`L`, `H1`) to the window (section 7). | The derive layer reads these outside any window; the test of section 8.4 item 3 proves nothing is missing. |
| D6 | The board combines the per-zone `aws_odcr` searches of 1 entry and 1 round into 1 sweep: the best state of the answered zones, `gotZoneMax` (the most that 1 zone placed, the headline `got`), `gotZoneSum` (the sum over the answered zones, a lower bound, in the tooltip), and the 2 coverage fields `zones` and `zonesYes` (section 5.3 rows to sweep). | Both clouds answer the same question (an Azure regional request also lets the platform pick the zone). A single request lands in 1 zone, so the headline is 1 zone; the zones are separate capacity pools, so their sum is a fair lower bound for a fleet that spreads across them. Splitting by zone or adding a zone list stays possible, because the rows keep the zone (section 5.5). |

## 15. Open and implementer's choice

- Design rulings for section 11.9, where the design conflicted with a data rule:
  - K1: a Capacity Block is "now" when it starts less than 1 h after the check.
  - K2: the now rule reads the 1-day check `leadH` (`meta.now.cb`), not the days of `aws_cb_longest` (section 11.3 item 4).
  - K3: block counts over the window count only checks that start within 1 h; later starts belong to Book ahead.
  - K4: ruled "follow the design". Deferred: the design does not define the zone rows, so section 11.9 shows none yet and the zone breakdown stays in the place tooltip.
  - K5: the tooltip rules of section 11.6 hold (hide after 1 animation frame, nearest cell from the pointer x).
  - K6: a place in state -3 or -4 reads "Not available" or "Not offered", never "Our quota"; the rule 7a display stands (section 6.1, decisions R10 and D1).
  - Q1: 1 board horizon control, with "Day" added to the header of section 11.7; section 11.9 has no window control of its own.
- Open: the size of the `all` payload; the fixture cases and the window SQL of the rows-to-sweep rule; a per-zone list in the drill-down, and a check of the Region and zone forecasts after a week of per-zone data.
- Implementer's choice: the module split inside the folders of section 3; payload caching in the server (section 8.1 item 2); whether SQL or TypeScript serializes the JSON (section 8.2); the layout of the error and empty-store lines; the expected-file names of the tests.

## 16. Done when

- [ ] `web/` has the folders of section 3; `web/README.md` explains the mirror, the sample, `make web/run`, `make web/export`, and that the board needs no login.
- [ ] `tasks/Makefile.web` has the targets and variables of section 12, and `make lint/make` passes; the old snapshot targets and files are gone.
- [ ] The server answers the 2 paths of section 7.1 from `WEB_DB`, read-only, with no credential and no network, and the tests of section 8.4 pass (fixture database built by the real scripts, payload files, window-loses-nothing, read-only, engine version).
- [ ] `make web/export` writes `web/dist/` whose API files equal the server bodies byte for byte, and `make web/preview` shows the board from it.
- [ ] The derive layer passes the Biome rule and the no-DOM type check of section 10.3; the view holds no number or caption logic.
- [ ] Every board section of section 11 has its derive file, view file, and test, and each test matches the expected values of section 10.4 with C1 to C12 applied.
- [ ] A fresh clone, after `make init` and `make web/sample` and with no network, runs `make web/run` and shows every section on the sample with the banner of section 5.2 item 2.
- [ ] The browser network log of the board shows no request to another host; at 360 px the page does not scroll horizontally.
- [ ] The author ran the targeted tests, then `make fmt` and `make lint`, and they pass.

## Sources

Providers:

- [AWS accelerated computing instance specifications (GPU count, model, and memory of each type)](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)
- [Azure NCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series), [NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series), [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series), [NCas T4 v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series) (1 GPU each for the sizes of section 11.1)
- [DescribeInstanceTypeOfferings: "Lists the instance types that are offered for the specified location."](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypeOfferings.html)
- [SKU not available errors: submit a SKU request to Azure Support](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/error-sku-not-available)
- [Azure capacity reservation: success or failure in its entirety](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-create)
- [AWS On-Demand Capacity Reservations](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-reservations.html) and [insufficient instance capacity](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html#troubleshooting-launch-capacity)
- [Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html) and [DescribeCapacityBlockOfferings](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [The AWS Spot placement score is 1 to 10](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-sps-works.html)
- [The Azure Spot Placement Score is Low, Medium, or High](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/spot-placement-score)
- [DescribeSpotPriceHistory](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)
- [ListObjectsV2](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html), [GetObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html), [Assign a role for blob data access](https://learn.microsoft.com/en-us/azure/storage/blobs/assign-azure-role-data-access)

Tools and time:

- [Vite guide](https://vite.dev/guide/), [Vite plugin API](https://vite.dev/guide/api-plugin.html), [Vite build and `base`](https://vite.dev/guide/build.html), [Vite CLI](https://vite.dev/guide/cli.html)
- [DuckDB Node.js Neo client (`@duckdb/node-api`)](https://duckdb.org/docs/stable/clients/node_neo/overview), [npm versions (`1.5.6-r.2` is the newest release on engine 1.5.6, 2026-10-29)](https://www.npmjs.com/package/@duckdb/node-api?activeTab=versions)
- [DuckDB configuration: `access_mode`](https://duckdb.org/docs/stable/configuration/overview)
- [Biome `noRestrictedImports`](https://biomejs.dev/linter/rules/no-restricted-imports/)
- [TypeScript `lib`](https://www.typescriptlang.org/tsconfig/#lib)
- [Fontsource Inter](https://fontsource.org/fonts/inter/install)
- [US daylight saving time rules](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst)
