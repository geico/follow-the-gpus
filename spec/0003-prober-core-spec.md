---
title: "Prober core spec: command line, config, records, storage, plugins, scheduler, and the sim generator"
date: 2026-10-11
---

# Prober core spec

**Status:** Phase 1 build · 2026-10-11
**Parent:** [spec 0002](0002-gpu-capacity-tracker-spec.md), sections 7, 8.1, 8.4, D2, D13, D19.
**Code:** `prober/` (Python package `ftg_prober`), `config/targets.yaml`, `tasks/Makefile.prober`.

A person must be able to build the prober core again from this file and spec 0002. The core has 7 parts: the command line, the config, the record and key formats, the storage backends, the probe plugin contract, the scheduler, and the `sim` generator. The probes themselves are not part of the core. Wave 2 adds them as modules under `ftg_prober/aws/` and `ftg_prober/azure/`.

## 1. Scope and rules

1. Scope of the experiment: continental US commercial regions, GPU only. Spot signals are the core analyzed data (spec 0002, D1).
2. The core must not call a cloud API. Only probe modules call cloud APIs.
3. The core takes no secrets. Each SDK uses its default credential chain (section 9).
4. The core keeps no durable state. A crash loses at most the probe call in flight (spec 0002, 7.1).
5. Python 3.12 or later. The tests run with no cloud credentials and no network ([ADR 0005](../decisions/0005-prober-and-sweeper-in-python.md)).

## 2. Command line

The package declares the script `ftg-prober = "ftg_prober.cli:main"`. `main(argv)` returns the exit code.

```text
ftg-prober run --provider aws|azure --config PATH --store URL [--once] [--probe NAME] [--simulate]
ftg-prober sim --config PATH --store URL --hours N [--seed S]
ftg-prober check-config PATH [--provider aws|azure]
ftg-prober compact --provider aws|azure --config PATH --store URL
ftg-prober --version
ftg-prober --log-level DEBUG|INFO|WARNING|ERROR <command>
```

ADR 0011 replaces the `run_mode` config field with `--store` (kept, already required) and a run-wide `run --simulate` flag, alongside a job's own `simulate` key (section 3). `run --simulate` sets `simulate: true` on every job for that run only, and refuses a `--store` that is not `file://` (section 7.1). `sim` keeps its own shape; it now gets the same `file://`-only rule. `--probe NAME` keeps its meaning (it limits the run to 1 already-listed job by name), and the allocation guard still runs before that job; neither flag can enable a job that the file does not list.

| Command | Behavior |
|---|---|
| `run` | Load the config. Load the probes of `--provider` (section 5). Run them at their cadence (section 6). Without `--once` the loop runs until SIGINT or SIGTERM. When the provider has 0 loaded probes, `run` logs `run.start` with `probes=0` and exits 0 at once, with or without `--once`; this is expected until the probe modules of that provider exist (`prober/README.md`, "No probes for a provider"). `--once` runs each probe 1 time. `--probe NAME` limits the run to the probe with `probe_type` = `NAME`. `--simulate` simulates every job of the run (section 7.1); it also refuses a `--store` that is not `file://`. |
| `sim` | Write `--hours` hours of synthetic, simulated rows for every `probe_type` of section 4.1 (section 7). `--seed` defaults to 42. Refuses a `--store` that is not `file://`, the same as `run --simulate`. |
| `check-config` | Validate the file against section 3. Print 1 `ok` line and exit 0, or print each fault and exit 2. `--provider aws\|azure` also does everything `run --provider` does up to its first network call: `config.section(provider)` (a config error when the section is missing), `load_probes(packages_for(provider))`, and the `Scheduler` construction and `select()`. This is the VM-side sync gate of section 6A: a config with no `provider:` section, or a probe plugin that fails to load or construct, fails this stronger check the same way `run --provider` would, with no network call on either side of it. |
| `compact` | Pack each closed UTC day of the config file's experiment into 1 daily file per probe type, in the store (section 4.3), with the rules of section 9B. Exit 0 when every day is complete, 1 when 1 or more days failed (the next run tries them again), 2 on a config error or a bad store URL. A `file://` store works too, for tests. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Clean stop: `--once` finished, `sim` finished, `check-config` passed, or the loop got SIGINT or SIGTERM. |
| 2 | Config error: the file is missing, is not YAML, or breaks the schema. Also a bad store URL, a `--store` that is not `file://` under `--simulate` or `sim`, an unknown `--probe` name, a simulated store folder that is not empty (section 7.2), or a usage error. |

Store URL (`--store`): `file:///abs/path`, `s3://bucket[/prefix]`, or `az://<storage account>/<container>[/prefix]`. Section 9 defines each backend.

Environment variables:

| Variable | Read by | Effect |
|---|---|---|
| `FTG_PROBE_RG` | Azure provisioning probes | The Azure probe resource group. The core does not read it. |
| `AZURE_SUBSCRIPTION_ID` or `ARM_SUBSCRIPTION_ID` | core, Azure client factory | The subscription for the `compute` client (section 5.3). |

ADR 0011 removes `FTG_RUN_MODE_OVERRIDE` and `FTG_EXPERIMENT` (core). The experiment file's `experiment` key and each job's own `simulate` key are the only source of what a run does; nothing outside the file can change it. The sweeper's own `FTG_EXPERIMENT` app setting (spec 0007) is a different variable and stays: the Azure stack fills it, and the AWS IAM tag conditions (`iam.tf`), from `local.experiment`, the targets file's own `experiment` key ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 2), the same source the prober reads, not from a separate `experiment_tag` variable.

Install on a VM: `pip install "/opt/ftg/releases/<release ID>/prober[aws]"` or `"[azure]"`, into a venv at `/opt/ftg/releases/<release ID>/venv/` (section 6A). The systemd unit runs `ftg-prober run --provider <p> --config /opt/ftg/current/config/targets.yaml --store <store URL>`, where `/opt/ftg/current` is a symlink to the release currently in service.

### 2.1 Signals and the sleep

