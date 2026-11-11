# 0011. `--store` and a per-job `simulate` flag replace `--mode sim|real`

- Status: accepted, partly superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md), amended by [ADR 0016](0016-rename-the-consent-key-to-allow-billable-provisioning.md)
- Date: 2026-10-13

## Context

[ADR 0010](0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md), section 5, planned to replace `run_mode` (`local` / `cloud-cpu` / `cloud-gpu`) with a `run --mode sim|real` flag, for the whole process. The project rejects this before the build, in the issue that asked for this ADR. The project finds `--mode` awkward, and asks what it would even mean. Does a simulated run still query the cloud API? Does it still write to storage, or does it only write locally to validate a change? The question was whether the choice belongs on 1 probe at a time instead of on the whole process.

This ADR answers that question. It changes ADR 0010 sections 4, 5, 7, 10, and 11, and its Consequences, before the implementation pull request. It does not change code in this pull request.

### The questions `--mode` mixes

A single word cannot answer 3 separate questions at once:

1. **Where do the answers come from?** The real cloud API, or a simulator. A signal probe's simulator is the `sim.py` generators. An allocation probe's simulator is the probe's own code against a simulated client (spec 0003 section 7.1). Either way, no cloud call (section 7, item 6, below).
2. **Where do the rows go?** A cloud store (`s3://`, `az://`), or a local folder (`file://`).
3. **Does a row count as a result?** Only a row from a real call, on a real target, can feed the heat map.

Today's `run_mode` ties all 3 questions to 1 value, and it ties the choice to the whole run. A 4th question hides inside this: **at what level is the choice made?** Today it is 1 value per target entry in the file. ADR 0010 made it 1 value per process (`--mode`). The project asks whether it must be per probe instead.

## Decision

### 1. Options considered

**Option A: keep `--mode sim|real`, add `--store` only.** `--store` answers question 2 on its own. `--mode` still answers questions 1 and 3 together, for the whole process. This is the smallest change from ADR 0010. It does not fix the complaint, though. A developer may want 1 allocation probe to skip the cloud while every other job in the same run stays real. Option A still cannot do that: the choice stays per process, not per job.

**Option B (chosen below): a run-wide `--simulate` flag, a per-job `simulate` key, and `simulated` on the row.** This is the proposed design. `--store <uri>` answers question 2, for every job of the run. A job's own `simulate: true` key answers question 1 for that job alone: its rows come from the simulator, not the cloud. The run-wide `--simulate` flag sets `simulate: true` on every job at once, for a run with no cloud credentials at all. Every row carries `simulated: true` or `simulated: false`, so question 3 reads straight off the row. Only a `simulated: false` row on a real target is a result. The allocation guard looks only at a job that is not simulated (section 3, below).

**Option C: a per-job `source: simulate | real` enum, with no run-wide flag.** This turns option B's boolean into an enum, for a future third source. Nobody has asked for a third source yet. The enum also drops the 1-flag laptop and CI cases. Each of those needs to simulate every job in the file at once, with no edit to the file. [ADR 0008](0008-name-things-by-what-they-do-and-cost.md) already rejects 1 word with 2 meanings. An enum with 1 real member today is no simpler than a boolean, and it buys nothing yet. Rejected: it adds a word for a case that does not exist.

### 2. The choice: option B, with one simplification

Option B is chosen. It separates the 3 questions cleanly. It also matches the Prometheus-style module/job split of ADR 0010 section 1: a job already says "what, where, how often", and `simulate` adds "from where". It needs no new concept beyond a boolean on a job and a boolean on a row.

One part of the proposal is simplified. The proposal asks that a `--simulate` run refuse a cloud store unless the operator names it by hand. `--store` already has no default value. The CLI usage line marks it required on every call to `run` (spec 0003 section 2), and `prober/ftg_prober/cli.py` sets `required=True` on the flag. Every run already names its store by hand, so that condition is already true before `--simulate` exists. A second, separate flag could unlock a cloud store under `--simulate`. None of this ADR's own worked examples (section 4) needs that case. This ADR keeps the simpler rule instead: `run --simulate` refuses a `--store` URL that is not `file://`. ADR 0010 section 11.5 already gives this same rule to `run --mode sim`. There is no override flag for it. The `sim` subcommand gets the same rule, for the same reason. It has no per-job choice: every row it writes is `simulated: true`. This ADR gives every fully simulated run 1 rule, not 2 (section 4, below).

This rule applies only to the run-wide `--simulate` flag. A job's own `simulate: true` key carries no such limit. A job marked `simulate: true` can sit inside an otherwise live experiment file. It writes its `simulated: true` rows to the same `--store` as every other job in that run. That store can be the cloud. This is on purpose. A per-job `simulate: true` is a choice already written into the file. ADR 0010 section 11.1 already gives the experiment name this same file-is-the-only-source rule. There is no run-time accident to guard against, because the choice is already on record. The run-wide flag carries no such record, so it defaults to the safer, local-only behavior.

### 3. The allocation guard moves to the job

