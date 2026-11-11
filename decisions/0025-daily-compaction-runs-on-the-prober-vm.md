# 0025. Daily compaction runs on the prober VM, from a systemd timer

- Status: accepted (decision, 2026-10-27, option A)
- Date: 2026-10-27

## Context

The store holds 1 small object per probe call, and S3 and Azure Blob return 1 object per GET, so a local copy costs 1 request per object. The project decided that a cloud job packs each closed UTC day into 1 zstd-compressed JSONL file per probe type, in the store, and that the job must leave the existing data alone.

Measured on 2026-10-27 (local mirror `data/live`, 15 days per cloud, `*.jsonl` row objects, logs not counted; lateness from read-only store listings):

- The busiest day has 2,671 row objects (16.9 MB) on AWS and 1,179 (14.7 MB) on Azure; with the logs, 4,995 and 2,955 objects. The largest day and probe type is `aws_spot_price` of 2026-10-19 (11.2 MB raw) and `az_spot` of 2026-10-16 (7.4 MB).
- A row object's `LastModified` was at most 13 s (AWS) and 3 s (Azure) after the time in its key. The only later writes came from the one-time prefix move of [spec 0003](../spec/0003-prober-core-spec.md) section 9A.
- The peak of `compact_day` on the largest real day and probe type, with the cloud SDK loaded, measured as macOS RSS, was about 190 MiB on AWS and 179 MiB on Azure. The full job on the VM is not measured yet. The prober VMs are a `t4g.small` (2 GiB, [Amazon EC2 T4g](https://aws.amazon.com/ec2/instance-types/t4/)) and a `Standard_B2ats_v2` (1 GiB, [Basv2 series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/basv2-series)).
- Neither store has versioning or soft delete. The only lifecycle rule is `expire-logs` (90 days, logs only), which this decision does not change.
- A change of AWS `user_data` or Azure `custom_data` replaces the VM; `sync.sh` can install systemd units with no VM change ([ADR 0012](0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md)).

## Decision

1. **Where.** A systemd timer on the prober VM runs `ftg-prober compact`, with the prober's venv and the prober's identity. No new cloud resource. A separate identity or function per cloud was rejected as more infrastructure than the job needs.
2. **Grants.** The prober identity keeps its write grant on `experiment=<name>/*` and gets plain read and list: on AWS `s3:GetObject` on `experiment=<name>/*` and `s3:ListBucket` on the bucket; on Azure `Storage Blob Data Reader` on the `data` container. No delete permission, lifecycle rule, versioning, or retention change.
3. **What it writes.** Only `experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst` and the sidecar `<probe_type>_<D>.jsonl.zst.manifest.json`, which it writes last; the manifest marks that probe type's day complete. Writing to this separate path leaves the raw rows and the logs alone. No code deletes store data; a test scans the prober code for delete calls. The layout and the manifest are in [spec 0003](../spec/0003-prober-core-spec.md) section 4.3; the manifest fields come from `prober/ftg_prober/compact.py`.
4. **When.** `ftg-compact.timer` runs at 00:30, 06:30, 12:30, and 18:30 UTC with `Persistent=true` ([systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)). A day is closed 30 minutes after its UTC end, more than 100 times the largest normal lateness. A day whose raw objects changed after its daily file was built (names or sizes no longer match the manifest) is built again. A failed day gets no manifest and is tried again on the next run. The first run builds the existing days.
5. **Memory.** `MemoryMax=384M`, about 2 times the measured `compact_day` peak ([systemd.resource-control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)). `TimeoutStartSec=3h` is an estimate for the first run, not a measurement.
6. **Consumers.** The local sync copies the daily files of each new `date=` folder, and raw objects only for a day and probe type with no complete daily file ([spec 0006](../spec/0006-dashboard-spec.md) sections 4.4 and 5.5). DuckDB `read_json` reads `.jsonl.zst` ([DuckDB, loading JSON](https://duckdb.org/docs/current/data/json/loading_json.html)).

## Consequences

- the request was that the job's identity get "write on the new prefix only". The prober identity can write anywhere under `experiment=<name>/`, so only the job's code keeps its writes under `daily/v1/`. The project accepted this on 2026-10-27.
- The prober identity can now list every key of the bucket and read every object of its experiment.
- A daily file is derived data; the raw objects stay the source of truth, and any day can be rebuilt from them.
- A day is compacted at most about 6.5 hours after the day ends. Until then the sync copies its raw objects.
- The `daily` path segment is not `provider=`, so the dashboard's row glob ([spec 0006](../spec/0006-dashboard-spec.md) section 4.1) never reads a daily file, and `expire-logs` never matches one.
