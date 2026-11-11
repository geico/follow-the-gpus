# 0010. One experiment file of modules and jobs, a billable guard, and sim versus real

- Status: accepted, partly superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md)
- Date: 2026-10-12
- Note 2026-11-03: [ADR 0029](0029-plain-tfvars-files-replace-the-config-wrapper.md) replaces the deployment wiring of this ADR. A deployment names its own experiment file with the `targets_file` variable in its `terraform.tfvars`, and both stacks deploy from this repo. The decisions of this ADR stay.

## Context

`run_mode` (`local`, `cloud-cpu`, `cloud-gpu`) answers 3 questions at once: are the calls real, what may an allocation probe create, and do the rows count. No code branches on `cloud-cpu` versus `cloud-gpu`; what an allocation probe creates comes from the `deep_probes` list, not from the mode value. Today's signals-only deploy writes rows under `run_mode=cloud-gpu/`, which is false: no allocation probe runs in that deploy. Cadence, targets, and settings are scattered across per-cloud sections of `config/targets.yaml`.

The old term hid what the probe does. [ADR 0008](0008-name-things-by-what-they-do-and-cost.md) renamed the concept to "allocation probe" and listed the `deep_probes` config key, the `az_deep` cadence key, and the file name `0010-azure-deep-probes-spec.md` as a later phase. The project has since banned the old term outright: new text uses "allocation probe" only.

It records decisions only. No code changes in this pull request.

## Decision

### 1. Modules and jobs, like the Prometheus blackbox exporter