ADR 0010 section 4 puts `allow_billable: true`, the key of ADR 0010's own "billable" guard, at the top of the file. 1 key unlocks every job of an allocation module in that file at once. This is unsafe once a job can also carry its own `simulate: true`. Picture a live file with `allow_billable: true` already set, for its real allocation jobs. A second, different job creates real capacity only by accident, and its sole protection is `simulate: true`. Delete that 1 key from the second job, and the job becomes real and starts creating capacity. The file-level key is already `true`, so the guard does not fire. The Terraform precondition (ADR 0010 section 7) finds nothing wrong either, because the file already allows jobs that create capacity. 1 small edit turns on a real, paid-for allocation with no fault and no plan error.

This ADR moves consent from the file to the job. ADR 0010 section 4's top-level `allow_billable` key is removed. In its place, a job of an allocation module carries its own `allow_allocation: true` key, next to its own `simulate` key. The key is named `allow_allocation`, not `allow_billable`. Every row this prober writes already costs something, a store write, so "billable" names no 1 thing. The key names the 1 thing it actually gates: a probe that creates real capacity, a reservation or VMs (ADR 0008's own word for this, "allocation probe"). The guard's rule:

1. A job is **not** simulated when its own `simulate` key is not `true`, the command is not `sim`, and, on `run`, the run-wide `--simulate` flag is not set. Every job is simulated under the `sim` command (rule 5, below), no matter its own `simulate` key.
2. A job that is not simulated, and names an allocation module, must carry `allow_allocation: true` of its own. A job missing that key is 1 fault. The fault names the job's `module` and, where it has 1, its `regions`, the same way ADR 0010 section 4 names a billable entry today.
3. A job with `simulate: true` needs no `allow_allocation` key, whether or not it names an allocation module. It makes no cloud call, so there is nothing to allow.
4. Terraform's *active* rule (ADR 0010 section 7, spec 0005 R1) stays on 2 keys only. A job's allocation resources plan when its module is an allocation module and its own `simulate` is not `true`. `allow_allocation` plays no part in *active*. A `precondition` on a resource only runs when Terraform plans that resource. A gate that used `allow_allocation` to decide *active* would let an active allocation job with no `allow_allocation: true` go unplanned, with no error. It would also plan to remove an already-deployed job's resources the moment someone deletes its `allow_allocation: true` key. ADR 0010 section 7 already rejects that "quietly adding no resource" outcome. Instead, a separate check fails the plan outright when an active job lacks `allow_allocation: true` (section 7, item 7, below). **Superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md): there is no Terraform-side check of `allow_allocation` any more, separate or otherwise.** The *active* rule's 1st half (module and `simulate` decide resource shape) still stands; its 2nd half, a stack's own `grant_billable_provisioning` variable, now also gates whether that shape plans at all, independent of `allow_allocation` or any other file content.
5. The guard still runs in `ftg-prober sim`, as it does today (ADR 0010 section 4). `sim` treats every job as simulated, by rule 1 above, so the guard skips every job that `sim` runs. No job needs `allow_allocation: true` to run through `sim`.

A boolean next to the job it protects is the simplest working form. Deleting 1 key (`simulate: true`) from a job can never, by itself, make that job create real capacity, unless that job already carries its own `allow_allocation: true`. In that case the deletion is meant to make the job real again, on purpose, the way the pipeline-test example reverts (section 4, below). A job with no `allow_allocation: true` of its own still needs it added, by hand, to run for real. A reviewer then sees both keys on the same line.

**Failure example.** A live file lists 1 real, consented allocation job and 1 simulated test job of the same module:

> Superseded in part, 2026-10-15: the built layout differs from the examples of sections 3 and 4. `aws_odcr` is not a job. It stays an entry of `aws.allocation_probes`, with `instance_type`, `region`, `zone`, and `n`, outside the jobs model ([spec 0003](../spec/0003-prober-core-spec.md) section 3, `aws.allocation_probes`; `prober/ftg_prober/config.py`). The consent key is `allow_billable_provisioning` ([ADR 0016](0016-rename-the-consent-key-to-allow-billable-provisioning.md)), not `allow_allocation`. The two-job failure scenario still holds for an entry: an edit that removes `simulate: true` from an entry that has no consent key fails `check-config`. The examples below are kept as written.

```yaml
experiment: ftg-20261013-live

aws:
  jobs:
    - {module: aws_odcr, regions: [us-east-1], interval: 2h, allow_allocation: true}
    - {module: aws_odcr, regions: [us-west-2], interval: 2h, simulate: true}
```

An edit removes `simulate: true` from the second job, to try it against a region closer to the first, and does not add `allow_allocation: true`:

```yaml
experiment: ftg-20261013-live

aws:
  jobs:
    - {module: aws_odcr, regions: [us-east-1], interval: 2h, allow_allocation: true}
    - {module: aws_odcr, regions: [us-west-2], interval: 2h}
```

`check-config` fails, in the style `config.py` already uses for a billable entry (`prober/ftg_prober/config.py`, `_billable_faults_aws`):

```text
experiments/ftg-20261013-live.yaml: aws.jobs[1] (aws_odcr, [us-west-2]) creates real capacity. Set `allow_allocation: true` on purpose, or add `simulate: true`
```