The loop must stop at once on SIGINT or SIGTERM, also during a sleep between probe calls. In Python 3.5 and later, `time.sleep` continues after a signal when the handler does not raise an exception ([Python docs, time.sleep](https://docs.python.org/3/library/time.html#time.sleep)). Thus the loop must not sleep with `time.sleep`. It must wait on a `threading.Event` that the signal handler sets.

## 3. Config schema (`config/targets.yaml`)

One file drives both clouds (spec 0002, 8.4). A single-cloud user deletes the other section. The loader uses `yaml.safe_load`. ADR 0010 built the schema below: an experiment file of modules (how to probe) and jobs (what, where, how often), with an allocation guard on the provisioning probes. A listed job is enabled; nothing else enables it; a job for a probe type with no job never runs.

A **module** says how to probe: a probe type plus the settings it overrides. The probe types and their code defaults are built into the code, 1 place per probe type (`MODULE_DEFAULTS` in `config.py`): `aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest`, `az_spot_score`, and `az_spot`. A file may also name its own modules under `modules:`, each a `type:` (1 of the probe types above) plus the settings it overrides. A **job** says what, where, and how often: a `module:` (a built-in probe type, or a name from `modules:`), 1 or more `regions`, and an optional `interval` (default: the probe's own cadence). The override merges with the code default at the top level only: a key the module sets replaces the whole default value for that key; a key it omits keeps the code default. A list or a nested mapping is replaced as a whole, never merged field by field. For example, `aws_cb_offering`'s code default settings are `{instance_types: [p5.4xlarge, p4d.24xlarge], instance_count: 1, duration_hours: [24], start_window_days: 14}` (`MODULE_DEFAULTS["aws_cb_offering"]` in `config.py`; the ADR 0010 section 3 worked example lists 2 of these 4 keys, `instance_types` and `duration_hours`, word for word). A module override of `{duration_hours: [24, 48]}` gives `instance_types: [p5.4xlarge, p4d.24xlarge]` (kept, the override did not set this key) and `duration_hours: [24, 48]` (replaced as a whole list, not appended to the default `[24]`); `test_adr_0010_section_3_merge_example` in `test_config.py` asserts the whole merged mapping.

The provisioning probes (`aws.provisioning_probes`, `azure.provisioning_probes`) and `*.quota` stay outside the jobs model: each is a flat list or mapping, as before, next to `modules:` and `jobs:`. Each provisioning entry carries its own `simulate` and `allow_billable_provisioning` keys (ADR 0011 section 3), in place of the earlier per-entry `run_mode`. `allow_billable_provisioning` is checked only here, by the prober's own config guard (`check-config` and `run`); [ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md) keeps this guard exactly as it was, and does not have the Terraform stack of either cloud read this key: a stack's own `grant_billable_provisioning` variable (spec 0004 section 4.2, spec 0005 section 4.1) is the only thing that decides whether it is capable of provisioning real capacity, independent of this file. An active, consented job with no corresponding grant still gets refused by the cloud provider at call time, with the provider's own error written to the log as returned (for example `UnauthorizedOperation` on EC2, or [`ResourceNotFound`](https://learn.microsoft.com/en-us/azure/azure-resource-manager/troubleshooting/common-deployment-errors) or [`AuthorizationFailed`](https://learn.microsoft.com/en-us/azure/role-based-access-control/troubleshooting) on Azure), not by a 2nd, Terraform-side copy of this guard.

```yaml
experiment: ftg-20261012-signals      # required; dated and descriptive (section 3, `experiment` row)
aws:                                  # optional, but aws or azure must exist
  modules:                            # optional; named overrides of a probe type's code default
    aws_spot_score:
      type: aws_spot_score
      spot_score_requests:            # 1 to 11 entries, unique names (spec 0002, 4.2)
        - {name: p5-1x, instance_types: [p5.4xlarge], label: "1x H100"}
        - {name: control-cpu, instance_types: [m5.4xlarge], label: "Control: CPU alone"}
    aws_cb_offering:
      type: aws_cb_offering
      instance_types: [p5.4xlarge, p5.48xlarge, p5en.48xlarge, p6-b200.48xlarge, p4d.24xlarge]
      instance_count: 1
      instance_counts: {p5.4xlarge: [1, 4, 8]}   # optional, per type; default instance_count; spec 0008, 3.3
      duration_hours: [24, 72, 120, 168]
      start_window_days: 14
      earliest_duration_hours: [24, 168]    # optional, default duration_hours; spec 0008, 3A
  jobs:                                # a listed job is enabled; nothing else enables it
    - {module: aws_spot_score, regions: [us-east-1, us-east-2, us-west-2], interval: 5m}
    - {module: aws_spot_price, regions: [us-east-1, us-east-2, us-west-2], interval: 5m}
    - {module: aws_spot_advisor, regions: [us-east-1, us-east-2, us-west-2], interval: 24h}
    - {module: aws_cb_offering, regions: [us-east-1, us-east-2, us-west-2], interval: 2h}
    - {module: aws_cb_earliest, regions: [us-east-1, us-east-2, us-west-2], interval: 2h}
    - {module: aws_cb_longest, regions: [us-east-1, us-east-2, us-west-2], interval: 2h}
  cadence: {aws_odcr: 2h}             # the provisioning probe's cadence key; it is not a job
  provisioning_probes:                  # spec 0002, 4.9; each entry carries simulate/allow_billable_provisioning
    - {instance_type: m5.large, region: us-east-1, n: 2, simulate: true}
azure:
  modules:
    az_spot_score:
      type: az_spot_score
      spot_sizes: [Standard_NC40ads_H100_v5, Standard_NC24ads_A100_v4, Standard_NV36ads_A10_v5, Standard_NC4as_T4_v3, Standard_NC144ds_xl_RTXPRO6000BSE_v6]
    az_spot:
      type: az_spot
      spot_sizes: [Standard_NC40ads_H100_v5, Standard_NC24ads_A100_v4, Standard_NV36ads_A10_v5, Standard_NC4as_T4_v3, Standard_NC144ds_xl_RTXPRO6000BSE_v6]
      spot_price_max_age_days: 540
  jobs:
    - {module: az_spot_score, regions: [eastus, eastus2, southcentralus, westus2, westus3, centralus, northcentralus, westus, westcentralus], interval: 15m}
    - {module: az_spot, regions: [eastus, eastus2, southcentralus, westus2, westus3, centralus, northcentralus, westus, westcentralus], interval: 5m}
  cadence: {az_provisioning: 2h}       # the pre-check's and both provisioning probes' cadence key
  provisioning_probes:                  # spec 0002, 4.3 to 4.5; each entry carries simulate/allow_billable_provisioning
    - {sku: Standard_B2s, region: eastus, zone: null, n: 2, placement_group_mode: multi, simulate: true}
```

`load_config` resolves `modules:` and `jobs:` and promotes the result onto the flat keys of the table below, so that the scheduler and every probe read `ctx.config["regions"]`, `ctx.config["spot_score_requests"]`, and so on, exactly as they did before this schema (`Config.aws`/`Config.azure` are this resolved, flat mapping; `_resolved_section` in `config.py`). A job's `interval` promotes to a `*.cadence` entry for its probe type.

This build keeps exactly 1 shared, flat `regions` list per cloud, 1 shared `spot_sizes` list for Azure, and 1 shared `capacity_blocks` mapping for AWS, because that is what the scheduler and every probe already read. Rather than silently union or pick a winner when jobs disagree, `check-config` refuses the file: every job of 1 provider must list the same `regions`, and `az_spot_score`'s and `az_spot`'s `spot_sizes` must be equal when both are listed (critical review finding of the first build of this schema). A probe type that reads another job-eligible type's settings but has none of its own (`aws_spot_price`, `aws_spot_advisor`, `aws_cb_earliest`, `aws_cb_longest`) needs that other job listed too, or `check-config` refuses the file and names the missing job (`JOB_DEPENDENCIES` in `config.py`): `aws_spot_price` and `aws_spot_advisor` need `aws_spot_score`, for its `spot_score_requests`; `aws_cb_earliest` and `aws_cb_longest` need `aws_cb_offering`, for its capacity block settings. A job-eligible probe type with no job never runs: the scheduler drops it from the probes it loads (`Config.has_job`), so an `aws_spot_price` job with no `aws_spot_score` job cannot run with an empty request list either way.

A cloud section accepts only a known set of keys (`KNOWN_CLOUD_KEYS` in `config.py`, below); any other key is refused with a generic message that points at this section, instead of being silently ignored.

| Old key | New place |
|---|---|
| `aws.regions`, `azure.regions` | each job's own `regions` list (ADR 0010 section 1); every job of 1 provider must list the same `regions` |
| `aws.cadence.<job-eligible type>`, `azure.cadence.<job-eligible type>` | that job's own `interval`; `*.cadence` keeps only the provisioning probes' keys (`aws_odcr`, `az_provisioning`) |
| `aws.spot_score_requests` | the `aws_spot_score` module's `spot_score_requests` setting |
| `aws.capacity_blocks` | the `aws_cb_offering` module's settings (`instance_types`, `instance_count`, `duration_hours`, `start_window_days`, `earliest_duration_hours`, `instance_counts`) |
| `azure.spot_sizes` | the `az_spot_score`/`az_spot` module's `spot_sizes` setting; both must agree when both are listed |
| `azure.spot_price_max_age_days` | the `az_spot` module's `spot_price_max_age_days` setting |
| `aws.provisioning_probes`, `azure.provisioning_probes`, `aws.quota`, `azure.quota` | never moved, and stay outside the jobs model (ADR 0010 sections 7 and 8); each provisioning entry's `run_mode` becomes `simulate` and `allow_billable_provisioning` (ADR 0011 section 3) |

`check-config` refuses any other cloud-section key with `<provider>.<key> is not a known key. See spec 0003 section 3 for the file shape.`; see `KNOWN_CLOUD_KEYS` and `_unknown_key_faults` in `config.py`. The message echoes the key as the file gave it; the known-key list above never grew a 2nd name for a key once it moved.

Validation rules. `check-config` must report every fault, 1 per line, with the path of the field, for example `aws.jobs[aws_spot_score].spot_score_requests[3].name must be a non-empty string`.

| Field | Rule |
|---|---|
| top level | A mapping. Keys: `experiment`, `aws`, `azure` only. At least 1 of `aws`, `azure`. |
| `experiment` | Required. A string of at most 63 characters that matches `^[a-z0-9]+(-[a-z0-9]+)*$` (ADR 0010 section 2), so 1 name is safe in the storage path, a cloud tag value, and an OpenTofu value. The name is dated and descriptive: 1 or more words, then a `-YYYYMMDD-` word that is a real calendar date, then at least 1 descriptive word, for example `ftg-20261012-signals`. `ftg`, `ftg-signals`, `ftg-20261399-x`, and `ftg-20261012` are faults. Every row is stored under `experiment=<name>/` (section 4.2), so a default name mixes the rows of unrelated runs: there is no default for a real run (decision, 2026-10-14). Only a fully simulated run (`sim`, or `run --simulate`) can omit the key; it then uses `ftg-<today, UTC, YYYYMMDD>-simulated`. The file is the only source: the stacks and the sweeper read this same value (spec 0004, spec 0005, spec 0007), and there is no override. |
| Allocation guard | ADR 0011 section 3 replaces the earlier file-level `allow_billable` key. An `azure.provisioning_probes` entry or an `aws.provisioning_probes` entry is a provisioning entry (it creates a VM or a Capacity Reservation) unless its own `simulate` key is `true`. A non-simulated provisioning entry needs its own `allow_billable_provisioning: true`; without it, the check reports 1 fault per such entry, naming the entry (index, `sku` or `instance_type`, `region`) and saying to set `allow_billable_provisioning: true` on purpose, or add `simulate: true`. The prober does not start on a config fault, in `run` or in `check-config`. `run --simulate`, and the `sim` command, simulate every job of the run, so the guard never fires under either (`force_simulate` in `config.py`); `check-config` has no run-wide flag, so it reads only each entry's own `simulate` and `allow_billable_provisioning` keys. None of the job-eligible probe types of this section (`aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest`, `az_spot_score`, `az_spot`) is a provisioning entry, so a job never triggers this guard; only `provisioning_probes` entries do. |
| `*.modules` | Optional mapping of a name to a mapping with `type` (1 of the job-eligible probe types above, for that cloud) plus the settings it overrides. `type` must be a known probe type of that cloud. Any other key must be 1 of that probe type's own settings (`MODULE_DEFAULTS[type]` in `config.py`, plus `earliest_duration_hours` and `instance_counts` for `aws_cb_offering`); any other key is a fault, so a setting of 1 probe type (for example an allocation-probe-shaped `provisioning_probes` list) cannot ride along on an unrelated module and reach the flat section past the allocation guard (critical review finding). |
| `*.jobs` | Optional list (default: none); `null` is a fault, not an empty list. Each entry has `module` (a name from `*.modules`, or a built-in probe type of that cloud, directly), `regions` (non-empty list of non-empty strings), an optional `interval` (a cadence string, section 6 rule 1), an optional `simulate` (boolean, default `false`; ADR 0011 section 2): `true` runs the job through the `sim.py` generator of section 7, with no cloud call, and an optional `allow_billable_provisioning` (boolean, default `false`; ADR 0011 section 3), reserved for a future allocation job-eligible type; no job-eligible type of this build is an allocation type (the allocation guard row, above), so it is currently a no-op. A non-boolean `simulate` or `allow_billable_provisioning` is a fault that names the job. Any other key is a fault (`JOB_ENTRY_KEYS` in `config.py`, 1 constant so that a later change can extend it; it now holds `module`, `regions`, `interval`, `simulate`, and `allow_billable_provisioning`). A `module` that is in `*.modules` wins over a same-named built-in probe type (open point of ADR 0010 section 12, decided here); when that `*.modules` entry's own `type` is itself a fault, the job is skipped rather than resolved against a built-in type of the same name, so only 1 fault is reported. At most 1 job per probe type; a second job for the same type is a fault. An unknown `module` (not in `*.modules` and not a built-in probe type of that cloud) is a fault, refused at load, the same as any other bad value in the file (ADR 0010 section 11, rule 6). |
| `*.regions` | Not a top-level key any more: a job's own `regions` list (above). Every job of 1 provider must list the same `regions` (1 shared, flat `*.regions` list, as the scheduler and the probes read it); a file whose jobs disagree is refused. |
| `*.run_mode` | Removed (ADR 0011 section 6). There is no section-level mode any more: a signal job's own `simulate` key (above) and the run-wide `--simulate` flag decide, and a provisioning entry carries its own `simulate` key (section 3's `aws.provisioning_probes`/`azure.provisioning_probes` rows, below). |
| `*.cadence` | Optional mapping, for the provisioning probes only: the AWS provisioning probe's key is `aws_odcr`; the Azure pre-check and both Azure provisioning probes share the key `az_provisioning`. A job-eligible probe type's cadence is its job's `interval` instead (above); it is a fault to also set that probe type's key in `*.cadence` (the key would be ignored, so the file does not carry 2 answers). |
| `aws.jobs[aws_spot_score].spot_score_requests` | List of 1 to 11 mappings. Each has `name` (string, unique), `instance_types` (non-empty list of strings), `label` (string); any other key is a fault (`SPOT_SCORE_REQUEST_KEYS` in `config.py`). The code default is an empty list, so a file that lists the `aws_spot_score` job must override this setting. |
| `aws.jobs[aws_cb_offering].instance_types`, `.instance_count`, `.duration_hours`, `.start_window_days`, `.earliest_duration_hours`, `.instance_counts` | The settings of spec 0002 section 4.1: `instance_types` (non-empty list of strings), `instance_count` (integer, 1 or more), `duration_hours` (non-empty list of integers more than 0), `start_window_days` (integer, 1 or more), and optional `earliest_duration_hours` (list of integers more than 0; default `duration_hours`; an empty list turns `aws_cb_earliest` off), and optional `instance_counts` (a mapping of an instance type to a non-empty list of distinct integers, 1 or more; each key must be in `instance_types`; a type not in it uses `instance_count`). The number of (type, count) pairs times (the number of `duration_hours`, plus the number of `earliest_duration_hours`, plus 19, the worst case of 1 `aws_cb_longest` search) must be 200 or fewer. The calls wait in a queue and drain at 1 call per Region per wake ([spec 0008](0008-aws-probes-spec.md), sections 3.3, 3A.3, and 3B.5). `aws_cb_earliest`'s and `aws_cb_longest`'s jobs share this same `capacity_blocks` data (there is 1 flat `capacity_blocks` key after resolution); neither needs a module of its own. |
| `aws.probe_timeout_seconds`, `aws.poll_interval_seconds`, `aws.teardown_timeout_seconds` | Optional integers, 1 or more (spec 0002, section 4.9). `probe_timeout_seconds` must be longer than `poll_interval_seconds` (defaults 90 and 5 when a key is absent): the call deadline floor is `max(longest probe, poll interval)` and the entry deadline runs from before the precheck, so an equal or shorter timeout lets no create call start. The config check refuses it. "Longer" is a floor, not a safe value: the entry deadline also covers the precheck time, so the timeout must exceed the poll interval by at least that time or the entry writes only a skipped `call deadline` row. On AWS the precheck is `describe_instance_type_offerings` and the quota calls (including `describe_instance_types` inside `quota_ceiling`); on Azure it is the Resource SKUs list and `list_usage` (`run_precheck`). The wait for the Region lock comes before the entry deadline starts. The check adds no margin for the precheck time. Out of scope for this change: these describe the `aws_odcr` provisioning probe, which stays outside the jobs model. |
| `aws.provisioning_probes` | Optional list. Each entry has `instance_type` (an EC2 instance type), `region` (string), `n` (integer, 1 or more; no `zone`: the probe covers every zone of the Region that offers the type, spec 0002 section 4.9; 2 entries with the same type and Region are a fault), an optional `n_max` (integer greater than `n`, at most `N_MAX_LIMIT` = 16, valid on a simulated entry; it turns on the upward search of [ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md)), an optional `simulate` (boolean, default `false`), an optional `allow_billable_provisioning` (boolean, default `false`; required when the entry is a provisioning entry and not simulated, above), and an optional `sim_script` (a fault-injection step, or a list of steps cycled 1 per call, used when the entry is simulated; spec 0002, section 4.9); any other key is a fault (`AWS_PROVISIONING_ENTRY_KEYS` in `config.py`, now `instance_type`, `region`, `n`, `n_max`, `simulate`, `allow_billable_provisioning`, `sim_script`). The prober does not validate `sim_script`'s own shape; an unknown step raises only when the entry is simulated. |
| `azure.jobs[az_spot_score].spot_sizes`, `azure.jobs[az_spot].spot_sizes` | Non-empty list of strings. Each of the 2 modules carries its own copy; when both are listed, the 2 copies must be equal (1 shared, flat `spot_sizes` list), or `check-config` refuses the file. |
| `azure.jobs[az_spot].spot_price_max_age_days` | Optional integer, 1 or more ([spec 0009](0009-azure-spot-signals-spec.md), section 3.5). |
| `azure.probe_timeout_seconds`, `azure.poll_interval_seconds`, `azure.teardown_timeout_seconds` | Optional integers, 1 or more ([spec 0010](0010-azure-provisioning-probes-spec.md), section 10). `probe_timeout_seconds` must be longer than `poll_interval_seconds` (same rule, floor note, and defaults as `aws.probe_timeout_seconds`, above). Out of scope for this change: these describe the Azure provisioning probes, which stay outside the jobs model (same note as `aws.probe_timeout_seconds`, above). |
| `azure.provisioning_probes` | Optional mapping from a probe type (`az_capacity_reservation` or `az_vmss_flex`) to a list of entries ([ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)). The loader flattens the mapping into 1 list and adds `probe_type` to each entry. The old list shape, an unknown probe type key, and more than 1 `az_vmss_flex` entry for 1 size and Region (case-insensitive, any zone) are faults. Each entry has `sku` (string), `region` (string), `zone` (string, number, or null), `n` (integer, 1 or more), `placement_group_mode` (`multi`; the value `single` is a fault that names [ADR 0021](../decisions/0021-provisioning-probes-measure-available-capacity-not-single-cluster-capacity.md)), an optional `n_max` (integer greater than `n`, at most `N_MAX_LIMIT` = 16, valid on a simulated entry; the upward search of [ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md)), an optional `simulate` (boolean, default `false`), an optional `allow_billable_provisioning` (boolean, default `false`; required when the entry is a provisioning entry and not simulated, above), and 1 further optional key ([spec 0010](0010-azure-provisioning-probes-spec.md), section 10): `sim_script` (a fault-injection step, or a list of steps, used when the entry is simulated). A `route` key is a fault that names ADR 0022. There is no `run_mode` key (ADR 0011, final): `terraform/azure/probe.tf`'s "active" rule reads `simulate` directly (`try(p.simulate, false) != true`), not a stack-side part-B change, so `run_mode` has no reader left anywhere. An entry that still carries `run_mode` is a fault that names it and says to use `simulate: true` instead. Any other key is a fault (`AZURE_PROVISIONING_ENTRY_KEYS` in `config.py`, now `sku`, `region`, `zone`, `n`, `n_max`, `placement_group_mode`, `simulate`, `allow_billable_provisioning`, `sim_script`). |
| `aws.queue_wake_seconds`, `aws.queue_max_calls_per_wake`, `aws.queue_max_throttle_attempts` | Optional integers, 1 or more. Defaults 30, 1, and 5. The queue worker of section 6A. |
| `aws.queue_jitter`, `aws.queue_backoff_base_seconds`, `aws.queue_backoff_cap_seconds` | Optional numbers, 0 or more; `queue_jitter` must also be less than 1. Defaults 0.2, 30, and 600. Section 6A. |
| `aws.quota`, `azure.quota` | Optional mapping. The 2 blocks have 1 shape: `regions` (non-empty list of region names) and `sizes` (non-empty list of mappings with `size`, `count`, and optional `regions`; any other key in a `sizes` entry is a fault, `QUOTA_SIZE_ENTRY_KEYS` in `config.py`). The field rules are in [spec 0011](0011-azure-quota-requests-spec.md), section 3, for both clouds. Each quota region must be in the resolved, flat `<cloud>.regions` (the union of the job regions, above). A region name is a Region code in `aws.quota` (for example `us-east-1`) and lowercase letters and digits in `azure.quota` (for example `eastus2`). A `size` is an EC2 instance type in `aws.quota` ([spec 0012](0012-aws-quota-requests-spec.md), section 3) and a VM size in `azure.quota`. One function checks both blocks. The prober does not read these keys. Unchanged, outside the jobs model (ADR 0010 section 8). |

An unknown cloud-section key is a fault (above). The same rule applies inside every list in this schema, not only to a cloud section: a key outside the ones named in the row for that list (a `*.jobs` entry, a `*.quota.sizes` entry, an `*.provisioning_probes` entry, or an `aws.jobs[aws_spot_score].spot_score_requests` entry) is a fault that names the entry and the key, instead of a key like a typo'd `intervall` going dark with no error. The old consent key `allow_allocation` gets this same fault, with a note that names `allow_billable_provisioning` (ADR 0016); there is no alias.

Merge edge cases (ADR 0010 section 12, decided here):

1. A key set to `null` or `{}` in a module override is not special: it replaces the code default for that key with `null` or `{}`, the same as any other override value. The loader does not distinguish "clear this key" from "set this key to an empty value"; a module that wants a setting to keep its code default simply omits the key.
2. A name in `*.modules` that is also a built-in probe type wins over the built-in type when a job names it: the loader looks up `*.modules` first, and only falls back to a built-in probe type when the name is not a module.
3. The Azure provisioning probes' grouped timeout (`probe_timeout_seconds`, `poll_interval_seconds`, `teardown_timeout_seconds`) is not split per module in this change, because the provisioning probes stay outside the jobs model (ADR 0010 section 10, item 4): all allocation modules of a cloud still share the 1 top-level set of timeouts, exactly as before. A later pull request that moves the provisioning probes into jobs decides the per-module split. Superseded further by [ADR 0011](../decisions/0011-store-and-per-job-simulate-replace-run-mode.md): that later pull request also removes `*.run_mode` (a per-job `simulate` key and a run-wide `--simulate` flag replace it, not a `--mode` flag) and the top-level `allow_billable` key (a per-job `allow_billable_provisioning` key replaces it, next to the job's own `simulate` key).

The repo file `config/targets.yaml` must hold: the Day 1 request list of spec 0002 section 4.2 (8 entries) as the `aws_spot_score` module's `spot_score_requests`, the Capacity Block types of section 4.1 as the `aws_cb_offering` module's settings, 3 AWS Regions, the 9 Azure US regions, 5 or more Azure GPU sizes from [research/gpu-lineup-aws-azure.md](../research/gpu-lineup-aws-azure.md), and 2 `provisioning_probes` entries, all on `Standard_D2as_v4` with `zone: null`, `n: 2`, and `simulate: true`: `eastus` with `placement_group_mode: multi` and `eastus2` with `placement_group_mode: multi`. It must also hold 1 or more `aws.provisioning_probes` entries, all `simulate: true` (spec 0002, section 4.9). The OpenTofu default targets file is this file, so no entry sets `allow_billable_provisioning: true`, and a fresh deploy creates no provisioning probe capacity. The Azure entries carry no `run_mode` key: `terraform/azure/probe.tf`'s "active" rule reads each entry's `simulate` key directly. It must also hold the `azure.quota` section of spec 0011, section 3, and the `aws.quota` section of spec 0012, section 3.

## 4. Record and key formats

### 4.1 Record

One row per probe attempt or poll. The fields and their order are the rows of spec 0002 table 7.2:

`ts_utc`, `run_id`, `simulated`, `provider`, `region`, `zone`, `probe_type`, `sku`, `placement_group_mode`, `n_requested`, `n_placed`, `outcome`, `error_code`, `latency_ms`, `details`.

Rules:

1. `ts_utc` is ISO 8601 in UTC with milliseconds and a `Z` suffix: `2026-10-11T19:51:03.123Z`.
2. `run_id` is a UUID string. Each probe call gets a new `run_id`. All rows of 1 call share it.
3. `simulated` is `true` or `false` (ADR 0011 section 6, item 4, replacing `run_mode`). `provider` is `aws` or `azure`. A row written before this change has `run_mode` and no `simulated` field; a reader applies the rule of ADR 0011 section 5: `run_mode: local` means `simulated: true`, any other value means `simulated: false`.
4. `zone` is null for a regional row. `n_placed` and `error_code` are null when unknown or absent.
5. `probe_type` is 1 of 12 values, in this order: `aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest`, `aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`, `az_spot_score`, `az_spot`, and `az_precheck`. All 12 are the values of table 7.2. `aws_odcr` is the AWS provisioning probe (spec 0002, section 4.9). `az_precheck` is the Azure pre-check ([spec 0010](0010-azure-provisioning-probes-spec.md), decision Z1). `outcome` is 1 of the 10 values of table 7.2. `placement_group_mode` is `single`, `multi`, or `n/a`.
6. `details` is a JSON object. The value of a signal (a score, a price, a range) goes in `details`.
7. `sku` holds 1 instance type or VM size. For a Spot score request with more than 1 instance type, `sku` holds the types joined with a comma, in request order.

The code must have a `Record` dataclass with these fields, `to_json()` that returns 1 JSON object with no new line inside it, and `validate()` that returns the list of faults. A unit test must read table 7.2 from spec 0002 and compare the field names and the `probe_type` list with the code. The code list ends with `az_precheck`. The test needs an exact match.

A file holds the rows of 1 probe call as JSON Lines: 1 object per line, a new line after each line, UTF-8.

### 4.2 Object key

`experiment=<name>/provider=<p>/probe_type=<t>/date=<YYYY-MM-DD>/<ts>_<target>_<uuid>.jsonl`

1. `date` and `ts` are UTC. `ts` is `YYYYMMDDTHHMMSSZ`, the time at the end of the call.
2. `target` is `<sku slug>-<region slug>`. A slug is lower case, `[a-z0-9-]` only: each run of other characters becomes 1 dash, and the dashes at the ends are removed. For example `p5.48xlarge` in `us-east-1` gives `p5-48xlarge-us-east-1`, and `Standard_B2s` in `eastus` gives `standard-b2s-eastus`.
3. When the rows of 1 call differ in `sku`, the sku part is `multi`. When they differ in `region`, the region part is `multi`. For example `multi-us-east-1`, or `multi-multi`.
4. `uuid` is the `run_id` of the call, or a new UUID.
5. A raw feed object (spec 0002, 4.8) uses the same path with `<ts>_raw_<uuid>.json`.
6. Never overwrite a raw row object or a log object. Each backend of `Store.put`, which writes the rows and the prober's logs, refuses a key that exists (section 9). The sweepers write their log objects with their own code, each under a new UUID key (section 9, rule 5). The only objects that are ever replaced are the daily files of section 4.3, under `experiment=<name>/daily/v1/`, by the compaction job (section 9B).
7. `date` stays the last partition ([ADR 0010](../decisions/0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md) section 5, item 4, kept by ADR 0011 section 6). A simulated row's key looks exactly like a real row's key: `simulated` is a row field (section 4.1), not a path segment. A fully simulated run (`run --simulate`, or `sim`) writes only to a local `file://` path, never to the cloud bucket.

The Hive-style `key=value` folders let a DuckDB query read the partition values as columns with the `hive_partitioning` flag ([DuckDB docs, Hive partitioning](https://duckdb.org/docs/current/data/partitioning/hive_partitioning.html)). DuckDB reads JSON Lines files with `read_json` in the `newline_delimited` format ([DuckDB docs, JSON loading](https://duckdb.org/docs/current/data/json/loading_json.html)). The DuckDB docs show the flag with `read_parquet`. The dashboard build must test it with `read_json` on these folders.

### 4.3 Daily files

[ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md). A daily file holds every row of 1 closed UTC day and 1 probe type. It is derived data: the raw objects of section 4.2 stay the source of truth.

`experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst` (the daily file)
`experiment=<name>/daily/v1/date=<D>/<probe_type>_<D>.jsonl.zst.manifest.json` (its manifest)

1. `D` is the `date=` key (`YYYY-MM-DD`, UTC) of the raw objects, the same rule as `scripts/compact-rows.sh` ([spec 0006](0006-dashboard-spec.md) section 5.5).
2. Layout reasons (decision, 2026-10-27): the date comes first, so a sync lists only the `date=` folder names and copies about 14 small files per new day, and a time range is a set of `date=` folders; there is no `provider=` folder, because each cloud has its own store and each probe type starts with `aws_` or `az_`; `v1` lets the format change later; the file name repeats the probe type and the day, so a copied file explains itself.
3. The second path segment is `daily`, not `provider=`, so the row glob of [spec 0006](0006-dashboard-spec.md) section 4.1 never reads a daily file, and the `expire-logs` rule (section 8.2) never matches it.
4. The daily file is 1 zstd frame with a content checksum. Decompressed, it holds the lines of the raw `*.jsonl` objects of that day and probe type, byte for byte: 1 object per base name, in byte order of the base names, a new line added after a last line that has none. The raw feed objects (`*_raw_*.json`) are not in it. DuckDB `read_json` reads it ([DuckDB, loading JSON](https://duckdb.org/docs/current/data/json/loading_json.html)).
5. The manifest is the JSON object that `compact_day` in `prober/ftg_prober/compact.py` builds: `raw_object_count`, `kept_object_count`, `row_count`, `rows_bytes` and `rows_sha256` (the decompressed bytes), `unparsed_row_count`, `first_ts_utc`, `last_ts_utc`, `objects_sha256` (the SHA-256 of the sorted `<base name>\t<size>` lines of the kept objects; `objects_sha256_from_listing` computes it from a listing), `ftg_prober_version`, `built_utc`, and `file_bytes` and `file_sha256` (the compressed daily file).
6. A daily file is complete when its manifest exists and the file has the manifest's `file_bytes` and `file_sha256`. The manifest is written last, so a reader uses the raw objects of that day and probe type until then.

### 4.4 The `details` contract

Moved from spec 0006 section 6.3. A probe must write these `details` keys with these types. Specs 0008, 0009, and 0010 give the rows of each probe, and `prober/tests/test_sim.py` reads this table. The dashboard reads only the keys of [spec 0006](0006-dashboard-spec.md) section 6.4.

| `probe_type` | Keys | Type |
|---|---|---|
| `aws_spot_score` | `details.score`, `details.instance_types` | integer 1 to 10; array of strings |
| `aws_spot_price` | `details.price`, `details.price_ts` | number, USD per hour; string, the AWS record time |
| `aws_spot_advisor` | `details.interruption_range`, `details.savings_pct` | string: `<5%`, `5-10%`, `10-15%`, `15-20%`, `>20%`; number |
| `aws_cb_offering` | `details.start`, `details.end`, `details.duration_hours`, `details.upfront_fee` | string, string, number, number |
| `aws_odcr` | none; the record fields only | |
| `az_spot_score` | `details.score`, `details.desired_count` | string: `High`, `Medium`, `Low`; integer |
| `az_spot` | `details.eviction_rate`, `details.price`, `details.os_type` | string or null, a percent range such as `5-10`; number, USD per hour; string |
| `az_capacity_reservation` | none; the record fields only | |
| `az_vmss_flex` | none; the record fields only | |

The `aws_spot_score` row also has `details.request`, the name of the Spot score request ([spec 0008](0008-aws-probes-spec.md) section 5.2). Old rows hold this value under `details.panel`. A reader accepts both keys; a writer writes only `request`. The board reads neither key.

## 5. Probe plugin contract

### 5.1 The `Probe` class

```python
from ftg_prober.probes import Probe, ProbeContext
from ftg_prober.record import Record


class SpotScoreProbe(Probe):
    probe_type = "aws_spot_score"   # the probe_type value of the rows
    provider = "aws"                # "aws" or "azure"
    default_cadence = "5m"          # for example "5m", "15m", "2h", "24h"

    def run(self, ctx: ProbeContext) -> list[Record]:
        ec2 = ctx.clients.boto3("ec2", ctx.config["regions"][0])
        started = ctx.now()
        ...                         # 1 provider call, or 1 group of calls
        return [
            Record(
                ts_utc=ctx.timestamp(),
                run_id=ctx.run_id,
                simulated=ctx.simulate,
                provider="aws",
                region="us-east-1",
                zone=None,
                probe_type=self.probe_type,
                sku="p5.48xlarge",
                placement_group_mode="n/a",
                n_requested=1,
                n_placed=None,
                outcome="success",
                error_code=None,
                latency_ms=int((ctx.now() - started).total_seconds() * 1000),
                details={"score": 3},
            )
        ]
```

Rules:

1. A probe module lives under `ftg_prober/aws/` or `ftg_prober/azure/`, 1 module per probe. It must not edit a core file.
2. A concrete probe sets the 3 class fields as strings. A class without all 3 is a helper and is not loaded.
3. `run` returns the rows of 1 call. It returns an empty list when the call made no row. A known failure becomes a row with `outcome` = `error` and the raw code in `error_code`. An exception is allowed; the scheduler logs it and goes on (section 6).
4. A probe writes a raw object with `ctx.put_raw(probe_type, data)`. It does not write rows. It returns all rows of the call, also when they have more than 1 `simulated` value. The scheduler writes them (section 6, rule 6).
5. A probe reads its targets from `ctx.config`, the provider section of the config.
6. `probe_type` must be unique. The loader fails on a duplicate.
7. A probe that reads the `provisioning_probes` entries one by one sets `region_lanes = True`: `aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`, and `az_precheck`. The scheduler runs it in Region lanes (section 6B). In its entry loop it checks `ctx.stopping()` before each entry and before each new attempt of a search, and hands the rows of each finished entry to `ctx.emit(rows)`, which returns the rows that `run` must still return.

### 5.2 `ProbeContext`

| Member | Meaning |
|---|---|
| `config` | The provider section of the config (`aws:` or `azure:`), as a dict. |
| `provider` | `aws` or `azure`. |
| `run_id` | The UUID of this call. |
| `experiment` | The experiment tag value: the config `experiment` key (section 3). |
| `store` | The store of the run (section 9). |
| `clients` | The client factory (section 5.3). |
| `now()` | The current time, time zone aware, in UTC. Tests replace it. |
| `timestamp()` | `now()` as a `ts_utc` string. |
| `simulate` | The run/job-level `simulate` flag (ADR 0011 section 2): the run-wide `--simulate` flag, ORed with this probe type's own job `simulate: true` key. |
| `simulated(target=None)` | `simulate`, or the target's own `simulate` key when `target` is given (section 7.1). |
| `cadence(probe_type)` | The configured cadence string of a probe type, or None. |
| `put_raw(probe_type, data)` | Writes a raw object under the spec 4.8 key and returns the key. |
| `stopping()` | True once the process stops (section 6B). Always false outside a scheduler run. |
| `emit(rows)` | Hands the rows of 1 finished entry to the scheduler, which writes them at once (section 6, rule 4). Returns `[]` when it wrote them, or the rows unchanged when the context has no writer (a direct call, for example a test). |

### 5.3 `clients`

`ctx.clients.boto3(service, region)` returns a boto3 client for the service in the Region, for example `("ec2", "us-east-1")`. `ctx.clients.azure(kind)` returns 1 of 4 Azure clients:

| `kind` | Client |
|---|---|
| `credential` | `azure.identity.DefaultAzureCredential()` |
| `compute` | `azure.mgmt.compute.ComputeManagementClient(credential, subscription_id)` |
| `resourcegraph` | `azure.mgmt.resourcegraph.ResourceGraphClient(credential)` |
| `resources` | `azure.mgmt.resource.resources.ResourceManagementClient(credential, subscription_id)`, for the tag calls of the Azure provisioning teardown |

The factory makes each client once and caches it by key. The `compute`, `resourcegraph`, and `resources` clients are built with the one `credential` client. Tests pass their own factories. The factory imports an SDK inside the call, so that a missing extra fails only when a probe asks for that client. Credentials: boto3 uses its default chain, which includes the environment, the shared files, and the IAM role of the instance ([boto3 docs, credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)). `DefaultAzureCredential` tries a chain of credential types that includes the environment, a managed identity, and the Azure CLI login ([azure-identity docs, DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential); [Azure SDK for Python, credential chains](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/credential-chains)).

### 5.4 Loader

1. `run --provider aws` loads the package `ftg_prober.aws` only. `--provider azure` loads `ftg_prober.azure` only. Both packages exist in the core with an empty `__init__.py`.
2. The loader walks the package with `pkgutil.walk_packages`, which yields each module and subpackage under the path recursively ([Python docs, pkgutil](https://docs.python.org/3/library/pkgutil.html#pkgutil.walk_packages)). `walk_packages` imports each subpackage to find its modules, and without `onerror` it skips a subpackage whose import fails. The loader must pass an `onerror` that raises, so that a broken probe package stops the start. It imports each module and collects each concrete `Probe` subclass that the module defines.
3. The loader makes 1 instance of each class. The scheduler keeps the probes whose `provider` equals `--provider`.
4. A test must register a probe in a temporary package on `sys.path` and find it through the loader. A probe in a nested subpackage must be found too.

## 6. Scheduler rules

1. Each probe has a cadence in seconds: the config `cadence` entry for its key (section 3) wins over `default_cadence`.
2. At start, every selected probe is due, except a lane probe (section 5.1, rule 7: `aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`, and `az_precheck`). A lane probe runs on a UTC grid: its slots are the multiples of its cadence since the Unix epoch. At start its next due time is the first slot strictly after now, so a restart or an apply adds no real cycle. A cycle is missed (1) when the process is down at a slot, (2) for a Region whose lane of the last cycle still runs at the slot: the scheduler logs `lane.overrun` and starts no lane for that Region at that slot (section 6B; the other Regions run), (3) when the wall clock steps forward by 1 cadence or more (a step under 1 cadence only makes the fire late by the step; a step of k cadences skips about k slots), `next_due` runs on the monotonic clock and the slot index reads the wall clock, so the step moves the index ahead of the slot that was due, and (4) when the wall clock steps back by more than 1 cadence: the remembered slot is then ahead of the clock, and the lane waits until the clock passes that slot again (about as many cadences as the step). The scheduler reads the monotonic clock together with the wall clock when it computes a due time, so a wake never lands before its slot. If the wall clock stepped back during the sleep, the monotonic wake can still land before the slot on the wall clock; the fire gate then checks that the wall clock has reached the remembered slot, and if not, it sets the next due time to the monotonic clock plus the wall time that is left and skips the fire. So a fire never lands before its slot, whatever the step. A stop also truncates the cycle in progress (a lane starts no new entry). The scheduler remembers the index of the slot that it waited for. After a call, its next due time is the slot after the later of that slot and the slot that holds now (`now // cadence`, no rounding). So a late wake that is less than 1 cadence late runs the slot that was due and then waits for the slot after it, and an early wake or a step back of the wall clock never runs a slot twice. The cycles then land at the same hours each day when the cadence divides 24 h (a 2 h cadence runs at 00:00, 02:00, and so on, UTC). For a cadence that does not divide 24 h (for example 5 h), the slots are still multiples of the cadence since the Unix epoch, so the hours change from day to day; the config check does not refuse such a cadence. For any other probe, the next due time after a call is the start of the call plus its cadence. For a lane probe with the cadence c, a UTC day holds ceil(24 h / c) slots (12 for a 2 h cadence, 5 for a 5 h cadence), and a slot runs at most 1 cycle, whatever the number of restarts. When c divides 24 h, slots sit on midnight and a late fire skips the slots up to the wall clock (`grid_wait`), so a UTC day holds at most 24 h / c cycles (12 for a 2 h cadence). When c does not divide 24 h, a forward wall-clock step that makes 1 fire late can put 1 more cycle in a UTC day: at most ceil(24 h / c) + 1 (6 for a 5 h cadence). This is derived from the code, not measured. A restart that falls just before a slot can still run 2 cycles close together (the one in progress before the stop, and the one at the slot).
3. The loop runs each due probe, then sleeps until the earliest due time. A lane probe (section 5.1, rule 7) does not run on this thread: the loop starts its Region lanes and goes on (section 6B). It checks the stop flag before each sleep. The clock, the sleep, and `now()` are constructor parameters, so that a test uses a fake clock.
4. One call, 1 object per `simulated` value: right after `run` returns, the scheduler writes the rows as JSONL objects (section 4.2). The rows that a probe hands to `ctx.emit` are written right after that entry instead, as the objects of that entry (section 6B). It never batches rows across calls, and it never keeps rows in memory for a later write (spec 0002, 7.1).
5. No rows, no object. The scheduler logs `probe.empty`.
6. The scheduler groups the rows of 1 call by `simulated` and writes 1 object per group. The groups keep the order of the first row of each value. The key of every object looks the same regardless of `simulated` (ADR 0011 section 6, item 6): no object mixes a simulated row with a real one.
7. The scheduler validates each row with `validate()` and logs each fault as a warning. It still writes the row.
8. An exception from `run` or from the store is logged with `probe.failed` and the traceback. The loop goes on. One probe failure never stops the loop and never stops the other probes. The scheduler writes no row for an exception.
9. `--once` runs each selected probe 1 time and returns 0. `--probe NAME` selects the probe with `probe_type` = `NAME`. An unknown name is a config error (exit 2) and lists the loaded names.
10. Each probe call gets a new `run_id` (UUID version 4).
11. A test must use a fake clock and show: a 5-minute probe and a 15-minute probe run 6 and 2 times in 30 minutes, each call makes 1 object, and a failing probe does not stop the other.

## 6A. The call queue

A probe can return a `plan`: a list of calls (`Call` = `probe_type`, `region`, `args`, and an optional `key`). The probe then has `run_call(ctx, call)` for 1 call, and `run` makes all calls in a row. `aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest` have a plan. A probe with a plan is a queued probe. Rules:

1. Planner. When a queued probe is due (section 6, rule 2), the scheduler does not call it. It adds each call of the plan to the queue of the Region of the call. A call whose key already waits is not added again: it keeps its place, its attempts, and its not-before time. Thus the queue does not grow when a cycle does not finish, and a new cycle does not cancel a backoff. The key is (`probe_type`, `region`, `args`), unless `Call.key` is set: then it is (`probe_type`, `region`, `Call.key`), and `args` does not matter. The default (`Call.key` is `None`) fits a probe whose plan does not change between cycles. `aws_cb_longest`'s plan is a search in steps whose `args` change at each step (section 6A, rule 5), so it sets `Call.key` to the pair (instance type, instance count): the whole search then has 1 queue identity, and a new cycle's `earliest0` call does not join the queue beside the search's own next call ([spec 0008](0008-aws-probes-spec.md), section 3.3, rule 8, and section 3B.5).
2. Worker. If any probe is queued, the scheduler has a worker tick with its own due time. It wakes every `aws.queue_wake_seconds` (default 30) times a random factor, uniform in 1 - `aws.queue_jitter` to 1 + `aws.queue_jitter` (default 0.2 for 0.8 to 1.2). Each Region with waiting calls starts at a random offset of 0 to one interval. In each Region with waiting calls it takes the first calls from the queue: at most `aws.queue_max_calls_per_wake` (default 1), and at most the whole tokens of the bucket of the Region. A call takes 1 token for each request, extra pages included (`ctx.api_calls`). The bucket models the AWS bucket (10 tokens, refill 0.15 per second, monotonic clock, 1 bucket for each Region, shared by all queued probes; it starts with 5 tokens, because the real bucket can be partly drained).
3. One thread. The worker runs in the same loop as the signal probes (a lane probe runs off it, section 6B). It makes its calls in turn, so a wake delays another probe by the time of 1 call per Region. The queue does not block a 5-minute probe, because the calls are spread over the wake ticks and never run as a long loop.
4. Throttle. If a call returns 1 row with the `throttle_code` of the probe (for example `RequestLimitExceeded`), the scheduler writes no row and puts the call back at the end of its queue with a not-before time: now + uniform(0, min(`aws.queue_backoff_cap_seconds`, `aws.queue_backoff_base_seconds` x 2^attempts)), with defaults 600 and 30 (exponential backoff with full jitter). A wake skips a call that is not ready. After `aws.queue_max_throttle_attempts` (default 5) throttled attempts in 1 cycle, the scheduler writes the row of the last attempt and drops the call. Any other result follows rule 5: it is written (section 6, rules 4 to 7), unless `follow_up` adds a next call for it.
5. Follow-up. After a call, the scheduler calls `follow_up(call, rows)` and adds the calls it returns to the queue. If it returns 1 or more calls, the scheduler writes no row for this call: it was 1 step of a search, not a finished result, and a step can put whatever it needs for the next step in `rows[0].details`, because that row is never persisted. If it returns none, the scheduler writes the row as usual (section 6, rules 4 to 7). The default returns none, so an ordinary queued call still writes its row. `aws_cb_longest` ([spec 0008](0008-aws-probes-spec.md), section 3B) uses this for a search in steps: a step's row carries the next `Call` in `details["_next"]`, and `follow_up` only reads that key.
6. Rows keep the time of their call. `--once` and a simulated job do not use the queue: `--once` runs `run`, and a simulated job writes the sim rows of section 7.
7. The random source is a parameter of the scheduler (`rng`, with `uniform(a, b)`), like the clock. A test must use a fake clock, a fake sleep, and a seeded or fixed random source, and no real time. It must show: the jitter bounds, the start offsets, the backoff growth and cap, the error row after 5 throttles, the not-before time, the drain order and timing, the replace on the same key, a `Call.key` not joined by a 2nd call for the same key, the throttle put-back, the bucket limit, independent Regions, a follow-up call, and a 5-minute probe on time while the queue drains.
8. Restart. The queue and the token bucket live only in the memory of the process. A restart, from a service restart or a VM replacement, ends both: the next process starts with an empty queue, so it loses each waiting call's backoff state (its attempts and its not-before time, rule 4), and a bucket at `START_TOKENS` tokens, and plans a fresh cycle (rule 1). On SIGTERM, which a plain stop or restart sends first, the scheduler finishes the probes already due and the worker wake already in progress, and writes their rows, before it exits: `run_forever` checks the stop signal only between 1 wake's batch of work and the next, never inside it, and the signal handler itself only sets an event (`cli.py`). A restart loses a search in progress entirely: its steps wrote no row (rule 5), and the next process's plan starts it again from the first step ([spec 0008](0008-aws-probes-spec.md), section 3B). The Region lanes of the provisioning probes stop as section 6B, rule 6, says. Only a SIGKILL, which systemd sends after its stop timeout if the process has not exited by then, or a power loss with no signal at all, can end a call already in flight before it writes a row. The AWS Spot score freeze (spec 0008, section 4.4) also ends on a restart, the same as the queue: it lives for the process lifetime, not on disk, and the next process starts unfrozen. A row already written is not lost: the scheduler writes it right after its call returns, before any later call runs (section 6, rule 4).
9. The VM-side pull and restart ([ADR 0012](../decisions/0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md); AWS wiring: [spec 0004](0004-aws-stack-spec.md) sections 4.5, 4.6, 5, 6; Azure wiring: [spec 0005](0005-azure-stack-spec.md) sections 3.1, 3.4, 4.3). A code or a config change no longer replaces the VM. Each stack writes a small manifest object at a fixed key (AWS: `manifest.json` at the bucket root; Azure: `manifest.json` in the `code` container), naming the 2 current, hash-keyed code zips and the current, hash-keyed `sync.sh` itself (`{"prober_key": ..., "config_key": ..., "sync_key": ...}` on AWS; `{"prober_blob": ..., "config_blob": ..., "sync_blob": ...}` on Azure). `/opt/ftg/bin/sync.sh` (`terraform/sync.sh`, 1 file shared by both clouds) runs as root, from `ftg-prober-sync.service` on `ftg-prober-sync.timer` (every 5 minutes) and once at first boot:
   - It fetches the manifest and compares it to a cached copy at `/etc/ftg/manifest.json`. On Azure, a `BlobNotFound` counts as "unchanged, retry next tick," not an error: the manifest blob is briefly absent on every apply that writes a new one (`source_content` forces its own replace). Every other fetch error, on either cloud, is logged and retried on the next tick.
   - Self-update, every tick a manifest fetch succeeds, before anything else: it reads the manifest's own `sync_key`/`sync_blob` and compares the sha256 that name carries to the sha256 of the copy already installed at `/opt/ftg/bin/sync.sh`. A difference fetches that object, verifies its sha256 the same way as the 2 code zips below, and replaces the installed copy atomically (staged as `/opt/ftg/bin/.sync.sh.new`, then a rename, on the same filesystem), then **exits at once**, so the next tick runs the new version; this tick does not also try to install whatever the manifest's `prober`/`config` keys name. `sync.sh` itself is fetched once at first boot (AWS: a 2-step fetch in `user_data.sh.tftpl`, manifest then the named `sync_key`; Azure: the same in `cloud_init.yaml.tftpl`'s `runcmd`) and never again by anything but this self-update step: a plain reboot does not refresh it.
   - Self-update safety. A `sync_key`/`sync_blob` with no sha256 in its name cannot be verified: the tick emits `sync.self_update_failed` and exits non-zero. Before the rename, the new copy must match its sha256, start with `#!/`, and pass `bash -n`; otherwise the tick emits `sync.self_update_refused`, keeps the old copy, and (for the shebang or syntax failure) adds the hash to `/etc/ftg/sync.failed`. The old copy is kept as `/opt/ftg/bin/sync.sh.prev`, and the new hash goes to `/etc/ftg/sync.trial`. If the copy to the staging file, or the copy of the old file to `sync.sh.prev`, fails, the tick emits `sync.self_update_failed` and installs nothing. A run of the new copy that gets past self-update removes `sync.trial`. `ftg-prober-sync.service` copies `sync.trial` to `sync.trial.start` in an `ExecStartPre`, and reverts in an `ExecStopPost` (not in `ExecStart`: a start timeout kills the whole cgroup of `ExecStart`, and `ExecStopPost` still runs after it). The revert runs when `sync.trial` exists and the run did not succeed (a non-zero exit, or the timeout), or the run succeeded but `sync.trial` is still the file `ExecStartPre` copied (a new copy that exits 0 without getting past self-update never proved itself, so it counts as failed). systemd sets `EXIT_CODE` and `SERVICE_RESULT` for `ExecStopPost` ([systemd.service(5)](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#$SERVICE_RESULT,%20$EXIT_CODE,%20$EXIT_STATUS)): `EXIT_CODE` is `exited` after a normal exit and `killed` after a signal, such as a stop or a reboot. The revert therefore runs when `SERVICE_RESULT` is `timeout`, `core-dump`, `oom-kill`, or `watchdog`, when it is `signal` and `EXIT_STATUS` is not `TERM`, `INT`, or `HUP` (a `SIGKILL`, `SIGSEGV`, or `SIGABRT` is a failed trial), and for a run with `EXIT_CODE=exited` that failed or left `sync.trial` unchanged; a stop or a reboot during the trial tick ends the oneshot main process with `SIGTERM`, which systemd counts as clean only for a daemon (`is_clean_exit` in `exit-status.c`), so it reads `SERVICE_RESULT=signal`, `EXIT_CODE=killed`, `EXIT_STATUS=TERM` and reverts nothing and keeps the good copy on trial (known limit: a stop that systemd escalates to `SIGKILL` after `TimeoutStopSec` reads as a kill and reverts). It adds the hash to `sync.failed`, restores `sync.sh.prev`, removes `sync.trial`, prints `sync.self_update_reverted`, and exits 1, so the unit shows failed. When `sync.sh.prev` is missing or cannot be restored, it prints `sync.self_update_revert_failed` instead and also exits 1: it never reports a revert that did not happen. The self-update tick itself (it leaves a new `sync.trial` and exits 0) is not reverted, because `sync.trial` did not exist when it started. The unit sets `TimeoutStartSec=3600`: 2 health windows (the forward one and the rollback one, 900 s each) plus 1800 s for the downloads, the venv, and `pip install`; a hang past that fails the unit instead of stalling the timer. A hash in `sync.failed` is never installed again (the same pattern as `manifest.failed`); an operator can clear the file by hand.
   - Health window. If `health_window` does not print a whole number of seconds, or prints a mode other than `exact` or `capped`, the check fails closed with `sync.health_window_invalid` (a first-boot failure or a rollback, as for any failed check) rather than polling without end or skipping the `probe.done` check. The mode is `exact` when the raw fastest cadence is at or under the 900 s cap, and `capped` otherwise: a cadence of 840 s or 900 s gives a 900 s window and is `exact`. The check also fails when the unit's `InvocationID` is not the one it started with.
   - Stop timeout. Before each restart of `ftg-prober.service` (a forward install and a rollback), `sync.sh` computes `TimeoutStopSec` as `max(180, probe_timeout_seconds + 2 x teardown_timeout_seconds + 30)` from the provider section of the release's config (defaults 90 and 60, so 240 s): on SIGTERM every Region lane finishes only its attempt in progress, all at once, and the slowest attempt is a scale set pre-teardown, the probe, and the teardown (section 6B, rule 6), writes it to the drop-in `/etc/systemd/system/ftg-prober.service.d/stop-timeout.conf`, and runs `systemctl daemon-reload` before the restart. The restart stops the old prober, which runs with the old release's timeouts, so the drop-in written before the restart holds `max(old drop-in value, new value)` (0 when there is no drop-in); after the restart `sync.sh` writes the new value and reloads again, so a shorter new value does not SIGKILL an old teardown. A value that is not a whole number emits `sync.stop_timeout_invalid` and fails the install, like the health window. A value in the existing drop-in is read as a decimal number even with a leading zero (`0900` is 900). If the write after the restart fails, `sync.sh` emits `sync.stop_timeout_write_failed` and goes on to the health check: the larger value stays in force (in the drop-in file when the file write failed; in the memory of systemd when only the reload failed, because the file then holds the new, smaller value), and the install is not failed or rolled back for it. The value is not in the user data or the custom data, so a config change never replaces the VM.
   - Azure first boot retries the fetch of `manifest.json` and `sync.sh` up to 30 rounds, 10 seconds apart, and starts no new round after 1800 s, because `manifest.json` can briefly not exist while it is re-created. Each call of `fetch-blob.sh` makes 1 try there (`FTG_FETCH_ATTEMPTS=1`), so its own 40-try retry (15 s apart) does not nest inside the loop; every `curl` in `fetch-blob.sh` has a `--max-time`. If the loop ends without success, cloud-init shows the failure and the sync unit fails visibly on the missing `sync.sh`.
   - Fetch result. `sync.sh` takes the result of a fetch from the exit code of `fetch-blob.sh` (0 fetched, 3 `BlobNotFound`, other: error) and never from its stdout. `fetch-blob.sh` prints `fetched <blob>` on stdout on success, so `sync.sh` sends that stdout to `/dev/null`. The test fake prints the same line.
   - On a new or different manifest, it builds a release at `/opt/ftg/releases/<release ID>/` (the release ID is the sha256 of the manifest's own content), never in place: it first requires a sha256 in the name of the prober and the config key or blob (a name with none fails the manifest like a sha256 mismatch: `sync.build_failed`, `manifest.failed`), then downloads each zip to a throwaway file, verifies its sha256 against the hash in the object's own key or blob name, unzips the config zip and builds a venv with the prober package installed (`pip install`, up to 3 attempts), each at its final path, never moved. A revert to a manifest whose release directory is still present, complete, and whose files still match a stored combined hash skips the fetch and the install; a directory with no completion marker, left by an earlier interrupted sync, is removed and rebuilt.
   - The gate: `ftg-prober check-config --provider <p> /opt/ftg/releases/<release ID>/config/<config file>` (section 2), using the release's own venv, stronger than a plain `check-config`. It runs whether the release was just built or reused on a revert. A failed gate, a failed sha256 check, or a failed `pip install` leaves `/opt/ftg/current` and the running service untouched and writes the manifest's content to `/etc/ftg/manifest.failed`, so this manifest is not retried until a new deploy changes it or an operator clears the marker by hand (`rm /etc/ftg/manifest.failed`). A failed gate never deletes the release directory when it is still named by `current` or `/etc/ftg/previous-release` (for example a reused release whose gate fails on a later recheck, after an environment change outside `sync.sh`'s own control): only an unused, newly built release is removed on this failure.
   - Once the gate passes, it swaps `/opt/ftg/current` to the new release with 1 atomic symlink rename, writes the old target to `/etc/ftg/previous-release` first (unless `current` already points at this release, an interrupted sync landed a 2nd time), and restarts `ftg-prober.service`.
   - The health check polls, for the whole window derived from the config's own fastest cadence plus a margin (capped at 15 minutes), never returning early just because 1 poll happened to see every check pass: a build that looks healthy and then crashes, or whose unit's own `Restart=` policy fires, before the window ends must still fail. The window comes from the same `Scheduler` object the gate itself constructs (`cadence_s`), so a job-eligible probe type that sets no explicit `interval` still counts by its own code-level default cadence, not only the config's literal `cadence:` entries. At the end of the window: the unit is `active`, the journal has this invocation's own `run.start` line, `NRestarts` is still 0, and (when some probe's cadence fits the cap) the journal has a `probe.done` line. A pass caches the new manifest and prunes every release except `current` and `previous-release`. A failure rolls back to `previous-release` (unless it already names the release that just failed) and health-checks that restart too, over the same full window.
   - `/etc/ftg/rollback.failed`: written, with the failing manifest's content, when a rollback restart also fails health, or when there is no distinct `previous-release` to roll back to. While present, every tick exits non-zero (a failed unit) until a manifest that differs from the one it names arrives, even when that tick's own fetch fails or (Azure) returns `BlobNotFound`; an operator can also clear it by hand (`rm /etc/ftg/rollback.failed`), the same as `manifest.failed`. A differing manifest does not, on its own, clear the file or show the unit as healthy: whatever is running at that point is unconfirmed (either the unhealthy new build, or a rollback target that itself just failed once), so that tick skips the plain "manifest matches the cache, nothing to do" shortcut and runs the gate, the swap, the restart, and the full health check again, for real. `sync.sh` clears `/etc/ftg/rollback.failed` on its own only once one of these actually confirms health (a genuine new install, or a successful rollback); a benign tick that changes nothing (a download failure still under its own 1-hour limit, a sha256 mismatch, or a gate failure on an untouched release) never clears it, because nothing was re-proven healthy.
   - A zip download that keeps failing is transient only up to 1 hour, tracked by a Unix epoch at `/etc/ftg/download-failing-since`; past that limit it is treated as a bad manifest the same as a gate or a sha256 failure, and the journal event (`sync.download_failed_repeatedly`) carries the last download error as returned, not only the time stamp. `manifest.failed` is written before the time stamp file is cleared, in that order, so a crash between the 2 can never leave a manifest blacklisted with no record of why, nor a stale time stamp a later, unrelated failure could misread as its own. The time stamp is cleared at the same 4 points ADR 0012 section 4, rule 3 states: a successful download; the moment the limit trips (above); a tick whose fetched manifest matches the cache, by design, even while some other, still-failing manifest's own clock is also running (keeping the clock unkeyed to any 1 manifest is what lets 2 different bad manifests, alternating, still trip the limit together; keying it to "the manifest currently being tried" would reopen that hole); and when a new manifest replaces one `manifest.failed` or `rollback.failed` already named.
   - `sync.sh` runs as root (it writes under `/opt/ftg` and calls `systemctl`); `ftg-prober.service` keeps running unprivileged (AWS: `User=ftg`; Azure: `DynamicUser=yes`), with read and execute only on `/opt/ftg` and `/etc/ftg`, never write. It gets no `FTG_EXPERIMENT` or other experiment-name environment variable: the prober reads its experiment name from the config file only (ADR 0010, section 11), so a new `experiment:` value in a config update is an ordinary config change, never a reason to fail the gate or the health check.

## 6B. Region lanes for the provisioning probes

The provisioning probes and the Azure pre-check run off the scheduler thread, so that a provisioning call cannot delay a signal probe, and the Regions run at the same time, so that the full matrix fits the cadence ([ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)).

1. Lanes. When a lane probe is due, the scheduler starts 1 thread (a lane) per Region of the `provisioning_probes` entries (the Region compared in lower case). The lane calls `run` 1 time with the provider section in which `provisioning_probes` holds only the entries of its Region, in config order. This call has its own `run_id`, and `probe.done` has a `region` field. The loop does not wait for the lanes.
2. 1 lane at a time per Region. A lock per Region, shared by all lane probes of the process, is held for the whole lane call. Thus at most 1 entry, and 1 attempt of its search, holds quota or calls the provider in a Region at a time, as on 1 thread; the Regions run in parallel. Quotas, rate limits, and the pre-check's `n_ceiling` are per Region on both clouds, so no quota and no rate bucket sees more than before.
3. Overrun. A lane of the last call that still runs when its probe is due again is not started a second time: the scheduler logs `lane.overrun` (a warning) and keeps the cadence.
4. Write-through. The probe hands the rows of each finished entry to `ctx.emit`, and the scheduler writes them at once, 1 object per `simulated` value (section 6, rules 4 to 7). A store failure there logs `probe.failed` and the next entry still runs. A crash loses at most the entry in progress (spec 0002, 7.1).
5. Copies. Each lane has its own shallow copy of the probe, made once and kept for the process: the state that a probe keeps for 1 call (for example the adapter of an Azure probe) is never shared by 2 threads. `Clients` builds each client once, under a lock.
6. Stop. `ctx.stopping()` is true once the stop flag of `run_forever` is set, or once `run_forever` ends. Then a lane that waits for its Region lock makes no call, a probe starts no new entry (it logs `allocation.stopped` or `odcr.stopped`), and a search starts no new attempt, not even its first one, after the precheck (`details.search_stopped` = `"stop"` on the last row of a real entry only; a simulated entry gets no such key; with no attempt yet, the row is an `error` row with `details.skipped` = true and `details.reason` = `stop`, as [spec 0010](0010-azure-provisioning-probes-spec.md), section 5, says). The attempt in progress ends with its teardown and its rows are written. `run_forever` waits for every lane before it returns, so the store log handler closes after the last row. All lanes drain at once: the drain time is the slowest single attempt, at most `teardown_timeout_seconds` (a scale set pre-teardown) + `probe_timeout_seconds` + `teardown_timeout_seconds`. The stop timeout covers it (section 6A, rule 9).
7. `--once` starts the lanes of each lane probe and waits for them before it returns.
8. Cycle time. 1 Region lane runs its entries in series; a worst-case entry has the budget (1 + k) x 150 s at the default timeouts, with k = ceil(log2(`n_max` / `n`)) ([ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md), section 5): 600 s for `n` 2 and `n_max` 16, and at most 750 s at the hard limit `n_max` 16 with `n` 1. The full matrix has at most 7 Azure entries per Region, so 1 Azure cycle is at most 87.5 min, under the 2 h cadence. The AWS lane is the zone-expanded lane of the next paragraph: 1 entry is the budget for each zone, so the timeout bound of a Region is above the cadence, and the measured times decide. A test on a fake clock shows it, with real threads and a virtual clock per Region. The config check refuses a file whose worst-case lane of a Region is not shorter than `azure.cadence.az_provisioning` (default 2 h): it sums (1 + k) x (`probe_timeout_seconds` + `teardown_timeout_seconds`) over all Azure entries of the Region, of both probe types and simulated entries too, with the Azure defaults when a key is absent. A lane that equals the cadence is refused too (strict), because the `az_precheck` lane shares the Region lock and grid slot and is not counted in the sum. The same check refuses an AWS Region whose worst-case `aws_odcr` lane is not shorter than `aws.cadence.aws_odcr` (default 2 h), over the entries of `aws.provisioning_probes`, simulated entries too, 1 zone for each entry with the AWS defaults (90 s and 60 s) when a key is absent; a lane that equals the cadence is refused too (strict). What each check does not count: the Azure sum leaves out the `az_precheck` lane calls of the same Region, which run under the same Region lock (`scheduler.py`, `region_locks`). The wait for a series lock is inside the entry budget, because `run_allocation` takes `series_lock` after it sets the entry deadline (`azure/prechecks.py`), and it is free in practice under the Region lock. The AWS sum leaves out three times that take lane time but sit outside an entry budget. (1) The once-per-Region `cleanup()` before the first real entry (`aws/odcr.py`, `OdcrProbe.run` and `cleanup`) spends up to `teardown_timeout_seconds` for each stale reservation it cancels, and it can run past that by up to the 30 s cancel backoff plus its calls. (2) The poll sleep that ends after the probe window (`_create_and_poll`) overshoots by up to `poll_interval_seconds` (5 s by default) plus the next describe call. (3) A hung call. The create call is 35 s on its `max_attempts=1` client (5 s connect plus 30 s read, `awsclient.py`), but an ambiguous create is retried up to 3 times with 1 s and 2 s of backoff (about 108 s), and then `_ambiguous_create_cleanup` runs. Every other call (describe, cancel, poll, the cleanup list and its pages) uses the 3-attempt client: about 105 s for each hung call. The strict check and the 2 h default cadence leave room for these; a config at the exact edge can still overrun the slot, and then `lane.overrun` skips the next slot (rule 3). Both checks share 1 budget function, `up_steps`.
   The zones of an `aws_odcr` entry run in series inside the Region lane, under the Region lock of rule 2 (a Region has up to 6 zones; the vCPU quota is per Region, so zones in parallel would hold up to 6 times the quota of ADR 0020). The config check above counts 1 zone for each entry and does not refuse on the zones. At run time the probe adds the zones of each entry at the full budget, and logs the warning `odcr.lane_bound_exceeds_cadence` (`region`, `bound_seconds`, `cadence_seconds`) when the sum is not shorter than the cadence; it never refuses. A cycle that does run long is recorded as `lane.overrun` (rule 3). The budget is a timeout bound. Measured on the zone `a` lane of each Region over 157 cycles before the probe covered every zone: median 20 to 29 s, p95 26 to 35 s, longest 41 to 47 s of probe time and at most 185 s wall time. At 6 zones that is about 3 to 19 min, under the 2 h cadence.
9. A test must show: a lane blocked in an entry does not delay a 5-minute probe; the stop lets the entry in progress write its row and starts no new entry or lane call; 1 lane at a time per Region and all Regions at once; the overrun; a store failure of 1 entry; each entry's rows written before the next entry starts.

## 7. Simulate and the `sim` generator

ADR 0011 replaces per-target `run_mode` with `--store`, a run-wide `--simulate` flag on `run`, and a job's own `simulate` key (section 3). The 2 questions `run_mode` used to mix (where the answers come from, and where the rows go) are now separate: `--store` always says where the rows go; `simulate` says where the answers come from.

### 7.1 `simulated` per target (spec 0002, D19)

`ctx.simulated(target=None)` returns, in this order of precedence:

1. `ctx.simulate` (`True`/`False`): the run-wide `--simulate` flag, ORed with this probe type's own job `simulate: true` key (`Scheduler.job_simulated`).
2. `target["simulate"]` (`True`/`False`), when `ctx.simulate` is `False` and `target` is an `azure.provisioning_probes` or an `aws.provisioning_probes` entry with that key.

A signal probe has no per-target entry; its `simulated` value is `ctx.simulate` alone (2 does not apply).

| Case | Signal probes (`aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `az_spot_score`, `az_spot`) | `aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest` | Provisioning probes and the Azure pre-check (`aws_odcr`, `az_precheck`, `az_capacity_reservation`, `az_vmss_flex`) |
|---|---|---|---|
| Not simulated | The probe calls the provider. | The probe calls the provider. | The probe calls the provider. |
| Simulated (the job's own `simulate: true`, or the run-wide `--simulate` flag, or every job under `sim`) | The scheduler does not call the probe. It writes the synthetic rows of section 7.2 for the probe type, as the probe call would. | Same as the signal probes: the scheduler does not call the probe and writes the synthetic rows of section 7.2. | The scheduler calls the probe. The probe runs its own code against a simulated client (`SimEc2` for AWS, `SimAdapter` for Azure; spec 0002 section 4.9, the Azure provisioning probes spec of wave 2), reading `ctx.simulated(target)` in place of the earlier `ctx.run_mode(target)`. |

A probe in the core `sim` path needs no code for a simulated job. It must make no provider call when simulated.

In short: a simulated allocation job runs its real probe code, unchanged, against a fake provider client (`SimEc2`, `SimAdapter`); a simulated signal or Capacity Block job never runs the probe at all, and the `sim.py` generator writes its rows instead. Rows from both paths carry `simulated: true` (section 4.1); nothing downstream of the row can tell which path produced it.

Every row of a simulated call has `simulated: true`; its key looks exactly like a real row's key (section 4.2, rule 7). Only `simulated: false` rows feed the heat map.

### 7.2 Synthetic rows

`ftg-prober sim --hours N` writes a plausible history for every `probe_type` of section 4.1 that would run, so that a dashboard can run from `data/sim/` with no cloud. A job-eligible probe type with no job writes no synthetic rows either (ADR 0010 section 1; `Config.has_job`); a provisioning probe type and the pre-check, which are not job-eligible, are unaffected. Rules:

1. Time range: from `end` minus `--hours` hours to `end`, where `end` is now in UTC, rounded down to 5 minutes. Each probe type steps by its cadence (section 6, rule 1; defaults: `aws_cb_offering` 2h, `aws_cb_earliest` 2h, `aws_cb_longest` 2h, `aws_spot_score` 5m, `aws_spot_price` 5m, `aws_spot_advisor` 24h, `aws_odcr` 2h, `az_capacity_reservation` 2h, `az_vmss_flex` 2h, `az_spot_score` 15m, `az_spot` 5m, `az_precheck` 2h).
2. One seeded `random.Random(seed)` drives all values. The same config and seed give the same objects, byte for byte. `--seed` defaults to 42.
3. Each simulated call becomes 1 object with the key of section 4.2, like a real call. The `run_id` comes from the seeded generator.
4. Values use the native scale of each cloud. The table gives the shape of each probe type.
5. The `details` keys and types of each row are those of section 4.4, [spec 0008](0008-aws-probes-spec.md) section 5.2 for the keys of `success` rows, [spec 0009](0009-azure-spot-signals-spec.md) section 3.3, and [spec 0010](0010-azure-provisioning-probes-spec.md) section 4.1 for `az_precheck`. No row has the keys `spot_price` or `record_ts`. A unit test compares the `details` keys of each `probe_type` with a copy of the section 4.4 table, and checks the copy against the spec file. The section 4.4 table has no `az_precheck` row, because no panel reads the pre-check.
6. `sim` refuses a `--store` that is not `file://` (ADR 0011 section 2; the same rule as `run --simulate`, section 2). Of a `file://` store, it refuses a folder that holds 1 or more files or folders: it writes nothing, prints the folder and "is not empty" on stderr, and exits 2. A missing or empty folder is accepted. Give a new prefix for each run.

| `probe_type` | Calls per step | Rows per call | `details` and value rules |
|---|---|---|---|
| `aws_spot_score` | 1 | 1 per Spot score request per Region | `score` 1 to 10 (integer), `instance_types`, `request`, `label`. GPU families score low, a daily wave with the peak at 04:00 UTC, a CPU entry of fewer than 3 types stays at 3 or lower, a CPU mix of 3 or more types scores 4 or higher, and a GPU mix scores at least as high as each single type of the mix. `outcome` = `success`. The control entries have `details.control`, written by the probe's own `add_controls` (spec 0002, section 4.2), so the sim and real keys are equal. A request fails with a small chance (about 0.004%, like the live rows): then every Region of that request gets an `error` row with `error_code` = `ProxyConnectionError`, a latency of 60 to 90 s, no `score`, and `control.ok` = null for a control entry. |
| `aws_spot_price` | 1 per Region | 1 per single GPU request type per sim zone (`<region>a`, `b`, `c`, a sim choice) | About 20% of rows are `success` with `price` (number, USD per hour), `price_ts` (the step time as a `ts_utc` string), `currency` = `USD`, and `product_description` = `Linux/UNIX`. On the hourly step, a `none` row of a zone is followed by 1 `success` row with the same keys and `effective` = true (spec 0008, section 5.3). A step is hourly by the rule of the real probe, `crosses_hour(step - cadence, step)`, so a cadence that does not divide an hour (for example 45 min) gives the same steps as a real run. A call fails with a small chance (the live rows had none; the sim uses about 0.004%): then it has 1 `error` row per type, with no zone, `details` = `{}`, `error_code` = `ProxyConnectionError`, and a latency of 60 to 90 s. The rest are `none` with `effective_price` (number) and `effective_price_ts` (a `ts_utc` string, 3 hours before the step), like a real `none` row that has a zone. |
| `aws_spot_advisor` | 1 | 1 per single GPU request type per Region | A raw JSON feed object first (section 4.2, rule 5). Rows: `interruption_range` (`<5%`, `5-10%`, `10-15%`, `15-20%`, `>20%`), `interruption_range_min_pct` and `interruption_range_max_pct` (the bounds of the range; the max of `>20%` is null), `feed_max_pct` (5, 11, 16, 22, or 100, like the public feed), `savings_pct`, and `range_index` (0 to 4). |
| `aws_cb_offering` | 1 per type, instance count, and duration per Region (counts from `instance_counts[type]`, else `instance_count`, like the probe) | 0 to 3 offerings, else 1 `none` row | `start`, `end`, `duration_hours`, `duration_minutes` (= 0, like a real row; null only when AWS omits it), `instance_count`, `upfront_fee`, `currency`, `offering_id` (a stable sim id, `cb-sim-` and 16 hex digits of a SHA-256, different for each offering: it hashes type, Region, zone, start, duration, and count), `tenancy` (= `default`), `zone_type`, `ultraserver_type`, `ultraserver_count` (the last 3 are null). A `none` row has `details` = `{duration_hours, start_window_days}`. The sim and real rows have equal `details` keys and equal value types (null, number, string, bool) for `success` and `none` (a unit test checks it). `n_requested` = `instance_count`; `n_placed` is null, like a real offering row. |
| `aws_cb_earliest` | 1 per type, instance count, and earliest duration per Region | 0 or 1 offering, else 1 `none` row | As `aws_cb_offering`, but `start` is the first start AWS allows (step time + 30 minutes) or the next 11:30 UTC. A `none` row has `details` = `{duration_hours}`. [Spec 0008](0008-aws-probes-spec.md) section 3A. |
| `aws_cb_longest` | 1 per type and instance count per Region | 1 row per search: the first free day and the longest block there, or `none`, or `error` | A search in steps on the call queue (section 6A). `details` has the earliest and longest offer fields, `steps`, `calls`, and `planned_at`; no derived value. `n_placed` is null, like a real row. [Spec 0008](0008-aws-probes-spec.md) section 3B. |
| `aws_odcr` | 1 per `provisioning_probes` entry | 1 | `outcome` from `success` 55%, `no_capacity` 20%, `quota` 10%, `timeout` 10%, `error` 5% (ADR 0009: no `partial`, all or nothing). `n_requested` = `n`. `n_placed` = `n` on `success`, 0 on `no_capacity`, else null. `error_code` = `InsufficientInstanceCapacity` on `no_capacity`, `InstanceLimitExceeded` on `quota`. `placement_group_mode` = `n/a`. |
| `az_spot_score` | 1 per group of 5 sizes and 8 regions (regional), then 1 per Region and group of 5 sizes (zonal) | A regional call: 1 per size per region. A zonal call: 1 per size, Region, and zone (`1`, `2`, `3`), or 1 per size with `zone` = null in a Region without Availability Zones (`northcentralus`, `westus`, `westcentralus`; [list of regions](https://learn.microsoft.com/en-us/azure/reliability/regions-list)) | `score` = `High`, `Medium`, or `Low` with `outcome` = `success`; some H100 and H200 size and region pairs give `RestrictedSkuNotAvailable` with `restricted`; about 2% give `DataNotFoundOrStale` with `none`. `desired_count` = 1, `is_quota_available` = false on every `none` row and on about 85% of the other rows (the live share is 86%), `scope` = `regional` in a regional call and `zonal` in a zonal call. A `restricted` row has `error_code` = the score value, like a real row. |
| `az_spot` | 1 | 1 per size per region | `eviction_rate`, 1 of the sim buckets `0-5`, `5-10`, `10-15`, `15-20`, `20+` (a sim choice), `eviction_rate_min_pct` and `eviction_rate_max_pct` (the bounds of the bucket; the max of `20+` is null; about 22% of rows have an unknown bucket, and then all three are null), `price` (number, USD per hour), `price_effective_date` (midnight UTC of the step day, a string), `currency` = `USD`, `os_type` = `linux`, `eviction_error_code` and `price_error_code` (both null). `outcome` = `success`. A Region with no Spot data (`westcentralus`) gives `none` rows: `eviction_rate` and its bounds are null; `price` and `price_effective_date` are null, but in about 20% of the rows they hold an old price (`azure.spot_price_max_age_days` + 110 days old, 650 at the default of 540, like the live rows; the real probe writes `none` with a price only when the price is older than that limit). A call fails with a small chance (about 0.08%, like the live rows): then every row is `error` with `error_code` = `eviction_error_code` = `ServiceResponseError`, and the eviction rate and its bounds are null. |
| `az_capacity_reservation` | 1 per `az_capacity_reservation` entry | 1 | `outcome` from `success` 60%, `partial` 15%, `no_capacity` 15%, `timeout` 5%, `error` 5%. `n_requested` = `n`. `n_placed` = `n`, 1 to `n - 1`, 0, or null. `error_code` = `AllocationFailed` on `no_capacity`. `placement_group_mode` = `n/a`. Superseded by [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md), not implemented yet: a provisioning probe must not write `partial`, and a reservation is all or nothing. |
| `az_vmss_flex` | 1 per `az_vmss_flex` entry | 1 | As above, with the entry's `placement_group_mode`. |
| `az_precheck` | 1 | 1 per `provisioning_probes` entry, in config order | `outcome` = `success` about 95%, else `restricted` with `error_code` = `NotAvailableForSubscription`. `placement_group_mode` and `n_requested` from the entry; `n_placed` = null. `details` as the sim pre-check of spec 0010 section 9, rule 5: `entry_probe_type` (the probe type that lists the entry), `n_ceiling` (null on `restricted`), `capacity_reservation_supported` (true only for an `az_capacity_reservation` entry), `family` = `simFamily`, `vcpus` = 2, `sku_zones` (the entry zone, else `1`, `2`, and `3`), `restrictions` (empty on `success`; on `restricted` 1 `Location` restriction with `reason_code` = `NotAvailableForSubscription` and the entry region in `values` and `locations`), and on `success` `family_current_vcpus` (an even number from 0 to 100), `family_limit_vcpus` = 1000, `total_current_vcpus` = 0, `total_limit_vcpus` = 1000, `family_available_vcpus` (1000 minus `family_current_vcpus`), and `total_available_vcpus` = 1000. |

Simplifications of the sim that remain (a real run differs): the `cpu_mix_min` control is always `ok` (live rows have `ok` false in about 8%); `az_spot_score` writes no `error` rows; its `restricted` share is lower than the live share; and it writes no zone `unknown` or `4`.

More rules:

- The scheduler uses the same generator for a signal probe, `aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest` when simulated (section 7.1), with `random.Random(run_id)` as the seed.
- The repo config with `--hours 24` must give more than 1000 objects and every probe type, all 3 AWS Regions, all 9 Azure regions, and every request list SKU.

### 7.3 Provider response fixtures

`ftg-prober sim` and section 7.2 cover the demo path. They never call the real signal probe
code, so a change in a provider's response shape can slip past the unit tests. A separate set
of fixtures covers this gap, for the signal probes and `aws_cb_offering`, `aws_cb_earliest`,
and `aws_cb_longest`: a fixture per probe type and provider call, with a fake client that
returns it and a test that runs the real probe code (not `sim`) against the fake and checks the
parsed rows: fields, units, outcome, and `error_code`.

1. A fixture is, in order of preference:
   - (a) a response this project already stores, trimmed to the fields the probe reads: the raw
     Spot Advisor feed (`*_raw_*.json`, section 7.1), read by `aws_spot_advisor`'s test from
     `prober/tests/aws/fixtures/spot_advisor_feed.json`; or a Resource Graph page recorded from a
     live run (not a raw object this project stores: `az_spot` makes no `put_raw` call), read by
     `az_spot`'s test from `prober/tests/azure/fixtures/az_spot_eviction_response.json` and
     `az_spot_price_response.json`.
   - (b) where no response of (a) exists (`aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest`,
     `aws_spot_score`, `aws_spot_price`, `az_spot_score` call no `put_raw`, and no live Resource
     Graph recording exists for them), a response built in the shape the provider's own API
     reference documents, grounded in the values this project's own stored rows already show,
     under `prober/tests/fixtures/provider/<aws|azure>/`. The test cites the reference page in a
     comment, and says which values are grounded and which are illustrative.
   - (c) the provider's own published example response, unchanged, when the reference page gives
     one: `az_spot_score`'s fixture is the Azure REST API reference's own example response for
     `generate`, under `prober/tests/fixtures/provider/azure/`.
2. A fixture holds no account ID, subscription ID, tenant ID, request ID, or other private
   value ([CONTRIBUTING.md](../CONTRIBUTING.md)). An offering or request ID becomes a placeholder such as
   `cb-0123456789abcdef0`.
3. The test builds the fake client's answer from the fixture (parsing a timestamp string to
   `datetime` where the SDK gives one) and calls the probe's `run`, `run_call`, or a module-level
   parsing function directly: whichever reaches the real parsing code with the least extra
   state. `aws_cb_longest` runs 1 step of its search this way, not the whole search.
4. These fixtures sit beside the `sim` generator; neither replaces the other. `sim` keeps
   writing demo data for the mode of section 7.1; a fixture test opens no store and makes no
   provider call.

## 8. Logging

### 8.1 Stdout

1. Each event is 1 line on stdout: `<UTC time>Z <LEVEL> <logger> <event> key=value ...`. New lines inside a message become spaces. A value with a space is quoted.
2. Events: `probe.loaded`, `run.start` (with `provider`, `store`, `probes`, `once`, `simulate`, `experiment`, and `release`: the release ID, the sha256 of the manifest that `sync.sh` installed, read from the real path of `--config`, whose folder `/opt/ftg/releases/<release ID>/` names it; `none` when the config is not under a `releases` folder, for example in a local run), `probe.done` (with `rows` and `ms`), `probe.empty`, `probe.failed`, `store.put` (with `key`, `rows`, `bytes`), `row.invalid`, `run.signal`, `scheduler.stopped`, `lane.overrun`, `allocation.stopped`, `odcr.stopped`, `sim.start`, `sim.wrote`, `sim.done`, `provider.error`.
3. `--log-level` sets the level. The default is `INFO`.
4. The lines go to stdout, so that `journalctl -u ftg-prober` shows them on a VM. They also go to the store, through the second handler of section 8.2, so the process's own log survives a VM replacement (research: [results and logs as 2 streams](../research/results-and-logs-streams.md)).
5. The `azure` logger and its children (`azure.identity`, `azure.core`) must stay at `WARNING`, so that their INFO lines for each token request and HTTP call do not hide the prober events. A `--log-level` of `DEBUG` must restore them: they then follow the root level. This `WARNING` is a level setting, not a content filter: a record the `azure` logger emits at its level reaches the store unchanged (item 6, section 8.2).
6. A failed provider call logs its error message as the provider returned it, and a row never holds it: logs are logs, metrics are metrics. The line is 1 warning, `provider.error` (a probe may keep its own event name, for example `az_spot_score.failed`), with the fields `probe_type`, `region`, `code`, `status` (the HTTP status, else empty), `message`, and, where it helps, `call`. The message is unfiltered and unredacted: it is not filtered, redacted, shortened, or rewritten, and the same holds for every other log record, from our code and from the SDK loggers; only the line format of item 1 applies, and the byte caps of section 8.2 cut length only. The prober uses no static credentials, so a log has no credential to leak (a new line becomes a space, a value with a space is quoted). The store handler (section 8.2) writes the line to the store, so the message is kept apart from the rows. The helper is `ftg_prober.log.log_provider_error`.

### 8.2 The store handler

A row (stream 1) and a log line at any level (stream 2) are 2 different OpenTelemetry signals, not 2 severities of 1 stream (research: [results and logs as 2 streams](../research/results-and-logs-streams.md)). The rule: the prober and the sweeper write their own log to the store, apart from the rows, never through a VM-side journald export, CloudWatch Logs, or Log Analytics.

1. A second handler (`ftg_prober.log.StoreLogHandler`), next to the stdout handler of section 8.1, buffers every record of every logger (item 4) — the same level gate, so `--log-level` controls both destinations at once — as 1 JSON object mapped to the OpenTelemetry log data model ([OpenTelemetry, Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)): `Timestamp` (the record's UTC time, the same style as a row's `ts_utc`, section 4.1), `SeverityText` (the level name), `Body` (the formatted message; when the record carries an exception, for example a `log.exception` call, its formatted traceback is appended, the same text the stdout handler's `_LineFormatter` keeps, collapsed to 1 line — a provider error's text is written exactly as returned, in full, never only the `kv()` summary), and `Attributes` (an object: `logger`, the logger name, plus every other `kv()` field of the message, unwrapped from `kv()`'s `repr()`-quoting with `ast.literal_eval`). It lifts `run_id` out of `Attributes` into its own correlation field (`null` when the event carries none), alongside `experiment`, `provider`, and `host`.
2. Key: `experiment=<name>/logs/provider=<p>/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl` (`ftg_prober.store.logs_key`). The second path segment is the literal `logs`, not `provider=`, so the row glob of spec 0006 section 4.1 (`experiment=*/provider=*/probe_type=*/date=*/*.jsonl`) never matches a logs object.
3. The buffer flushes as 1 `Store.put` call every 60 s and once more when the process is done: `run --once` returns, a caught `SIGINT`/`SIGTERM` stops `run --forever`, or `cmd_run` exits for any other reason. A failed flush writes 1 stdout line and keeps the buffered records for the next flush, dropping the oldest first once the buffer passes 10,000 records or 8 MiB (the UTF-8 bytes of the serialized records, each with its newline), so a store that stays down cannot grow it without end, even when a long traceback or provider message makes the records large. A single record may take at most `min(cap / 64, 64 KiB)`, which is 64 KiB at the 8 MiB default. A longer record first loses the tail of each long string field (every Attribute value, which includes the logger name, and `run_id`) beyond 256 characters; if it is still too long (many `key=value` pairs, or one huge key), its Attributes shrink to the logger name alone; if it is still too long, `experiment`, `provider`, and `host` lose their tail beyond 256 characters; only then does it lose the tail of its `Body`, cut so that the JSON-escaped `Body` fits (a character that escapes to several bytes does not make the cut larger than needed); each cut ends with `...[truncated]`. So 1 huge message or traceback cannot evict the older records or itself. 8 MiB is about 2.5 times the size of 10,000 normal records (a few hundred bytes each), and it is 1 small `PutObject`. The flush thread and the scheduler thread both create SDK clients, so no code path creates a boto3 client from the shared default session, which boto3 documents as not thread-safe ([boto3 clients, multithreading](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/clients.html#multithreading-or-multiprocessing-with-clients)): `boto3_client` builds each client from its own `boto3.session.Session()`, and the lazy S3 and Blob store clients are built once, under a lock. Parsing the `key=value` pairs takes time linear in the message size, so a long word cannot delay the caller, and a message longer than 1 MiB is not parsed at all (its Attributes are the logger name only and its `run_id` is null), so a huge message with many pairs does not delay the caller either. `emit` only formats a record and appends it to the buffer; the write itself happens on a background thread, so a slow or failing store never delays a due probe, and the handler never raises into the scheduler loop.
4. The handler takes every record of every logger, ours and the SDK loggers (`botocore`, `boto3`, `s3transfer`, `urllib3`, `azure`), at the configured level. The configured level is the only gate: there is no logger-name filter, and a message is never filtered, redacted, or rewritten. The byte caps of item 3 cut length only. Reason: the prober and the sweepers use no static credentials (an instance role on AWS, a managed identity on Azure), so a log has no credential to leak, and logs are never filtered. The stdout handler is unchanged. The handler's lock is reentrant, so a signal handler that logs while `emit` holds it cannot deadlock.
5. The final flush at shutdown has a deadline of 10 s in total, so that a hung store cannot hold the stop past the unit's `TimeoutStopSec`. Records still buffered at that deadline are lost; the handler prints their count to stdout.
6. `cmd_run` attaches the handler once the store is open, for both a normal run and `run --simulate`: a `--simulate` run writes its logs to the local store, like its rows (section 7). `cmd_sim` (the bulk historical generator, section 7.2) does not attach it; it is not a live process whose own log needs a stream.
7. The sweeper gets the same rule, in `sweeper/azure/sweeper/log_store.py` (spec 0007 section 9): its own small copy of this handler, because the sweeper is a separate deployable unit with its own dependency set. Its Function App timer already runs every 60 s (spec 0007 section 3), so it flushes once per invocation instead of keeping a background thread. Its log is its only write under this rule: it has no row prefix of its own. The AWS sweeper has its own copy too, `sweeper/aws/sweeper/log_store.py`, with 1 S3 `PutObject` in place of the Blob upload, flushed once per Lambda invocation ([spec 0013](0013-aws-sweeper-spec.md) section 9).

## 9. Storage backends

`Store.put(key: str, data: bytes) -> None` is the 1 interface. `open_store(url)` makes the backend from the URL. Tests pass a fake client with `client=`.

| URL | Backend | Write | No-overwrite rule |
|---|---|---|---|
| `file:///abs/path` | File | Makes the folders of the key and writes the file. | Opens the file with mode `xb`. Mode `x` is exclusive creation and fails when the file exists ([Python docs, open](https://docs.python.org/3/library/functions.html#open)). |
| `s3://bucket[/prefix]` | S3 | `client.put_object(Bucket=bucket, Key="<prefix>/<key>", Body=data, IfNoneMatch="*")` | S3 rejects a `PutObject` with `If-None-Match: *` when the key exists, with HTTP 412 ([Amazon S3 docs, conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html); [boto3 docs, put_object](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)). A delete at the same time can give 409 instead. |
| `az://<account>/<container>[/prefix]` | Blob | `container.upload_blob(name="<prefix>/<key>", data=data, overwrite=False)` | `upload_blob` raises `ResourceExistsError` when the blob exists and `overwrite` is False, the default ([azure-storage-blob docs, ContainerClient](https://learn.microsoft.com/en-us/python/api/azure-storage-blob/azure.storage.blob.containerclient)). |

Rules:

1. The S3 client is `boto3.client("s3")`. The Blob client is `ContainerClient(account_url="https://<account>.blob.core.windows.net", container_name="<container>", credential=DefaultAzureCredential())`.
2. The backend imports its SDK inside the first `put`, so that `file://` works without any extra.
3. A `file://` URL must have an absolute path. A bad URL is a config error (exit 2).
4. Tests cover the key format, the `file://` backend with its no-overwrite rule, and the S3 and Blob backends with fakes that record the call.
5. `Store.put` is the write path of the raw row objects and of the prober's log objects. The 2 sweepers write their own log objects with their own code: the AWS sweeper with `put_object` and no `IfNoneMatch` (`sweeper/aws/sweeper/log_store.py`), the Azure sweeper with `upload_blob(overwrite=False)` (`sweeper/azure/sweeper/log_store.py`); each log key holds a new UUID. The compaction job does not use `Store.put`; it writes only the daily files of section 4.3 (section 9B, item 5).

## 9A. Data move runbook (ADR 0010 section 5, item 6)

A store written before this change holds objects under the old `run_mode=<m>/provider=<p>/probe_type=<t>/date=<d>/<file>` key (section 4.2). The move to the new `experiment=<name>/...` key is a one-time rename of a key prefix: exactly what each cloud's own copy tool already does server-side, so this project does not maintain its own copier for it. No code change to the dashboard or the sweeper is needed first: both already read the old layout too, by the old-row reader rule (section 4.1, rule 3), and the board's reader dedupes a row that briefly exists under both keys during the move (spec 0006, section 5.1). Nothing below deletes anything; the delete of the old prefix is a separate, later, deliberate step.

A store can hold more than 1 distinct old `run_mode=<m>/` value (`cloud-gpu`, `cloud-cpu`, `local`, from before this change); run the move once for each value that holds objects. `run_mode=local/` holds simulated rows: copy it too, under the same new experiment name, rather than leaving it behind. The old-row reader rule marks a `run_mode=local` row `simulated: true` regardless of which `experiment=` prefix it lands under (section 4.1, rule 3), so moving it changes nothing the dashboard shows; leaving it in place only means the delete step (below) cannot remove the whole old prefix in 1 pass.

**AWS (S3), once per old `run_mode=<value>/` that holds objects:**

```text
aws s3 sync s3://<bucket>/run_mode=<value>/ s3://<bucket>/experiment=<name>/
```

`aws s3 sync` copies S3 to S3 with the `CopyObject` API: its own example for this case describes the transfer as "copying S3 objects," with `copy:` output lines, backed by the `CopyObject` API ([AWS CLI reference, `aws s3 sync`](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)). It recurses by default and keeps each source key's suffix under the destination prefix, the same as `aws s3 cp --recursive` for this case. It copies only what is missing or changed, and is safe to run again: for an S3-to-S3 sync, the same reference states "An S3 object will require copying if the sizes of the two S3 objects differ, the last modified time of the source is newer than the last modified time of the destination, or the S3 object does not exist under the specified bucket and prefix destination" — this is a size-and-time comparison, not a hash, so a 2nd run proves only that no source object looks newer or differently sized than its already-copied destination twin, not that the bytes are bit-for-bit identical (the `--size-only` and `--exact-timestamps` flags change the exact rule; neither is the default).

**Check 1, the dry run:** run the same command again with `--dryrun` ("Displays the operations that would be performed using the specified command without actually running them," [same reference](https://docs.aws.amazon.com/cli/latest/reference/s3/sync.html)). It must print nothing: any `copy:` line means the first run left work undone.

**Azure (Blob), once per old `run_mode=<value>/` that holds objects:**

```text
azcopy sync 'https://<acct>.blob.core.windows.net/data/run_mode=<value>/' 'https://<acct>.blob.core.windows.net/data/experiment=<name>/' --recursive
```

`azcopy sync` compares the 2 sides and transfers only what is missing or changed, and skips the rest; by default it compares last-modified times, skipping a file when the destination's is more recent, and copies Blob to Blob service to service ([AzCopy `sync` reference](https://github.com/Azure/azure-storage-azcopy/wiki/azcopy_sync)). The `--compare-hash=MD5` flag switches the comparison to an MD5 hash instead of timestamps, when a byte-level check matters more than a timestamp check; this repo pins no `azcopy` version (`mise.toml` has no `azcopy` tool), so check `azcopy --version` and the reference page above for whether the installed build documents `--compare-hash` before relying on it. Authenticate by reusing an existing `az login` session (`export AZCOPY_AUTO_LOGIN_TYPE=AZCLI` and `export AZCOPY_TENANT_ID=<tenant-id>`) or with `azcopy login` ([Authorize access for AzCopy with a user identity](https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azcopy-authorize-user-identity)); the identity needs `Storage Blob Data Contributor` on the `data` container (covers both reading the old prefix and writing the new one).

**Check 1, the dry run:** run the same `azcopy sync` command again with `--dry-run` ("Prints the path of files that would be copied or removed by the sync command. This flag does not copy or remove the actual files.", [same reference](https://github.com/Azure/azure-storage-azcopy/wiki/azcopy_sync)). It must list nothing: any listed file means the first run left work undone.

**Check 2, every old object has its copy (both clouds, independent of check 1):** for each moved `run_mode=<value>/`, list keys and sizes under that old prefix and under the new prefix, strip the prefix from each name, and sort both lists. Every line of the old list must be in the new list: `comm -23 old.txt new.txt` must print nothing. The old list must not be empty for a `<value>` that is being moved: an empty old list means the prefix is wrong, not that the move is done. The new list holds more lines than the old one, because the prober keeps writing new rows under `experiment=<name>/` and because more than 1 old prefix moves into it, so a plain diff of the 2 lists is never empty and is not the check.

```text
aws s3api list-objects-v2 --bucket <bucket> --prefix run_mode=<value>/ --query 'Contents[].{k:Key,s:Size}'
aws s3api list-objects-v2 --bucket <bucket> --prefix experiment=<name>/ --query 'Contents[].{k:Key,s:Size}'
```

(`--bucket` and `--prefix`, and the `Key`/`Size` fields of each entry, are documented on [the `aws s3api list-objects-v2` reference](https://docs.aws.amazon.com/cli/latest/reference/s3api/list-objects-v2.html); the command paginates automatically past 1,000 keys.)

```text
az storage blob list --auth-mode login --num-results "*" --account-name <acct> --container-name data --prefix "run_mode=<value>/" --query "[].{n:name,s:properties.contentLength}"
az storage blob list --auth-mode login --num-results "*" --account-name <acct> --container-name data --prefix "experiment=<name>/" --query "[].{n:name,s:properties.contentLength}"
```

(`--container-name`, `--prefix`, and `--auth-mode login` are documented on [the `az storage blob list` reference](https://learn.microsoft.com/en-us/cli/azure/storage/blob#az-storage-blob-list); `--auth-mode login` uses the signed-in identity rather than an account key. `az storage blob list` returns at most 5,000 blobs unless `--num-results "*"` is given, so both commands give it; a `Next Marker:` line on stderr means the list is cut short and the check is not valid. The exact JSON field name for a blob's size is not stated on that page; confirm it against the command's own output, or the List Blobs REST response, before scripting the diff.)

**The operator steps, for a live store:**

1. The prober can keep running: every new row already lands under `experiment=<name>/` (section 4.2); nothing above needs the prober stopped.
2. Run the sync command (AWS or Azure, above) for each old `run_mode=<value>/` that holds objects.
3. Check 1 (the dry run or 2nd run) and check 2 (the listing diff) must both pass before the next step.
4. A person runs the delete of the old `run_mode=*/` prefix, and only after both checks pass: `aws s3 rm --recursive s3://<bucket>/run_mode=<value>/`, or `az storage blob delete-batch --source data --pattern "run_mode=<value>/*" --account-name <acct> --auth-mode login` (add `--dryrun` first, to preview; [the `az storage blob delete-batch` reference](https://learn.microsoft.com/en-us/cli/azure/storage/blob#az-storage-blob-delete-batch) documents `--source` as the container, `--pattern` for the key glob, and `--dryrun` for the preview). A destructive step stays a deliberate, separate action, run by hand, never by a script this repo ships.

The move of ADR 0010 is done. This runbook stays for a later move. When a later move needs Make targets, they are in this repo. They wrap steps 2 and 3 above around the same CLI commands, and they read the bucket and account names from the deploy outputs ([spec 0014](0014-config-and-deploy-spec.md) section 4). A step 4 delete target refuses to run without `CONFIRM=yes` on the command line and a passing check, and a person runs it by hand, never a script or an agent.

## 9B. Daily compaction

`ftg-prober compact` builds the daily files of section 4.3 ([ADR 0025](../decisions/0025-daily-compaction-runs-on-the-prober-vm.md)). It runs on the prober VM from `ftg-compact.timer`, with the prober's venv and identity ([spec 0004](0004-aws-stack-spec.md) section 6, [spec 0005](0005-azure-stack-spec.md) section 3.4). The core (`prober/ftg_prober/compact.py`) packs a day and builds the manifest; the job lists, reads, calls the core, and writes.

1. **Scope.** The experiment of the config file and the provider of `--provider`.
2. **Closed day.** A day `D` is closed 30 minutes after `D + 1` at 00:00 UTC. Measured on 2026-10-27: a row object's `LastModified` was at most 13 s (AWS) and 3 s (Azure) after the `ts` of its key.
3. **What to build.** The job lists the raw rows and the daily files. A closed day and probe type is built when it has no manifest, or when `objects_sha256_from_listing` of its listed raw objects differs from the manifest's `objects_sha256` (a late object). Otherwise nothing is written. The first run builds every existing day.
4. **Order.** Read the raw objects, call the core, write the daily file, read it back and check it against the manifest (`compact.verify`), then write the manifest last. `compact.verify` recomputes `row_count`, `rows_bytes`, `rows_sha256`, `unparsed_row_count`, `first_ts_utc`, and `last_ts_utc` from the decompressed rows, checks `file_bytes` and `file_sha256`, requires the exact JSON type (an int field rejects `60.0` and `true`), and raises `CompactVerifyError` for a manifest that is not an object. It only reads and compares; a failure removes and rewrites nothing. `objects_sha256` writes each name with `\`, tab, and newline escaped as `\\`, `\t`, `\n`; a name without them (every prober key) gives the same digest as before, so no existing manifest changes. A failed day gets no manifest, is logged as `compact.day_failed` with the error as returned, and is tried again on the next run; the job goes on with the next day and exits 1.
5. **Writes and no removal.** The job writes only the 2 keys of section 4.3 for the configured experiment; one write function refuses any key outside `experiment=<name>/daily/v1/`, and a test proves it. No prober or job code deletes store data or calls a lifecycle, retention, versioning, or tier API; a test scans `prober/ftg_prober/` for such calls. The only rule that removes store objects is the existing `expire-logs` lifecycle rule (90 days, logs only, section 8.2), owned by Terraform and unchanged.
6. **Memory.** 1 day and probe type at a time. The peak of `compact_day` on the largest real day and probe type, with boto3 loaded, measured as macOS RSS, was about 190 MiB on AWS (`aws_spot_price` of 2026-10-19, 11.2 MB raw) and 179 MiB on Azure (same method). The full job on the VM is not measured yet. The unit sets `MemoryMax=384M`.
7. **Logs.** The store log handler of section 8.2, with the events `compact.start`, `compact.day_built`, `compact.day_failed`, and `compact.done`.

## 10. Package and Make targets

`prober/pyproject.toml`:

| Item | Value |
|---|---|
| name, version | `ftg-prober`, `0.1.0` |
| `requires-python` | `>=3.12` |
| dependencies | `pyyaml`, `zstandard==0.25.0` (BSD-3-Clause; daily compaction, `ftg_prober/compact.py`; Python 3.12 has no stdlib zstd) |
| extras | `aws`: `boto3`, `requests`. `azure`: `azure-identity`, `azure-mgmt-compute`, `azure-mgmt-resource`, `azure-mgmt-resourcegraph`, `azure-storage-blob`, `requests`. |
| dev group | `pytest`, `ruff`, pinned to 1 version each |
| script | `ftg-prober = "ftg_prober.cli:main"` |
| layout | Flat: `prober/ftg_prober/`, `prober/tests/`. `uv.lock` is committed. |

`tasks/Makefile.prober` ([ADR 0002](../decisions/0002-makefiles-follow-cloud-posse-conventions.md)): `prober/test` (pytest), `prober/lint` (`ruff check` and `ruff format --check`), `prober/format`, `prober/lock`, `prober/check-config`, and `prober/sim`. `prober/sim` writes `PROBER_SIM_HOURS` hours (default 24) to `PROBER_SIM_DIR` (default `data/sim`). The folder must be missing or empty (section 7.2, rule 6). Each target runs `$(MISE_EXEC) uv --directory prober ...`.

## 11. Decisions

Decisions of this spec that an earlier build plan did not state.

| # | Decision | Reason |
|---|---|---|
| P1 | `ts_utc` has milliseconds. | Signal rows of 1 call are close in time; seconds alone would collide. |
| P2 | `run_id` is 1 UUID per probe call, shared by its rows. | Spec 0002 says "unique per cycle"; a call is the smallest write unit (7.1). |
| P3 | `target` in the key is `<sku slug>-<region slug>`, with `multi` for a part that differs between the rows. | One call can cover many SKUs and Regions; the key stays readable and `[a-z0-9-]`. |
| P4 | The scheduler writes the rows. A probe only returns them, and writes a raw object itself with `put_raw`. | Keeps the write-through rule in 1 place. |
| P5 | When simulated, the scheduler replaces a signal probe, `aws_cb_offering`, `aws_cb_earliest`, and `aws_cb_longest` with the core generator. An Azure or AWS provisioning probe runs its own fault injection against a simulated client. | A wave 2 signal probe needs no sim code. Provisioning probe faults are specific to each cloud's path. |
| P6 | The scheduler writes no row for an exception. | A probe that knows the failure writes an `error` row. An unknown exception has no clean `sku` and `region`. |
| P7 | `cadence` accepts any key; the lookup uses `probe_type`, except `az_provisioning` for the pre-check and both provisioning probes. | An earlier build plan used a different cadence key for the same purpose. A strict key list would block a new probe type. |
| P8 | `--hours` is required for `sim`; `--seed` defaults to 42. | An earlier build plan showed `--hours N`. A default seed makes `make prober/sim` repeatable. |
| P9 | `sku` of a multi-type request is the types joined with a comma. | Spec 0002 4.2 says the field holds the type set for a mix entry. |
| P10 | Removed (ADR 0011): `FTG_EXPERIMENT` no longer exists; the config `experiment` is the only source. | The experiment file is the only source of what a run does; nothing outside it can change it. |
| P11 | The run loop waits on a `threading.Event`, not `time.sleep`. | Section 2.1. |
| P12 | Implementer's choice: the exact plausible-value functions in section 7.2 (base scores, prices, waves). | Only the shape, the scale, and the determinism are requirements. |
| P13 | Closed: the Azure pre-check (spec 0002, 4.3) has `probe_type` `az_precheck` and the cadence key `az_provisioning`. | [spec 0010](0010-azure-provisioning-probes-spec.md), decision Z1. |
| P14 | A second `sim` into a non-empty `file://` folder exits 2. `sim` does not make unique keys. | The same seed and end time give the same keys. Unique keys would mix 2 histories in 1 folder, and the dashboard would show both. |
| P15 | The `aws_spot_price` sim does not write the hourly effective-price row of spec 0008 section 5.3. | About 20% of polls change the price, so most hours have a `success` row. The old dashboard carried the price over the other hours (that rule is gone; the board takes the daily mean, spec 0006 section 5.4 item 5). |
| P16 | The scheduler splits the rows of 1 call into 1 object per `simulated` value (ADR 0011). A probe returns all rows. | One allocation call covers entries of different `simulate` values. This replaces the workaround of spec 0010 decision Z7. |

## 12. Sources

- [Python docs, time.sleep](https://docs.python.org/3/library/time.html#time.sleep)
- [Python docs, pkgutil.walk_packages](https://docs.python.org/3/library/pkgutil.html#pkgutil.walk_packages)
- [Python docs, open](https://docs.python.org/3/library/functions.html#open)
- [boto3 docs, credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)
- [boto3 docs, S3 put_object](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html)
- [azure-identity docs, DefaultAzureCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.defaultazurecredential)
- [Azure SDK for Python, credential chains](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/credential-chains)
- [azure-storage-blob docs, ContainerClient](https://learn.microsoft.com/en-us/python/api/azure-storage-blob/azure.storage.blob.containerclient)
- [Amazon S3 docs, conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [DuckDB docs, Hive partitioning](https://duckdb.org/docs/current/data/partitioning/hive_partitioning.html)
- [DuckDB docs, JSON loading](https://duckdb.org/docs/current/data/json/loading_json.html)

Sources of single facts in the text:

- [`DescribeCapacityBlockOfferings` only reads offerings; buying is a separate action](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html)
- [`azure.identity` and `azure.core` are descendants of the `azure` logger; A logger at `NOTSET` uses the level of the nearest ancestor with a level, else the root level](https://docs.python.org/3/library/logging.html)
- [`azure.identity` credentials log their HTTP sessions at INFO level](https://learn.microsoft.com/en-us/python/api/overview/azure/identity-readme?view=azure-python)
- [`azure.core` HTTP logging policy logs each request and response at INFO by default](https://learn.microsoft.com/en-us/python/api/azure-core/azure.core.pipeline.policies.httploggingpolicy?view=azure-python)
- [`ruff format --check` writes no files and exits non-zero on an unformatted file](https://docs.astral.sh/ruff/formatter/)
- [`uv --directory <dir>` changes to the folder before the command runs](https://docs.astral.sh/uv/reference/cli/)
- [`Linux/UNIX` is an EC2 Spot price product description](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeSpotPriceHistory.html)
- [An Availability Zone code is the Region code followed by a letter; the letters `a`, `b`, `c` are a sim choice](https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-availability-zones.html)
- [The Azure Spot price data has the OS type `linux`; Azure Spot prices are in USD per hour](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
- [`NotAvailableForSubscription` is a Resource SKUs restriction reason code; `NotAvailableForSubscription` is a reason code of a SKU restriction](https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list)
- [`azure-mgmt-resource` is a PyPI package](https://pypi.org/project/azure-mgmt-resource/)