The config models a probe the way the [Prometheus blackbox exporter](https://github.com/prometheus/blackbox_exporter) models a check, with the same split of concerns as its [configuration reference](https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md):

- A **module** says **how** to probe: a probe type plus its settings. The probe types and their defaults are built into the code, 1 place per probe type (section 3). A file may also define its own named modules under a `modules:` key, each a probe type plus the settings it overrides, the way the blackbox exporter's own config file holds named modules (for example an `http` module with a timeout and headers); a scrape job in `prometheus.yml` then names the module it wants.
- A **job** says **what**, **where**, and **how often**: a module, 1 or more regions or targets, and an interval. A job names either a built-in probe type directly, to use its code defaults, or a named module from `modules:`, to use that module's overrides.
- A listed job is enabled. Nothing else enables it. A job absent from the file does not run, and a probe type with no job never runs, no matter what else the file contains.
- A module can never change the cost class of its probe type. The cost class is fixed in code per probe type (section 4), and no override, named module, or job field moves a probe type between free and billable.

### 2. One file per experiment, shared by both clouds

1 file holds every job of an experiment, for both clouds. Each prober process reads the whole file and runs only the jobs of its own provider; it ignores the jobs of the other provider.

`config/targets.yaml` in this repo stops being "the" config file and becomes a worked example experiment file (section 9). Each deployment owns its own experiment file; a deployment keeps that file instead of a hand copy of `config/targets.yaml`.

The `experiment` name must match `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 63 characters (no double dash, no leading or trailing dash). This pattern is safe as a path segment, a cloud tag value, and a Terraform value, all at once, so 1 name works in the storage path (section 5), the resource tags, and the stack variables with no extra encoding. The live name `ftg-20261012-signals` matches it.

The file's `experiment` key is the only source of this name. Each Terraform stack reads it from the file to set `experiment_tag` on its own resources and the sweeper's `FTG_EXPERIMENT` app setting; the prober reads the same value from the same file. No variable, tag input, or environment variable carries a second copy of the name.

### 3. Defaults in code, one override layer

Each probe type's defaults (cadence, timeouts, cost class) live in code, 1 place per probe type. A job's module overrides only the settings it sets; every setting it omits keeps the code default. There is 1 override layer, not several: the project rejected a Helm-style stack of layered files, because 1 layer is enough and is simpler to read and to diff.

The override merges 1 level deep. A mapping key in the module overrides the matching mapping key of the code default; a key the module does not set keeps its code default. A list or a nested mapping is replaced as a whole, not merged field by field. For example, the `aws_cb_offering` module's code default settings are `{instance_types: [p5.4xlarge, p4d.24xlarge], duration_hours: [24]}`. A file override of `{duration_hours: [24, 48]}` on that module gives `instance_types: [p5.4xlarge, p4d.24xlarge]` (kept, the override did not set this key) and `duration_hours: [24, 48]` (replaced as a whole list, not appended to the default `[24]`).

Every key of today's flat schema moves to a new place:

| Old key | New place |
|---|---|
| `aws.regions`, `azure.regions` | a job's own `regions` list |
| `aws.cadence`, `azure.cadence` maps | each job's own `interval` |
| `azure.probe_timeout_seconds`, `azure.poll_interval_seconds`, `azure.teardown_timeout_seconds` | settings of the allocation module |
| `azure.spot_sizes` | the size list of the `az_spot`/`az_spot_score` module |
| `azure.spot_price_max_age_days` | a setting of the `az_spot` module |
| `aws.spot_score_panel` | the panel of the `aws_spot_score` module |
| `aws.capacity_blocks` | the settings of the `aws_cb_offering` module |
| `azure.quota`, `aws.quota` | unchanged: a section of each cloud, outside the job list (section 8) |

### 4. Billable guard

Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): 2 items below move. The file-level `allow_billable: true` key becomes a per-job `allow_allocation: true` key, next to the job's own `simulate` key (ADR 0011 section 3). The "does not apply under `--mode sim`" exemption, marked again below, becomes "does not apply to a job whose own `simulate` key, or the run-wide `--simulate` flag, is set, or the command is `sim`."

The config check refuses any job whose module uses a billable probe type, unless the file sets `allow_billable: true`. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): this top-level key is replaced by a per-job `allow_allocation` key (above). The cost class (free signal or billable allocation) is fixed in code per probe type; a job cannot change its own cost class. `allow_billable` comes only from the file; there is no environment variable or CLI flag for it (section 11), and the check looks at every job in the file, not only a job that `--probe NAME` picks. The guard has already merged on today's `aws`/`azure`/`deep_probes` schema, with an `allow_billable` key and a fault that names each billable entry. This ADR carries the key and its default (`false`) forward into the jobs model, but the rule it guards is not quite the same: today a `local` entry is never billable, no matter its probe type, because `run_mode` itself decides whether an entry calls a cloud. In the jobs model the cost class is fixed per probe type, not per entry, so a job for a billable probe type needs `allow_billable: true` even if someone meant it as a quick, no-cost check. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): a job with its own `simulate: true` key needs no `allow_allocation: true`, even for a billable probe type, because it makes no cloud call (ADR 0011 section 3, rule 3).

The guard runs in `ftg-prober run` and in `ftg-prober check-config`. It already runs in `ftg-prober sim` too, because today `sim` loads and validates the config the same way `run` does (`cmd_sim` calls `load_config`, which calls `validate`). This ADR changes that: the guard does not apply under `--mode sim` (section 11), because a sim run calls no cloud and creates nothing to guard against. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): there is no `--mode sim`. The guard instead skips a job whose own `simulate` key, or the run-wide `--simulate` flag on `run`, is set, or the command is `sim` (above). The Terraform stack of each cloud implements the billable-job rule in HCL (section 7), so the prober's Python check and each stack's HCL check must agree. 1 shared test fixture file (an experiment file with and without a billable job, with and without `allow_billable: true`) must give the same pass/fail answer in the Python tests and in `terraform test`, so the implementations cannot drift apart. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): the fixture's `allow_billable: true` becomes a per-job `allow_allocation: true` key. Superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md): there is no Terraform-side HCL check any more, and no shared Terraform/Python fixture for it. The prober's own Python check is unchanged and is now the only check of this key; each Terraform stack instead reads its own `grant_billable_provisioning` variable, independent of the file.

### 5. Run mode becomes sim versus real

Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): items 1 and 2 below, and item 4's last sentence (marked again there), are replaced by `--store`, a run-wide `--simulate` flag, and a per-job `simulate` key, next to the per-job `allow_allocation` key of section 4, above. Items 3, 5, and 6 stand.

1. `run_mode` (`local` / `cloud-cpu` / `cloud-gpu`) is replaced by a `--mode sim|real` CLI flag. The flag is not stored per record: a run is sim or it is real, for the whole process.
2. Sim output never goes to the cloud bucket. A sim run writes to a local path only.
3. A CPU-sized job is not a mode, and it needs no flag. It is an ordinary job whose target is a CPU size, for example to prove an allocation path before a GPU quota lands. Its row records the size it asked for, the same as any other job. The dashboard tells a CPU stand-in from a GPU row by the size, not by a flag: a size counts as a GPU size when its family is a GPU family (AWS: P, G, Trn, Inf; Azure: the N-series), a closed list; the heat map shows only rows whose size counts as a GPU size this way. Spec 0006 states this 1 rule.
4. The storage path drops the `run_mode=` partition and becomes:

   ```text
   experiment=<name>/provider=<p>/probe_type=<t>/date=<YYYY-MM-DD>/<file>
   ```

   `date` stays the last partition. The data is small, so date partitions keep lifecycle rules and deletes simple; a provider or probe-type change does not need to move files across a date boundary. A `--mode sim` run writes only to a local folder; the dashboard never reads that folder for a real panel, only for a local, offline run (spec 0006). Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): there is no `--mode sim`. The run-wide `--simulate` flag keeps this local-only rule, but a job's own `simulate: true` does not: its rows may land in a real, cloud store next to real rows (ADR 0011 section 2).
5. This supersedes the run-mode table of [ADR 0008](0008-name-things-by-what-they-do-and-cost.md) (marked there) and spec 0002 decision D19.
6. The live data under `run_mode=cloud-gpu/` is less than 1 day old. The migration: stop both probers; copy the data into `experiment=<live name>/` under the new layout; compare the object count and the total bytes per prefix, in S3 and in Blob, between the old prefix and the new one; delete the old `run_mode=cloud-gpu/` prefix only on a match; restart both probers on the new layout. A rerun of the copy is safe: each target key keeps the no-overwrite rule of the storage backends (spec 0003, section 9), so a second run skips a key that already exists and copies only what is missing.

### 6. Catalogue of allocation probe types

Every allocation probe type below follows the [ADR 0009](0009-allocation-probes-are-all-or-nothing-with-halving.md) contract: all or nothing, a failure halves the count, a verified teardown, and the same row format.

| Family | Cloud | Mechanism | Status |
|---|---|---|---|
| On-demand reservation | AWS | On-Demand Capacity Reservation (ODCR) | Planned (spec 0002 §4.9) |
| On-demand reservation | Azure | [Capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview) | Built |
| On-demand launch | Azure | [VMSS scale-out](https://learn.microsoft.com/en-us/azure/virtual-machines/flexible-virtual-machine-scale-sets), for sizes without reservation support | Built |
| On-demand launch | AWS | [EC2 Fleet `instant`](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html), On-Demand | Future (calibration pair) |
| Spot launch | AWS | [EC2 Fleet `instant`](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instant-fleet.html), Spot | Future |
| Spot launch | Azure | [Spot VMSS](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/use-spot) | Future |
| Future-dated reservation | AWS | Capacity Reservation with a future `StartDate` | Not probeable cheaply (below) |
| Capacity Block | AWS | Capacity Block purchase | Signal only; never allocate |

- **Future-dated reservation (AWS) is not probeable cheaply.** `CreateCapacityReservation` with a future `StartDate` accepts only the instance families C, G, I, M, R, T, U, and X, a minimum of 32 vCPUs, and a start date from 5 to 120 days ahead ([create a Capacity Reservation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-create.html)). A future-dated reservation also carries a minimum commitment duration once it starts: 14 days for the C, G, I, M, R, and T families, and 12 weeks for the U and X families ([create a Capacity Reservation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-create.html)). Once a future-dated reservation reaches the `scheduled` state, cancelling it needs `CancelCapacityReservation` and that cancellation is charged ([CancelCapacityReservation](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CancelCapacityReservation.html)). A probe that creates one to test capacity therefore pays to cancel its own test, and a successful one commits to days or weeks of billing, so this family has no cheap probe.
- **Capacity Block is signal only.** The experiment already reads Capacity Block offerings as a free signal (spec 0002 §4.1). It never buys one to probe capacity, because "Capacity Block cancellations aren't allowed" ([EC2 Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)): a purchase cannot be undone, so it is not a probe, only a cost.
- **EC2 Fleet `instant`, On-Demand is "future (calibration pair)," not a standalone probe.** [ADR 0009](0009-allocation-probes-are-all-or-nothing-with-halving.md) section 6 keeps an instant fleet for 2 future uses: a Spot allocation probe, and a daily On-Demand calibration pair, an ODCR and an instant fleet at the same count, to measure how often the 2 answers disagree. No standalone On-Demand launch probe (with no ODCR alongside it) is planned.

### 7. Terraform stacks follow the experiment file

**Superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md): this whole section describes a Terraform-side guard that re-parsed the experiment file's consent key, which turned out to have no principled stopping point against a YAML parser it could not match exactly. Each Terraform stack now reads its own `grant_billable_provisioning` variable instead, independent of the file; the paragraphs below are history, not current behavior.**

The Terraform stack of a cloud reads the same experiment file as its prober. When the file has no billable job, the stack plans no allocation-probe resources: no allocation scale sets, and no capacity-allocator or cleanup role assignment, when nothing in the file needs them. When the file has 1 or more billable jobs but no `allow_billable: true`, the plan fails outright, the same way the prober's config check fails: a `precondition` on the allocation resources stops `terraform plan` with an error, instead of quietly planning nothing. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): `allow_billable: true` is replaced by a per-job `allow_allocation: true` key, but it plays no part in whether the job plans a resource: that depends only on the module and the job's own `simulate` key, so that a missing `allow_allocation: true` never leaves a billable job unplanned with no error. A `terraform_data.allocation_guard` resource with a `precondition`, which every allocation resource depends on, fails the plan when an active job has no `allow_allocation: true` of its own, even under `-target` (ADR 0011 section 3, rule 4, and section 7, item 7). 1 file then drives both the prober and the stack; a deployment keeps its own experiment file, not a hand copy of `config/targets.yaml`.

This replaces the held Terraform switch that gave Terraform its own on/off toggle for allocation resources: a separate toggle would duplicate a fact that the job list and the billable guard already state. Superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md): a separate toggle is exactly what `grant_billable_provisioning` is. The file-reading guard this paragraph argues against turned out to be the one that could not keep up with YAML's own grammar; a plain variable has no such surface.

### 8. The `quota:` block stays outside jobs

`azure.quota` and `aws.quota` are each a section of its own cloud, outside the job list. They belong to the quota tool (`make quota/azure`, `make quota/aws`), not to the prober, and a job does not enable or disable them.

### 9. Worked example

An experiment file that matches today's live, signals-only deploy has signal jobs only, no allocation jobs, and no `allow_billable` key (the default `false` is correct, because nothing billable is listed). Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): `allow_billable` is a per-job `allow_allocation` key; this sketch has none, because it lists no allocation job.

```yaml
experiment: ftg  # example name

aws:
  jobs:
    - {module: aws_spot_score, regions: [us-east-1, us-east-2, us-west-2], interval: 5m}
    - {module: aws_spot_price, regions: [us-east-1, us-east-2, us-west-2], interval: 5m}
    - {module: aws_spot_advisor, regions: [us-east-1, us-east-2, us-west-2], interval: 24h}
    - {module: aws_cb_offering, regions: [us-east-1, us-east-2, us-west-2], interval: 2h}

azure:
  jobs:
    - {module: az_spot_score, regions: [eastus, eastus2, southcentralus, westus2, westus3, centralus, northcentralus, westus, westcentralus], interval: 15m}
    - {module: az_spot, regions: [eastus, eastus2, southcentralus, westus2, westus3, centralus, northcentralus, westus, westcentralus], interval: 5m}
  quota:
    regions: [eastus, eastus2, centralus, southcentralus, westus, westus2, westus3]
    sizes:  # quota sizes shortened; the live file lists every GPU size, as config/targets.yaml does today
      - {size: Standard_NC40ads_H100_v5, count: 8}
```

No `az_capacity_reservation` or `az_vmss_flex` job is listed, so no allocation probe runs and the Terraform stack plans no allocation-probe resources (section 7). The module settings (spot size list, panel entries, Capacity Block types) are left out of this sketch; they keep the shape already in `config/targets.yaml` and move under each module.

### 10. Migration plan

1. Rename, in a later pull request: the `deep_probes` config key, the spec file and the adapter module that carried the old name, and the `az_deep` cadence key. All 4 were the "Phase 2" renames that [ADR 0008](0008-name-things-by-what-they-do-and-cost.md) deferred; they move to allocation-probe names in the same change that builds the jobs model, so the rename and the new schema land together instead of twice.
2. This ADR supersedes the run-mode table of ADR 0008 (marked there) and spec 0002 decision D19 (section 5).
3. Other work in flight:
   - The held Terraform switch (an `enable_allocation_probes` toggle) is superseded by section 7 of this ADR and does not merge as written: the guard and the job list already decide whether the stack plans allocation resources, so a separate toggle is a second source of truth for the same fact.
   - The `aws.quota` change has already merged; its `quota:` block carries forward unchanged (section 8).
   - The current guard (the billable guard on today's schema) has already merged, against `deep_probes` and `run_mode`. Its key and default carry forward into the jobs model, with the difference stated in section 4. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): the key moves from the top of the file to a per-job `allow_allocation` key (section 4, above).
4. The code for modules, jobs, the billable guard on jobs, the sim/real flag, and the new storage path is a separate, later pull request. This ADR and its spec changes record the target shape; they do not change `prober/ftg_prober/`. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): "the sim/real flag" becomes `--store`, a run-wide `--simulate` flag, and per-job `simulate`/`allow_allocation` keys.

### 11. Overrides: env vars, flags, and the guard

1. `FTG_RUN_MODE_OVERRIDE` and the prober's `FTG_EXPERIMENT` override are removed. The experiment file is the only source of the settings they used to override; nothing outside the file can change what a run does. The sweeper's own `FTG_EXPERIMENT` app setting stays (spec 0007): the stack still fills it, now from the file's `experiment` key instead of a separate input.
2. `allow_billable` comes only from the file. There is no environment variable and no CLI flag for it (section 4): a person who wants to allow a billable job must edit the file. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): `allow_billable` becomes a per-job `allow_allocation` key; it still comes only from the file, with no environment variable or CLI flag.
3. `--probe NAME` is kept. It limits a run to 1 already-listed job by name; it cannot start a job that is not listed in the file, and the billable guard still runs before that job, the same as for any other run.
4. Only `run` takes `--mode`. "sim" means `run --mode sim`; there is no separate `sim` command. `--mode sim` or `--mode real` picks sim or real for the whole run. It cannot enable a job that is not listed, and it cannot make a billable job skip the guard on a real run; the guard simply does not apply under `--mode sim` (section 4), because a sim run calls no cloud. `--mode` defaults to `real`, so a bare `run` with no flag behaves as it does today. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): there is no `--mode` flag. `run` takes a `--simulate` flag instead; the `sim` command keeps its own, separate shape; and a job's own `simulate` key can simulate 1 job with no flag at all.
5. `run --mode sim` refuses a `--store` URL that is not a local folder: a sim run can only write to disk, never to the cloud bucket. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): `run --simulate` keeps this rule. A job's own `simulate: true` does not: it may write into a cloud `--store`, next to real rows (ADR 0011 section 2).
6. An unknown probe type named by a `modules:` entry is a config fault, refused at load, the same as any other bad value in the file.

### 12. Open for the implementation PR

This ADR leaves these points for the pull request that writes the code:

- The exact rule for a few nested-merge edge cases (section 3): whether `null` or `{}` on a key clears the code default for that key or is itself a value; what happens when a named module in `modules:` uses the same name as a built-in probe type; how a grouped timeout mapping (today's `azure.probe_timeout_seconds`, `poll_interval_seconds`, `teardown_timeout_seconds`) is split across settings; and, when more than 1 allocation module exists, which module's settings carry which timeout.
- The plan-time targets file (what Terraform reads to decide what to plan) and the file copied onto the VM (inside the code zip) are the same object, not 2 files that could drift apart; the implementation keeps them as 1 file through the whole deploy path.

## Consequences

- Spec changes in the same pull request as this ADR: spec 0002 (run modes), spec 0003 (config schema, scheduler, object key), spec 0004 (the S3 lifecycle rule), spec 0005 (the Terraform targets-to-resources rule), spec 0006 (the `run_mode` filter and the GPU-size rule), spec 0008, spec 0009, and spec 0010 (each `run_mode` field and the per-object split), each marked "not implemented yet" and pointing at this ADR.
- `config/targets.yaml` keeps its current `aws`/`azure`/`deep_probes`/`run_mode` shape until the implementation pull request lands; it is not rewritten by this ADR.
- The Azure names of the capacity reservation groups and the scale sets do not change by this decision alone.
- A future pull request removes `run_mode` from the `Record` schema (spec 0002 §7.2) and from every row, and runs the migration of section 5, item 6 (stop, copy, compare, delete, restart) on the existing data under `run_mode=cloud-gpu/`, so no reader ever needs to read the old layout. Superseded by [ADR 0011](0011-store-and-per-job-simulate-replace-run-mode.md): `run_mode` is removed from new rows only. A row written before the change keeps its `run_mode` field, and a reader applies the rule of ADR 0011 section 5.
- A future pull request writes the actual modules/jobs schema, its `check-config` validation, and the billable guard against jobs; this sketch (section 9) is illustrative, not the schema.