`tofu plan` fails too, on the AWS stack's `terraform_data.allocation_guard` precondition, which lists the second job by name (section 7, item 7, below), for the same reason. The first job's own `allow_allocation: true` is untouched and keeps planning. A file-level key would have let both jobs through, because the first job already set it.

### 4. Worked examples

> Superseded in part, 2026-10-15: these examples put `aws_odcr` in `aws.jobs` with `allow_allocation: true`. The built layout keeps `aws_odcr` in `aws.allocation_probes`, with `allow_billable_provisioning` (see the note in section 3). The `--store`, `--simulate`, and `--probe aws_odcr` behavior they show is unchanged.

**The live prober**, every job real, writing to the cloud:

```bash
ftg-prober run --provider aws --config experiments/ftg-20261013-live.yaml \
  --store s3://ftg-live-bucket
```

```yaml
experiment: ftg-20261013-live

aws:
  jobs:
    - {module: aws_spot_score, regions: [us-east-1, us-east-2, us-west-2], interval: 5m}
    - {module: aws_odcr, regions: [us-east-1], interval: 2h, allow_allocation: true}
```

The `aws_odcr` job is an allocation job and not simulated, so it carries its own `allow_allocation: true` (section 3, above).

**A developer on a laptop with no cloud credentials**, the whole run simulated, local store only:

```bash
ftg-prober run --provider aws --config config/targets.yaml \
  --store file:///tmp/ftg-dev --simulate
```

The experiment file does not change. `--simulate` sets `simulate: true` on every job, for this run only. The allocation guard then skips every job, so no job needs `allow_allocation: true`. `--store` must be `file://`, so this command cannot point at the cloud by mistake.

**A dashboard demo**, a quick offline history. The command line is unchanged:

```bash
ftg-prober sim --config config/targets.yaml --store file:///home/dev/ftg/data/sim --hours 48
```

`sim` keeps its shape from spec 0003 section 2 (`--config`, `--store`, `--hours`, `--seed`). It must still get an absolute `file:///abs/path` URL (prober/ftg_prober/store.py), not a relative one. This ADR also gives `sim` the same store rule as the run-wide `--simulate` flag (section 2, above). `sim` refuses a `--store` URL that is not `file://`. This supersedes today's rule that `sim` does not check an `s3://` or `az://` store at all (spec 0003 section 7.2, rule 6). Every fully simulated run then follows 1 rule, not 2. `sim` still refuses a non-empty `file://` folder (the same rule 6). Its rows now carry `simulated: true` in place of `run_mode: local`. The allocation guard still runs in `sim`, but it skips every job, because every job through `sim` is simulated (section 3, rule 5, above).

**A pipeline test of 1 allocation probe in a live experiment, with no call that creates real capacity:**

This edits the live file of the first example, above, for the test window only. It adds 1 key to the `aws_odcr` job, `simulate: true`, and keeps the job's existing `allow_allocation: true` untouched:

```yaml
experiment: ftg-20261013-live

aws:
  jobs:
    - {module: aws_spot_score, regions: [us-east-1, us-east-2, us-west-2], interval: 5m}
    - {module: aws_odcr, regions: [us-east-1], interval: 2h, allow_allocation: true, simulate: true}
```

```bash
ftg-prober run --provider aws --config experiments/ftg-20261013-live.yaml \
  --store s3://ftg-live-bucket --probe aws_odcr
```

`--probe aws_odcr` limits the run to that 1 job (spec 0003 section 2, kept as is). The job's own `simulate: true` means the run makes no probe API call and creates no real capacity. This holds even though the run writes into the live, cloud `--store`, and the `aws_odcr` probe type is an allocation type. The job's `allow_allocation: true` does nothing here, because rule 3 of section 3, above, exempts a simulated job from needing it. It is kept only because this edit is on the same, real job and will be reverted, not a dedicated test-only job. A job that exists only to be simulated, and never runs for real, needs no `allow_allocation: true` at all.

Do not run `tofu plan` or `tofu apply` against this edited file. If `enable_allocation_probe` is derived from the file (see the FYI in Consequences, below, for why it is not yet), the AWS stack's *active* rule (spec 0004, the AWS form of R1 in spec 0005) reads a `simulate: true` job as inactive, so OpenTofu would plan to remove the IAM actions the `aws_odcr` job already has. Until then, `enable_allocation_probe` is a human-set variable, so this edit alone does not change a plan, but the same caution still applies once it does. Only the prober command runs against the edit. Revert the edit after the test window, before any OpenTofu operation.

**A CI test**, no cloud credentials, no cloud store:

```bash
ftg-prober run --provider aws --config config/targets.yaml \
  --store file:///tmp/ci-store --simulate --once
```

`--once` runs each listed job 1 time and exits 0 (spec 0003 section 2, kept as is). This checks the config, the scheduler, and the storage write path. It makes no cloud call and needs no cloud credentials.

### 5. Rows already written

A row written before this change has `run_mode` and no `simulated` field. A reader, for example the dashboard, must not guess row by row. It applies 1 fixed rule:

- `run_mode: local` means `simulated: true`.
- Any other `run_mode` value (`cloud-gpu`, `cloud-cpu`) means `simulated: false`.

This rule matches the 1 fact that `run_mode: local` already recorded (spec 0002, D19: "`local`... no provider calls"). No row changes meaning. No backfill of old objects is needed.

### 6. What this changes in ADR 0010

This ADR supersedes these exact items of ADR 0010:

- Section 4's file-level `allow_billable` key.
- Section 4's "does not apply under `--mode sim`" guard exemption.
- Section 4's last sentence ("a job for a billable probe type needs `allow_billable: true`" with no exception for a simulated job).
- Section 5, items 1 and 2.
- Section 5, item 4's last sentence (the sim-run storage note).
- Section 7's sentence on the `allow_billable: true` precondition.
- Section 10, item 3's bullet on the guard's key and default.
- Section 10, item 4's "sim/real flag" phrase.
- Section 11, items 4 and 5.
- The Consequences bullet on removing `run_mode` from every row, so that "no reader ever needs to read the old layout" (section 5, above, gives that reader its rule).

It does not change ADR 0010 section 1 (modules and jobs) or section 2 (1 file per experiment). It also does not change the cost class fixed per probe type in code (section 4, first paragraph). It keeps section 5 items 3, 5, and 6, section 10's other items, and section 11 items 1, 2, 3, and 6. Section 7 keeps its outcome (no allocation job plans with no consent), but not its mechanism: 1 `terraform_data.allocation_guard` resource, in each stack, carries a `precondition` over every active job, and every allocation resource depends on it, because neither cloud gives every active job its own Terraform resource to carry the check on its own, and a `check` block only warns (section 3, rule 4, and section 7, item 7, above).

- The `run` subcommand takes `--simulate` (boolean flag, no value) in place of `--mode`. There is no `sim`/`real` value and no `--mode` flag. The separate `sim` subcommand stays (section 4, above). ADR 0010 section 11.4's plan to fold it into `run --mode sim` does not happen.
- A job gains 2 optional keys, `simulate` and `allow_allocation` (both boolean, default `false`). They sit in the same place as its `regions` and `interval` keys (ADR 0010 section 1). These replace the per-entry `run_mode` value of today's `aws.allocation_probes` entries and their Azure counterpart (spec 0003 section 3). They also replace the top-level `allow_billable` key of ADR 0010 section 4.
- The allocation guard (ADR 0010 section 4) skips a job that is simulated: its own `simulate: true` key, or the run-wide `--simulate` flag on `run`, or the `sim` command (section 3, above). A job that is not simulated, and names an allocation module, needs its own `allow_allocation: true` (section 3, above). `check-config` has no run-wide flag, so it reads only each job's own `simulate` and `allow_allocation` keys.
- **The storage path does not change again.** ADR 0010 section 5, item 4 already drops the `run_mode=` partition: `experiment=<name>/provider=<p>/probe_type=<t>/date=<YYYY-MM-DD>/<file>`, `date` last. This ADR keeps that path. A simulated row's key looks exactly like a real row's key. `simulated` is a row field, not a path segment. `run_mode` was already a row field this way, read instead of the path's Hive value (the old page of spec 0006 said: "the record is the contract; the path is only the layout"; spec 0006 section 5.1 now has the reader rule).
- **The `Record` schema** (spec 0002 section 7.2) drops `run_mode` (`cloud-gpu` / `cloud-cpu` / `local`) and adds `simulated` (boolean). A reader of an old row without `simulated` applies the rule of section 5, above. A CPU-sized job stays an ordinary job, as ADR 0010 section 5, item 3 already decided. It is not a mode, and it is not `simulated`.
- 1 experiment file per experiment, shared by both clouds, stays exactly as ADR 0010 section 2 decided. `date` stays the last partition, as ADR 0010 section 5, item 4 decided.

### 7. What the implementation pull request must build differently

The implementation change builds ADR 0010's jobs model. Against that plan, it must also:

1. Add the `simulate` and `allow_allocation` keys to the job schema, not to today's allocation-entry schema, and not as a top-level `allow_billable` key.
2. Add `--simulate` to the `run` subparser in `prober/ftg_prober/cli.py`. Remove any `--mode` flag. Keep the `sim` subparser's other flags as is, and add the same `file://`-only store check that `run --simulate` gets (section 4, above).
3. Change the allocation guard to read each job's own `simulate` and `allow_allocation` keys, not an entry's `run_mode` and a top-level `allow_billable` key.
4. Change `Record` (likely in `prober/ftg_prober/record.py`): drop `run_mode`, add `simulated`.
5. Change `prober/ftg_prober/sim.py`, for the `sim` subcommand's own history backfill only: the `_row()` helper's `run_mode` parameter becomes `simulated` (a boolean). Each `GENERATORS` function's own `run_mode` parameter (for example `aws_spot_score(section, at, rng, run_mode)`) becomes `simulated` too, and it stays always `True`.
6. Give `run --simulate`, and a job's own `simulate: true`, 2 different paths, by probe family. A signal probe's job (`aws_spot_score`, `aws_spot_price`, `aws_spot_advisor`, `aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest`, `az_spot_score`, `az_spot`) calls the same `sim.py` generator in place of the real probe, the way today's `local` mode already does for a signal probe (spec 0003, rule P5). An allocation probe's job (`aws_odcr`, `az_capacity_reservation`, `az_vmss_flex`, `az_precheck`) instead runs the probe's own code against a simulated client (`SimEc2` for AWS, `SimAdapter` for Azure, spec 0003 section 7.1). That path reads the job's own `simulate` value in place of `ctx.run_mode(target)`. A pipeline test (section 4, above) runs the real probe code this way, with fault injection, not a generator. 1 scheduler call always runs 1 job, so 1 call's rows are always all simulated or all real. No object ever mixes the 2.
7. **Superseded by [ADR 0015](0015-two-independent-controls-replace-the-terraform-allocation-guard.md): the `terraform_data.allocation_guard` precondition, the resolved-module-type check, and the shared `tofu test`/`pytest` fixture this item describes are removed. Re-parsing the experiment file's `allow_allocation` key in HCL, to match PyYAML's own reading of the same YAML, had no principled stopping point once the build found that go-yaml and PyYAML resolve YAML 1.1 boolean literals differently. Each Terraform stack now reads its own `grant_billable_provisioning` variable instead, independent of the file. The "active" rule below (module and `simulate` decide resource shape) still stands; it no longer decides whether that shape plans at all, which the new variable does instead. The rest of this item is kept as a historical record of the design this ADR originally called for.**

   Change the Terraform "active" rule (ADR 0010 section 7, spec 0005 R1). A job is active, and gets a resource, when 2 things are true: its module is an allocation module, and its own file `simulate` key is not `true`. Terraform reads the file only. It has no view of a future run's `--simulate` flag. A job the file marks real still gets a resource, even when an operator later runs that file with `--simulate` by hand.

   A `check` block cannot enforce this: a failed `check` block assertion only reports a warning and lets the operation continue ([Terraform docs, `check` blocks](https://developer.hashicorp.com/terraform/language/block/check): "Terraform reports a warning and continues executing the current operation"). A `precondition`, by contrast, "throws an error with the `error_message` argument and stops the current operation" ([Terraform docs, custom conditions](https://developer.hashicorp.com/terraform/language/validate)).

   Azure does not give every active job its own resource either. A scale set (R4) covers only a job whose `sku` is not reservation-eligible. A job that uses a capacity reservation shares 1 reservation group per region (R3), and the prober creates the actual reservation itself at run time, not Terraform. So a `lifecycle { precondition }` on a per-job resource has the same gap on Azure that it has on AWS: a reservation job's consent would never be checked, because no Terraform resource exists only for that 1 job.

   Add 1 `terraform_data "allocation_guard"` resource to each stack instead, with a `lifecycle { precondition }` over every active job of every allocation module, not only the jobs that already get a resource. `local.active_jobs` fills in `simulate` and `allow_allocation` as `false` when a job omits them (for example `try(j.allow_allocation, false)`), so the condition never fails with an "Unsupported attribute" error on a job that sets only `module` and `regions`. The condition is `alltrue([for j in local.active_jobs : try(j.allow_allocation, false) == true])`, and the `error_message` lists each failing job by its index, `module`, and `regions`, the same 3 facts `check-config` already names in its own fault (section 3, above).

   `active_jobs` must resolve each job's `module` to its probe type before it decides whether the job is an allocation job, not test the bare `module` name. A job can name a `modules:` entry whose own `type` is an allocation type (for example `modules: {my_odcr_variant: {type: aws_odcr}}`, with a job `{module: my_odcr_variant}` and no `allow_allocation`), and that job creates real capacity even though its `module` name is not `aws_odcr`. Testing `contains(local.allocation_modules, j.module)` on the bare name misses this: the job is never a member of `active_jobs`, so it plans with exit 0 and no resource, with no error (confirmed on OpenTofu 1.12.6, the pinned version; [ADR 0014](0014-opentofu-is-the-iac-tool.md)) — the same "quietly adding no resource" outcome rule 4, above, already rejects. The fix resolves first: `local.job_type` (section 7, item 7, below, gives its final form), and `active_jobs` then tests `contains(local.allocation_types, local.job_type[k])`, a list of probe types, not module names. Every resource's `for_each` must key off the same resolved type, not the raw `module` name, for the same reason. This resolution rule is named once, and the prober's config loader, `make preflight`, and Terraform must all agree on it, so a `modules:` rename of an allocation type creates real capacity everywhere or nowhere, never only in 2 of the 3. ADR 0011 adds this case to ADR 0010's shared fixture (ADR 0010 section 4, the 1 shared test fixture file): a `modules:` entry that renames an allocation type, with no consent, must fail the same way an unrenamed allocation job does, in the Python tests and in `tofu test`.

   The rule, not a closed list: every resource whose own `count` or `for_each` plans only because an allocation job is in the file must carry `depends_on = [terraform_data.allocation_guard]`, not only the 1 resource that most obviously is "the allocation resource." Today that list is `terraform/azure/probe.tf`'s `azurerm_orchestrated_virtual_machine_scale_set.allocation`, `azurerm_capacity_reservation_group.allocation`, and `azapi_update_resource.allocation_vmss_policy` (all 3 already `for_each` over the file's allocation jobs); `terraform/azure/rbac.tf`'s `azurerm_role_assignment.prober_capacity_allocator` and `azurerm_role_assignment.sweeper_cleanup`, which ADR 0010 section 7 already gates on an allocation job even though the current code does not yet gate either one at all; and `terraform/aws/iam.tf`'s `aws_iam_role_policy.prober`, whose own statements, not a separate resource, are filtered by `var.enable_allocation_probe` (section 7, item 7's AWS note, 2 paragraphs below), so the implementation must either put `depends_on` on that 1 resource or split the allocation statements into their own resource that carries it. A later change to either stack must check this list against `terraform/aws` and `terraform/azure` again: a new resource whose `count` or `for_each` depends on an allocation job, with no `depends_on` added to this list, reopens the same gap.

   A root-module `output` with the same `precondition` is kept too, for a plain `tofu plan`'s own summary, but an output alone does not close the gap: Terraform does not pull an output into a `-target` plan's graph unless something targeted depends on it, so a `-target` of 1 job's resource would skip an output-only guard. The `depends_on` on each allocation resource is the part that actually blocks a `-target` plan or apply of an unconsented job (confirmed with a scratch Terraform 1.16.5 config, and reconfirmed with the same scratch config on OpenTofu 1.12.6, the pinned version: `plan -target` and `apply -target` of the unconsented job's own resource both fail with "Resource precondition failed", so the job is never created).

   A destroy is not blocked by either `precondition`, on either pinned version: a destroy only removes resources, so it cannot buy capacity, and `plan -destroy`, `destroy`, and a `-target` destroy all succeed on a file with an unconsented job (confirmed the same way, on Terraform 1.16.5 and reconfirmed on OpenTofu 1.12.6). This needs no teardown workaround. `-refresh-only` creates no resource either way, and its exact behavior differs between `plan -refresh-only` (a `Warning`, exit 0) and `apply -refresh-only` (an `Error`, exit 1 on the output precondition, still only a `Warning` on the resource precondition, confirmed the same way on both pinned versions); because neither case can create a resource, this ADR does not rely on `-refresh-only` for anything and does not describe it further. The implementation pull request's `tofu test` suite must have 1 run per resource on the `depends_on` list, above: `command = plan`, a `plan_options.target` naming that 1 resource's own address for an unconsented job, and `expect_failures = [terraform_data.allocation_guard]` ([OpenTofu docs, tests](https://opentofu.org/docs/cli/commands/test/)). A resource added later with no `depends_on` then fails a test only if it is also added to this test list, so a reviewer of a future change checks the test list against the 2 stacks the same way section 7, item 7's `depends_on` list, above, must be checked. 1 more run covers the module-type resolution above: a job that names a `modules:` entry of an allocation type, with no `allow_allocation`, must also get `expect_failures = [terraform_data.allocation_guard]`. 3 further runs cover the resolved-type check below: a `modules:` entry with no `type` key, the 2-entry chain (`modules: {A: {type: "B"}, B: {type: "aws_odcr"}}` with a job naming `A`), and a `type` value that differs from a built-in name only by case (for example `"AWS_ODCR"`); each is `command = plan` with `expect_failures = [terraform_data.allocation_guard]`. 4 more runs cover the `null`-resolution gap below, and use `plan_options.target` to name a file-independent resource already on the `depends_on` list (for example `aws_iam_role_policy.prober` on AWS), not the failing job's own resource, because that gap was specifically a `-target` deferral: a `modules:` entry with `type: ~`, a job with `module: ~`, a job with no `module` key at all, and a `modules:` entry named after a built-in type with no `type` of its own. Each still gets `expect_failures = [terraform_data.allocation_guard]`.

   A string value fails safe. `allow_allocation: "true"` (a quoted string) makes `try(j.allow_allocation, false) == true` evaluate to `false`, because a string never equals the boolean `true` in HCL (confirmed the same way, on both pinned versions), so the guard still fires. The config loader already refuses a non-boolean value for this family of key: spec 0003 section 3 states that `allow_billable` (the key this ADR renames to the per-job `allow_allocation`) is "Optional boolean... A non-boolean value is a fault," and ADR 0011 section 6 carries that typing into the per-job `simulate` and `allow_allocation` keys ("both boolean, default `false`"). `check-config` and Terraform then agree: a string value is always a fault or always a fail, never a silent pass, on either side.

   A job's `module` must resolve, through `local.job_type`, to a built-in probe type of the cloud — not merely to a name that appears somewhere in `local.modules` or in the built-in list. A `precondition` that tests the raw `module` name against a list of known names, `alltrue([for j in local.all_jobs : contains(local.known_modules, j.module)])`, misses every case where the name matches but the resolved type is wrong: a `modules:` entry with no `type` key at all; a chain, for example `modules: {A: {type: "B"}, B: {type: "aws_odcr"}}` with a job naming `A` (the resolution gives `"B"`, a module name, not a built-in type, so a chain is refused rather than followed further); and a `type` value that differs from a built-in name only by case, for example `type: "AWS_ODCR"` (exact, case-sensitive match fails: `"AWS_ODCR"` is not `"aws_odcr"`). Each of these 3 inputs plans with exit 0, "No changes," against the raw-name check, not an error (confirmed on OpenTofu 1.12.6, the pinned version). The fix drops the raw-name check and tests the resolved type instead, over every job, active or not: `alltrue([for k, j in local.all_jobs : contains(local.builtin_types, local.job_type[k])])`, where `local.builtin_types` is the full list of the cloud's built-in probe types, signal and allocation together. This is the same rule spec 0003 section 3 already states for the config loader ("`type` must be a known probe type of that cloud"), now enforced in Terraform too, so the 2 implementations cannot drift apart on what counts as a known type. The `error_message` names each failing job by its index, its `module`, and its resolved type (`local.job_type[k]`). `local.known_modules` is removed: nothing else in the guard reads it. A job must never become inactive, and skip the consent guard, only because its `module` resolved to a type that is not built in.

   This check only works when `local.job_type[k]` is always a string, `""` included, and never `null`. On Terraform 1.16.5 (the version this guard was first proved on), `contains(list_of_strings, null)` is itself unknown, not `false` (`terraform console` on that version prints `(known after apply)` for `contains(["a", "b"], null)`), and Terraform treats a `precondition` whose condition value is unknown as not yet decided, not as failed: a `-target` plan or apply of an unrelated, file-independent resource already on the `depends_on` list (for example `aws_iam_role_policy.prober`, which carries no `for_each` over jobs) succeeds with exit 0 and no error on a job whose resolved type is `null` (confirmed on Terraform 1.16.5 with a `modules:` entry's `type: ~`, and separately with a job's `module: ~`).

   **This one quirk does not carry over to OpenTofu 1.12.6, the pinned version** ([ADR 0014](0014-opentofu-is-the-iac-tool.md)). On OpenTofu, `contains(["a", "b"], null)` is not deferred: `tofu console` fails outright with "Invalid function argument: Invalid value for 'value' parameter: argument must not be null." The same `-target` plan of `aws_iam_role_policy.prober` that succeeded with exit 0 on Terraform 1.16.5 instead fails immediately on OpenTofu 1.12.6 with that same error (confirmed with the pre-fix, raw `try(local.modules[j.module].type, j.module)` formula on both pinned versions, same scratch config, same inputs). This does not reopen the gap this item closes, and the guard design does not change: the fix below (`local.job_type` with `coalesce()` inside `try()`) was already written to guarantee `local.job_type[k]` is always a string, so it never calls `contains()` with a `null` argument on either tool. With the fix in place, a null-typed job fails the `TYPE` precondition immediately on OpenTofu 1.12.6 the same way it fails on Terraform 1.16.5 (both confirmed below). The difference only matters for the counterfactual, pre-fix formula this paragraph uses to justify the fix; the shipped guard's behavior is identical on both tools. A 3rd `try()` argument of `""` does not close the pre-fix gap on its own: `try(local.modules[j.module].type, j.module, "")` reaches its 3rd argument only when an earlier argument *errors*, and a present key whose value is literally `null` is not an error, so `.type` (and, before it, `j.module`) can each hand back `null` with nothing left to catch.

   `local.job_type` therefore does 2 things, not 1: it picks which name to resolve (does the job name a `modules:` entry, or a built-in type directly?), and it turns every non-string result of that pick, `null` or missing, into `""`, with `coalesce()` inside `try()`: `coalesce()` itself errors on a `null` or absent argument, and `try()` catches that error and supplies `""`.

   ```hcl
   local.job_type = {
     for k, j in local.all_jobs : k => (
       contains(try(keys(local.modules), []), try(coalesce(j.module), ""))
       ? try(coalesce(local.modules[j.module].type), "")
       : try(coalesce(j.module), "")
     )
   }
   ```

   When the job's `module` names a key of `local.modules`, `local.job_type[k]` is that entry's own `type` only, never the module name: a `modules:` entry with no `type` key, or `type: ~` (`null`), resolves to `""` and is refused by the built-in-type check, the same as any other unknown type. This also closes a 2nd drift the earlier form left open: `modules: {aws_odcr: {}}` (an entry named after a built-in type, with no `type` of its own) used to resolve to `"aws_odcr"` through the old formula's fallback to `j.module`, so it silently passed as the built-in type its name happened to match; now it resolves to `""` and is refused, the same way the prober's own config loader already refuses a `modules:` entry with no `type` (spec 0003 section 3). When the job's `module` does not name a key of `local.modules`, `local.job_type[k]` is the module name itself, so a direct use of a built-in type (`module: aws_odcr`, no `modules:` entry of that name) still resolves correctly; a `module: ~`, or a job with no `module` key at all, resolves to `""` the same way, through the same `coalesce()`/`try()` pair, and is refused rather than deferred.

   With this formula, `local.job_type[k]` is always a string, so `contains(local.builtin_types, local.job_type[k])` is never asked to test `null` and the condition is always known, never deferred, under a plain plan or a `-target` plan or apply of any resource (confirmed on Terraform 1.16.5 and reconfirmed on OpenTofu 1.12.6, the pinned version: the same `type: ~` and `module: ~` inputs above now fail both a plain plan and a `-target` apply of `aws_iam_role_policy.prober` with "Resource precondition failed," exit 1, not exit 0, on both tools, and OpenTofu raises no "Invalid function argument" error here, because `local.job_type[k]` is never `null`). The `error_message` still names each failing job by its index, its `module`, and its resolved type; a job with no `module` key names that key as absent, through `local.job_type[k]` itself being `""`, rather than through a raw-expression error.

   The condition is known at plan time only because every job comes from `yamldecode(file(...))` of the experiment file, and Terraform reads a file at plan time. A job value that comes from another resource's attribute, known only after apply, makes the condition unknown at plan time. A `-target` plan of a file-independent resource shows no error on that unrelated target, and the apply of that saved `-target` plan still fails the guard precondition before the guard resource is created (observed on Terraform 1.16.5 with a `terraform_data` resource's `id` as a job's `module`; reconfirmed the same way on OpenTofu 1.12.6, the pinned version). A plain, untargeted plan of this same scratch config fails on both tools before it reaches the guard at all, with "Invalid for_each argument," because `terraform_data.job`'s `for_each` depends on `local.active_jobs`, whose own membership is unknown until apply; this is identical on both tools, so it is not a gap the guard needs to close. Thus `local.all_jobs` must come from the experiment file only, never from a computed value, and a reviewer checks this on any change to how the stacks read the file.
8. Change the dashboard's `run_mode` single-select (the old spec 0006 section 6.6, item 2; that rule is gone) to a `simulated` toggle, default hiding `simulated: true` rows. The board built later has no toggle: the server drops `simulated: true` rows and `meta.simulated` shows the banner (spec 0006 section 5.2 item 2, decision R7). `meta.run_modes` becomes `meta.simulated` values present in the store. A query over old rows with no `simulated` field applies the rule of section 5, above.

### 8. Specs and the ADR index

Every spec passage that describes `--mode`, `run --mode sim|real`, a `run_mode`-based rule, or a file-level `allow_billable` rule (ADR 0010's own name for its consent key) that this ADR changes gets a short note. The note says that this ADR replaces that plan with `--store`, a run-wide `--simulate` flag, and per-job `simulate`/`allow_allocation` keys. The specs: spec 0002, spec 0003, spec 0004, spec 0005, spec 0006, spec 0008, spec 0009, and spec 0010. This ADR is added to the table in `decisions/README.md`.

## Consequences

- ADR 0010 section 4 keeps its cost-class rule (fixed per probe type in code). Only the file-level `allow_billable` key, and the guard's `--mode sim` exemption, are superseded, by per-job keys (section 3, above).
- ADR 0010 section 5 keeps items 3, 5, and 6 (the CPU-sized job rule, the ADR 0008 table supersession, and the migration plan). Items 1 and 2, and item 4's last sentence, are superseded (section 6, above).
- ADR 0010 section 7 keeps its "no allocation job plans with no consent" outcome, but not its mechanism. *Active* now reads only the module and `simulate`. Each stack gets 1 `terraform_data.allocation_guard` resource with a `precondition` over every active job, and every allocation resource depends on it, so a `-target` plan or apply of 1 job's resource still evaluates the guard. A root-module `output` with the same `precondition` is kept for a plain plan's summary, but it does not by itself cover `-target`. Neither is a `check` block, which only warns (section 3, rule 4, and section 7, item 7, above).
- ADR 0010 section 10 keeps items 1, 2, and 4's main point. Item 3's bullet on the guard's key and default, and item 4's "sim/real flag" phrase, are superseded.
- ADR 0010 section 11 keeps items 1 (env var removal), 2 (`allow_billable` file-only, now `allow_allocation` read per job), 3 (`--probe` meaning), and 6 (unknown module name is a fault). Items 4 and 5, about `--mode`, are superseded.
- No code changes in this pull request. The implementation pull request builds 2 things together: the jobs model of ADR 0010, and the `--store`/`simulate`/`allow_allocation` design of this ADR. The 2 land once, not twice.
- Spec changes in the implementation pull request:
  - Spec 0002: D19, the `Record` field, the storage key.
  - Spec 0003: the CLI table, the config schema, the `Record` field list, the scheduler's per-mode object split, section 7.
  - Spec 0004: the `enable_allocation_probe` condition.
  - Spec 0005: the Terraform active rule.
  - Spec 0006: the `run_mode` filter and glob.
  - Spec 0008 and spec 0009: the `run_mode` row field.
  - Spec 0010: the per-entry mode read.

  This pull request only marks the current passages as superseded. It does not rewrite them.
- `config/targets.yaml` keeps its `run_mode: local` entries until the implementation pull request lands, the same way ADR 0010 left it (ADR 0010, Consequences).
- A reader of a row written before the implementation pull request has no `simulated` field. It applies the rule of section 5, above, instead of treating the row as a fault.
- FYI, left for a later pull request: AWS's `enable_allocation_probe` variable (spec 0004) and the per-job `allow_allocation` consent list can drift apart, because a human sets the variable and the file sets the keys. A later pull request can remove the variable and read the IAM condition straight from the file's active jobs, the way the Terraform *active* rule already does.
