# 0012. Update the code and the config with a VM-side pull and restart, not a VM replacement

- Status: accepted
- Date: 2026-10-13

## Context

Today a change to the prober code or to `config/targets.yaml` gives the 2 code zips a new
content hash. The new hash gives each zip a new object key. The new key goes into the user
data of the AWS instance and the `custom_data` of the Azure VM. A change to either value
forces Terraform to replace the VM
([`user_data_replace_on_change`](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/instance);
Azure's `custom_data` argument: "Changing this forces a new resource to be created"
([`azurerm_linux_virtual_machine`](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_virtual_machine))).
A replacement costs a few minutes with no rows.

The call queue, the token bucket, and the Spot score freeze live only in the memory of the
running `ftg-prober` process ([`prober/ftg_prober/callqueue.py`](../prober/ftg_prober/callqueue.py),
[`prober/ftg_prober/scheduler.py`](../prober/ftg_prober/scheduler.py)). A VM replacement and a
plain service restart end that process the same way, so they have the same effect on this
state. The answer below states that effect, and the project accepts it: "I am OK with the
simplicity of downtime", with the 1 question of what a restart does to the queue.

The project wants this decision to remove the VM replacement only. A later change covers a
hot reload with no restart at all, for adding or removing probes or changing the config with
no downtime.

The manifest records the 2 keys that the VM must run (the code zip and the config zip), and `sync.sh` (section 4) logs them
on every successful sync, so an operator can read what the VM just installed. Detecting a
stale copy of the source tree before it is ever zipped, for example a build cache
that did not pick up the latest commit, is a separate problem from the VM replacement this
decision removes; a later change covers it.

> Revision, 2026-10-15: the manifest now also names the sync script itself, as `sync_key` (AWS) and `sync_blob` (Azure), and the VM replaces its own `sync.sh` from it before the other keys (self-update). Those 2 keys are not part of this decision. The procedure is in [spec 0003](../spec/0003-prober-core-spec.md) section 6A, item 9, and in `terraform/sync.sh`.

## What a VM replacement and a service restart do today

A VM replacement (Terraform destroys the old VM and starts a new one) and a service restart
(`systemctl restart ftg-prober.service` on the same VM) end the running process the same way.
Both have the same effect on the facts below, because none of them live outside the process:

| Fact | Where it lives | Effect of a replacement or a restart |
|---|---|---|
| Queued Capacity Block calls | `CallQueue.items`, an in-memory dict ([`callqueue.py:56-65`](../prober/ftg_prober/callqueue.py)) | Lost. The new process starts with an empty queue and plans a fresh cycle (spec 0003, section 6A, rule 1). |
| The token bucket of each AWS Region | `TokenBucket.tokens`, an in-memory dict ([`callqueue.py:26-45`](../prober/ftg_prober/callqueue.py)) | Reset to `START_TOKENS` (5), not the real bucket level. This already matches spec 0003 section 6A, rule 2: "it starts with 5 tokens, because the real bucket can be partly drained." |
| A multi-step search in progress (`follow_up`) | The follow-up calls that `follow_up(call, rows)` adds to the same in-memory queue (spec 0003, section 6A, rule 5) | Lost entirely. No step of the search writes a row (spec 0003, section 6A, rule 5), so the next process's plan starts the search again from the first step (spec 0008, section 3B). |
| The AWS Spot score freeze | 2 values held "for the process lifetime" (spec 0008, section 1, rule 8; section 4.4) | Ends. Spec 0008 section 4.4, rule 5: "The freeze ends only when the process stops." The next process logs `spot_score.frozen` again if it hits the limit again. |
| The Azure 429 pause | "in the memory of the probe object" (spec 0009, section 2.6, rule 14) | Ends. "A new process starts with no pause, and its first request can get a 429 again." |
| Rows already written | 1 JSONL object per call, written right after the call returns, never held in memory for a later write (spec 0003, section 6, rule 4) | Not affected. The object is already in the data store before the process ends. |

On a plain stop or restart, systemd sends SIGTERM first; the scheduler finishes the probes
already due and the worker wake already in progress, and writes their rows, before it exits
(spec 0003, section 6A, rule 8). A restart loses only the calls that still wait in the queue at
that moment, with the backoff state (attempts and not-before time) of each, not rows already
written. The framing is correct: the loss is the rest of 1 cycle, not data
corruption.

## Decision

### 1. Options considered

| # | Option | AWS | Azure |
|---|---|---|---|
| 1 | A fixed object key for a small manifest, plus a VM-side systemd timer that pulls on a change and restarts the service | Removes the replacement for a code or a config change. Downtime: the restart only, a few seconds. | Same. |
| 2 | The prober re-reads the config at the start of each cycle, with no restart | Removes the replacement, and removes the restart, for a config-only change. Needs its own code change (a config-reload path) and does not cover a code change: a new package still needs a restart. | Same. |
| 3 | Keep the hashed keys. An operator runs SSM Run Command (AWS) or Run Command (Azure) to pull the new zips and restart, outside Terraform | Removes the replacement. Needs an operator action for every update; a config or a code change with no operator present does not reach the VM. | Same. |
| 4 | Persist the call queue, for example to the data bucket, so a restart resumes it | Does not touch the replacement question. Adds a feature to cover a loss the issue's own text calls "the rest of 1 cycle, not data corruption." | Same. |
| 5 (added here) | A fixed manifest key read once at boot and again on a timer — option 1, but written so 1 script covers both the first boot and every later update | Same as 1, with less code: 1 code path, not 2. | Same. |

### 2. The pick: option 5 (a fixed manifest, a timer, a pull, a restart), for both clouds

Option 5 is option 1 written so the VM runs 1 script at boot and on every later tick, instead
of 1 script at boot and a second script for updates. It is the simplest option that removes the
VM replacement for both a config-only change and a code change, on both clouds:

- Option 2 removes the replacement only for a config change, not a code change, and needs a
  config-reload code path the prober does not have today. The later change the project asked
  for covers this.
- Option 3 removes the replacement, but it needs an operator to run a command for every single
  update. A config or a code change with no operator watching does not reach the VM.
- Option 4 does not answer the question this ADR asks. The table above already shows the loss
  is bounded and the rows already written are safe; adding a persistence layer for a bounded,
  non-corrupting loss is a feature the evidence does not call for (repo rule: evidence before
  features).

**Downtime that remains:** the time of 1 `systemctl restart ftg-prober.service`, a few seconds.
The project already accepts this.

**What happens to the queue:** the same as today's restart, shown in the table above. This
decision does not change that behavior; it only removes the VM replacement as a second way to
trigger it.

### 3. The Terraform shape change

**AWS** (`terraform/aws/`):

- `aws_s3_object.prober` and `aws_s3_object.config` keep their hashed keys
  (`code/prober-<sha256>.zip`, `code/config-<sha256>.zip`) and the 30-day expiration rule on the
  `code/` prefix (`aws_s3_bucket_lifecycle_configuration.data`, `s3.tf`). Nothing here changes.
  FYI, unrelated to this decision: that rule can delete a live zip 30 days after it was
  uploaded, if nobody applies in that time; the next apply uploads it again, under the same key
  (the key is a hash of the content, which has not changed), so this self-heals on the next
  apply, not on its own.
  2026-10-15: the `expire-code` rule was removed (spec 0004 section 4.2), so the
  `code/` prefix has no expiration and the FYI above no longer applies.
- `aws_s3_object.prober` and `aws_s3_object.config` each add
  `lifecycle { create_before_destroy = true }`. An S3 key cannot change in place, so a hash
  change still replaces the object; this setting only changes the order to create-then-destroy.
  Combined with the `depends_on` below, it also defers the old object's destroy until after any
  resource that depends on it has finished: Terraform's own documented behavior for a dependency
  of a `create_before_destroy` resource.
- A new `aws_s3_object.manifest` writes a small JSON object with a fixed key, `manifest.json`,
  at the bucket root, not under `code/`, so the 30-day `code/` expiration rule never deletes it.
  Its content is `{"prober_key": ..., "config_key": ...}`, the current hashed keys. `bucket`
  and `key` are the only `ForceNew` fields of this resource
  (`internal/service/s3/object.go`), and neither ever changes on this particular resource, so
  Terraform updates `aws_s3_object.manifest` in place, a new `PutObject` at the same key: there
  is no window where `manifest.json` does not exist on AWS.
  It `depends_on` `aws_s3_object.prober` and `aws_s3_object.config`, so Terraform writes the new
  content only after both zips exist at their new key, and, because of the
  `create_before_destroy` setting above, only before either zip's old key is destroyed: the
  manifest never names a key that is already gone.
- `aws_instance.prober`'s `user_data` template stops taking `prober_key` and `config_key`. It
  takes only the fixed `manifest.json` key, and it `depends_on` `aws_s3_object.manifest`. A code
  or a config change no longer changes the rendered user data, so
  `user_data_replace_on_change = true` no longer fires for this reason. (It still fires for an
  unrelated user-data change, for example a new proxy URL; that is correct and out of scope
  here.) The 1 exception: the apply that first switches the stack to this design changes the
  rendered user data itself, from the hashed keys to the fixed manifest key, so that apply
  replaces the VM 1 more time. Every apply after that one does not.
- `aws_instance.prober` **is the resource that stops forcing a replacement** for a code or a
  config change, after that 1 switchover apply.

**Azure** (`terraform/azure/`):

- `azurerm_storage_blob.prober` and `azurerm_storage_blob.config` keep their hashed names
  (`prober-<sha256>.zip`, `config-<sha256>.zip`), and each adds the same
  `lifecycle { create_before_destroy = true }` as the AWS zips. A blob name cannot change in
  place either, so a hash change still replaces the blob; this changes the order to
  create-then-destroy, the same reason as AWS.
- A new `azurerm_storage_blob.manifest` writes a small JSON blob named `manifest.json` into the
  same `code` container, with `source_content = jsonencode({prober_blob = ..., config_blob = ...})`.
  Azure sets no lifecycle rule on this container today, so there is no expiration to work
  around. Unlike AWS, this is not an in-place update: `source_content` "forces a new resource
  to be created"
  (`website/docs/r/storage_blob.html.markdown`), so **every** code or config apply, not only the
  first switchover apply, destroys and recreates `manifest.json`. `create_before_destroy`
  cannot help here, because a fixed name cannot have 2 blobs at once: there is no "new" identity
  to create before the "old" one is destroyed, the way the 2 code blobs above get 1 (different
  hashed names, 1 brief overlap). `sync.sh` (section 4) must treat a missing `manifest.json` the
  same as "no change, try again next tick," not as an error: on Azure, this gap is routine, on
  every apply, not a rare race. It `depends_on` `azurerm_storage_blob.prober` and
  `azurerm_storage_blob.config`, so the content it writes, once recreated, is never stale: it
  always names the 2 blobs that already exist by the time Terraform gets to it.
- `azurerm_linux_virtual_machine.prober`'s `custom_data` template stops taking `prober_blob` and
  `config_blob`. It takes only the fixed blob name `manifest.json`, and it `depends_on`
  `azurerm_storage_blob.manifest`. A code or a config change no longer changes `custom_data`,
  which Azure's own doc states forces a replacement. As on AWS, the 1 apply that switches the
  stack to this design still replaces the VM, because that apply itself changes `custom_data`
  from the hashed blob names to the fixed one; every apply after that one does not.
- `azurerm_linux_virtual_machine.prober` **is the resource that stops forcing a replacement**,
  after that 1 switchover apply.

### 4. The VM-side mechanism

Both clouds already fetch the code over the network at boot (AWS: `aws s3api get-object` in
`user_data.sh.tftpl`; Azure: `fetch-blob.sh` in `cloud_init.yaml.tftpl`). The change reuses that
same fetch, now driven by a small script that cloud-init or the user data installs once, and
installs each build as its own release, not in place, because a venv cannot simply be moved
(its scripts carry an absolute shebang back to the venv's own path) and `rename(2)` onto a
directory that is not empty fails.

`ftg-prober-sync.timer` and `ftg-prober-sync.service` run as root, a separate unit from
`ftg-prober.service` itself, which stays unprivileged (`User=ftg` on AWS, `DynamicUser=yes` on
Azure). Root is what writing under `/opt/ftg`, installing packages, swapping the `current`
symlink, and calling `systemctl restart` all need. This is acceptable because the boundary that
matters here is not root against the unprivileged prober user: both processes, on the same VM,
reach the cloud through the same instance identity (the AWS instance profile over IMDS, the
Azure managed identity), so neither side has a credential the other lacks. The control that
matters is section 5's explicit `Deny`, between the prober process (which parses untrusted
external API responses, and so is the more likely process to end up running attacker-influenced
code) and the content root's sync unconditionally installs and runs. **Residual risk:**
`sync.sh` still trusts whatever currently sits at `manifest.json` and `code/*`; anyone who can
write those keys by a path the `Deny` does not cover, for example a separate compromised
credential or a write to the bucket outside this role entirely, still gets root code execution
on the VM at the next sync.

On the filesystem itself, `/opt/ftg/releases`, `/opt/ftg/current`, and `/etc/ftg` are owned by
root, not writable by the prober's own unprivileged identity: the prober user has read and
execute only on them, enough to run its own venv and read its own config, and no write access
anywhere under `/opt/ftg`. On AWS this is a plain Unix permission (`User=ftg`, with no group or
other write bit on these paths). On Azure, `DynamicUser=yes` already implies
`ProtectSystem=strict` and `ProtectHome=read-only`
([`systemd.exec(5)`](https://manpages.ubuntu.com/manpages/noble/man5/systemd.exec.5.html)), so
the prober's own unit cannot write under `/opt/ftg` even if its dynamically allocated user were
somehow granted the bit, unless the unit also lists it in `ReadWritePaths=`, which this design
does not add.

1. `/opt/ftg/bin/sync.sh` fetches `manifest.json` (AWS: `aws s3api get-object`; Azure:
   `fetch-blob.sh manifest.json <output file>`, its existing 2-argument form), and compares it
   to a cached copy at `/etc/ftg/manifest.json`. On Azure, a real `BlobNotFound` counts as
   "unchanged": `sync.sh` does nothing further that tick, the same as for a manifest that has
   not changed. This is routine on Azure (section 3), where `manifest.json` briefly does not
   exist on every apply that changes the code or the config, not only the first switchover
   apply. On AWS, `manifest.json` is never absent (section 3: the resource updates in place, so
   there is no window where the key does not exist), so this rule has no "not found" case to
   handle there. Every fetch error, on either cloud (a network fault, an expired token,
   `AccessDenied`, and on Azure anything that is not `BlobNotFound`), is logged as it is returned
   and leaves everything as it is, to retry on the next regular tick (rule 12), not blacklisted
   the way rule 7 blacklists a bad manifest, because the manifest itself was never read, so
   there is nothing yet known to be bad about it. This needs 1 thing that today's script does
   not have: on Azure, `fetch-blob.sh`'s own exit code must distinguish "not found" from every
   other failure (today it retries for up to 10 minutes and gives up with the same exit code
   either way, per its own retry loop).
2. If the manifest is new or different, `sync.sh` picks a release ID for it, for example the
   sha256 of the manifest's own content. Before anything else, it removes: any release
   directory under `/opt/ftg/releases/` that has no `.complete` marker (rule 3), a partial
   install left behind by an earlier, interrupted attempt; any `/opt/ftg/releases/.tmp-*` file,
   the raw zip bytes of an interrupted download (rule 3); and any `/opt/ftg/releases/.next-*`
   symlink, the temporary name of an interrupted swap (rule 5). If `/opt/ftg/releases/<release ID>/`
   already exists at this point, it has a `.complete` marker already (the cleanup above would
   otherwise have removed it): this manifest names a release `sync.sh` already built once, for
   example a revert to an earlier deploy that gives back the same manifest content and so the
   same release ID. `sync.sh` recomputes the 1 combined hash of rule 3 over this existing
   directory's files and compares it to the value rule 3 stored alongside `.complete`. A match
   means the release is still exactly what it was when it was last built and verified: `sync.sh`
   skips the fetch and the install below entirely and goes straight to the gate (rule 4) and the
   swap (rule 5) with this existing release. A mismatch means the directory is not trustworthy
   as it stands, for example disk corruption since the last build: `sync.sh` deletes it and
   falls through to a full rebuild below, the same as if `.complete` had been missing; this
   decision does not try to repair or replace only the files that changed, the simplest answer
   to "verify it again," not the most efficient one. Otherwise, with no existing directory,
   `sync.sh` creates
   `/opt/ftg/releases/<release ID>/` itself, empty, as the install target: every later step
   builds directly inside this final path. Nothing in this rule, or in rule 3, ever moves a
   half-built release into place; a release directory is either absent, or built in place at
   its own final path and later marked `.complete`, never relocated after the fact. `sync.sh`
   never writes into `/opt/ftg/current/`, the path the running service uses.
3. `sync.sh` fetches each of the 2 zips the manifest names to a throwaway file,
   `/opt/ftg/releases/.tmp-<pid>-<name>.zip`, computes its sha256, and compares it to the hash
   already in the object's own key or blob name (`code/prober-<sha256>.zip`,
   `prober-<sha256>.zip`, main.tf and control.tf), before it unzips anything. A failed download
   is a transient error, the same as a manifest fetch error (rule 1): `sync.sh` logs it as
   returned, removes the throwaway file and the still-empty release directory of rule 2, leaves
   `/opt/ftg/current/` and the running `ftg-prober.service` untouched, and retries on the next
   regular tick. This stays a transient error only up to a limit, tracked by a time stamp, not a
   count: on the first failed download after a success, `sync.sh` writes the current UTC time
   to `/etc/ftg/download-failing-since`, alongside the other sync state under `/etc/ftg`, not
   under `/opt/ftg/releases`. A successful download removes the file, so the next failure, for
   any manifest, starts the clock over. A missing file means no failure is in progress yet. An
   unreadable file (for example a crash mid-write left it truncated) is treated the same as a
   missing one for the purpose of starting the clock: `sync.sh` logs this, then overwrites it
   with now, the same as a first failure. A negative gap, now minus `download-failing-since`
   less than 0 (the wall clock moved backward after the stamp was written, for example an
   out-of-order boot-time correction), is treated the same way: `sync.sh` logs it and rewrites
   the stamp with now, rather than treat a clock that has already corrected itself as still
   owing it a wait. On each failed download, `sync.sh` reads `download-failing-since` and
   compares it to now: once now minus that time is 1 hour or more
   (`>=`, not `>`, so a crash that lands exactly on the hour still trips it the next time this
   check runs), `sync.sh` stops treating the download as transient for whichever manifest it is
   currently trying: it writes the failed-manifest marker of rule 7 for that manifest, logs a
   distinct event, `sync.download_failed_repeatedly`, with the recorded failing-since time and
   the last download error as returned, and leaves `/opt/ftg/current/` and the running
   `ftg-prober.service` untouched, the same end state the sha256-mismatch branch below already
   reaches. A single time stamp, not a per-manifest count, means 2 different bad manifests on
   consecutive ticks still trip the limit after the same 1 hour of unbroken failure, where a
   count keyed to "the same sha256" would reset on every alternation and never trip at all; it
   also means a crash right after the limit is reached, but before the marker is written, still
   trips it on the very next tick, because the comparison reads `>=` against the clock, not
   `==` against a counter a missed write could skip past. `download-failing-since` is cleared at
   exactly 4 points, so it cannot outlive the failure streak it is timing: a successful download
   (above); the moment the limit trips and `manifest.failed` is written (the same step, so a
   blacklisted manifest never leaves a time behind for anything later to misread); a tick whose
   fetched manifest equals the cache (rule 10, "unchanged"), because that manifest is the one
   already running, with nothing in progress to be timing; and when rule 7 or rule 11 lets a new
   manifest through past an existing `rollback.failed` or `manifest.failed`, because that new
   manifest's own failure streak, if it ever has one, has not started yet. A design that instead
   stored the manifest's own sha256 in the file next to the time, and treated the stamp as
   absent whenever the sha256 did not match the manifest currently being tried, would also keep
   a stale stamp from reaching a brand new manifest's first failure, but it reopens the
   alternating-manifest hole this rule exists to close: 2 bad manifests that keep swapping
   places never match the cache either, so clearing on a cache match does not reopen it, but
   clearing on every sha256 change would, since the stamp would read as absent on every single
   tick and the 1-hour limit would never trip, the same failure mode a per-sha256 count already
   had. The single, unkeyed clock stays for that reason; only the 4 points above clear it. A
   sha256 mismatch, though, stops `sync.sh`
   there: it logs the failure, removes the throwaway file and the still-empty release directory
   of rule 2, writes the failed-manifest marker of rule 7 (so this manifest is not retried every
   tick either), and leaves `/opt/ftg/current/` and the running `ftg-prober.service` untouched.
   Once both hashes check out, `sync.sh` unzips the
   config zip directly into `/opt/ftg/releases/<release ID>/config/`, and creates the venv
   directly at `/opt/ftg/releases/<release ID>/venv/` with `python3 -m venv` and installs the
   package into it: this venv's own scripts carry an absolute shebang back to this exact path
   from the moment they are written, and the path never changes under them, because the venv is
   never built anywhere else and moved. It then runs the config gate of rule 4, using that same
   venv. A failed `pip install` or a failed gate stops `sync.sh` the same
   way as a sha256 mismatch: log, remove the incomplete release directory, write the
   failed-manifest marker, leave `/opt/ftg/current/` and the service untouched. Only once the
   gate passes does `sync.sh` compute 1 more hash, a single sha256 over the sorted list of
   every file now under this release directory, each paired with its own sha256, and write it
   to `/opt/ftg/releases/<release ID>/.sha256`, then write
   `/opt/ftg/releases/<release ID>/.complete`, empty, as the true last step: a release
   directory with no `.complete` file is always incomplete, and rule 2's own cleanup removes it
   on the next tick, whether or not this tick ever gets that far. The combined hash in
   `.sha256` is what rule 2 recomputes and compares before it reuses a release on a revert,
   rather than trusting `.complete` alone. None of this covers a build that passes the gate but
   fails once it is actually running (an import error, a crash after start), which rules 6 and
   7 cover.
4. The gate is stronger than running `ftg-prober check-config` alone, and it does everything
   `cmd_run` does up to and including the `Scheduler` construction and `scheduler.select()`,
   stopping 1 line before `run.start`: nothing through that point makes a network call, on
   either side of that line.
   `check-config` validates the file against spec 0003 section 3 and requires neither cloud
   section to exist ([`cmd_check_config`](../prober/ftg_prober/cli.py)); `cmd_run` additionally
   calls `config.section(args.provider)`, which raises a config error when that section is
   missing, `load_probes(packages_for(args.provider))`, which imports and constructs every
   probe plugin of that provider, and then constructs the `Scheduler` and calls `select()`
   ([`cli.py`](../prober/ftg_prober/cli.py)). A config with no `aws:` section passes
   `check-config` today but makes `run --provider aws` exit 2 the moment it reaches that first
   call. The gate `sync.sh` runs must therefore do what `cmd_run` does before its first network
   call, not only what `check-config` does. This needs a small extension to the prober CLI, in
   the implementation pull request, for example a `--provider` flag on `check-config` that runs
   these same extra steps. Passing the gate makes a bad config much less likely to reach the
   restart of rule 5, but it does not prove the restart will succeed: no static check can prove
   every probe's first real call will not fail for an unrelated reason. `run_probe` already
   catches each probe's own exception and logs `probe.failed` without stopping the process
   ([`scheduler.py`](../prober/ftg_prober/scheduler.py)), so a single probe's own failure does
   not, by itself, fail rule 6's health check: the health check catches a narrower thing, a
   build that never settles into the steady, running state rule 6 checks for, for example a
   failure before `run.start` ever logs, or a crash outside `run_probe`'s own catch, or (rule 6)
   a build where every probe runs and fails.
5. Once the gate of rule 4 passes, `sync.sh` first checks whether `current` exists, **and**, if
   it does, whether it already points at this same release ID: an interrupted sync can reach
   this point a 2nd time for the same manifest, for example a crash between a previous run's
   swap and its manifest-cache write (rule 8), which the next tick repeats from rule 1 onward.
   Only when `current` exists and points somewhere else does `sync.sh` read that existing
   target and write it to `/etc/ftg/previous-release`, replacing whatever that file already
   held: this is the 1 place "the previous release" is defined, and it is defined at the moment
   of an actual swap, not before, and not on a swap that would not change anything. If
   `current` already points at this release ID, `sync.sh` leaves `/etc/ftg/previous-release`
   untouched and skips straight past the symlink swap below to rule 6, because there is nothing
   to swap: writing `current`'s own current target back into `previous-release` here would
   wrongly make "the previous release" and "the current release" the same value. If `current`
   does not exist at all yet, this is the first boot (rule 11): `sync.sh` writes no
   `/etc/ftg/previous-release` file at all, rather than a placeholder value, because there is
   no release to name. Otherwise, `sync.sh` points
   `/opt/ftg/current` at the new release with 1
   atomic symlink swap: `ln -s /opt/ftg/releases/<release ID> /opt/ftg/releases/.next-<pid>`,
   then `mv -T /opt/ftg/releases/.next-<pid> /opt/ftg/current`, 1 call to
   [POSIX `rename()`](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html),
   atomic on the same filesystem, which replaces the old symlink in 1 step,
   never a moment with no `current` at all. Swapping a symlink moves nothing but a pointer, so
   it does not reintroduce the problem rule 3 avoids for the venv itself. It does not yet write
   the new manifest to the cache. `ExecStart` in the systemd unit points at
   `/opt/ftg/current/venv/bin/ftg-prober` and `--config /opt/ftg/current/config/<the config file
   of the stack>` (AWS: always `targets.yaml`; Azure: `local.config_file`, the basename of
   `var.targets_file`, which a deployer can set to a different name), never at a release path
   directly, so the unit file itself never changes across a release.
6. `sync.sh` runs `systemctl enable ftg-prober.service` (idempotent; a no-op once already
   enabled) just before `systemctl restart ftg-prober.service`, every time, not only the first.
   The boot script writes the unit file but does not itself run `enable --now` the way it does
   today, because `ExecStart` names a `current` path that does not exist until rule 5's first
   swap; `ftg-prober.service` is not enabled, and nothing has started it, before the first
   `sync.sh` run reaches this rule. After that first success, the unit is enabled, so a later
   plain reboot of the VM (not a replacement) starts it again on its own, without waiting on
   the timer. `sync.sh` then watches for a window it derives
   from the config it just gated (rule 4), not a number fixed in `sync.sh` itself: the shortest
   `cadence` or `interval` among the probes or jobs that config actually runs for this
   provider, plus a 1-minute margin, capped at a stated maximum (15 minutes). A config whose
   fastest probe is the 5-minute signal probes most live deploys run today
   (`aws_spot_score`/`aws_spot_price`, spec 0008 section 1; `az_spot`, spec 0009 section 1)
   gets a 6-minute window, covering 1 full cycle of that probe. A config with no probe at or
   under the 15-minute cap, for example a deploy that runs only the 2-hour Capacity Block
   probes, gets the capped 15-minute window instead, and the last of the 4 checks below, the
   one that needs a probe cycle to complete inside the window, is skipped for that config: 15
   minutes is a long time to leave `sync.sh` waiting idle for a signal that config's own cadence
   cannot deliver that fast. A `run.start` line plus a crash a moment later must count as a
   failure, so the check must hold for the whole window, not flash true once right after the
   restart: `sync.sh` polls, every few seconds for the window, 3 or 4 things together (4 when a
   probe fits the cap, else 3), and the health check passes only if all of them still hold at
   the end of the window, not only at some point during it:
   - `systemctl is-active ftg-prober.service` reports `active`,
   - the journal has the prober's own `run.start` log line
     ([`cli.py`](../prober/ftg_prober/cli.py)), which `cmd_run` logs only after
     `config.section`, `load_probes`, and the `Scheduler` construction have all already
     succeeded, and before it makes its first probe call. `sync.sh` reads the new invocation ID
     right after the restart (`systemctl show -p InvocationID --value ftg-prober.service`; `Unit`
     interface, [`org.freedesktop.systemd1(5)`](https://manpages.ubuntu.com/manpages/noble/man5/org.freedesktop.systemd1.5.html))
     and greps the journal for that 1 invocation
     (`journalctl _SYSTEMD_INVOCATION_ID=<that ID>`,
     [`systemd.journal-fields(7)`](https://manpages.ubuntu.com/manpages/noble/man7/systemd.journal-fields.7.html)),
     so a `run.start` line the unit logged before this restart, from an earlier run, never
     counts as the new build's own,
   - `systemctl show -p NRestarts --value ftg-prober.service` still reads `0` (`Service`
     interface, [`org.freedesktop.systemd1(5)`](https://manpages.ubuntu.com/manpages/noble/man5/org.freedesktop.systemd1.5.html)).
     `sync.sh`'s own restart in this rule is a manual restart, which resets this counter to 0:
     `service_start()` in systemd's own source does `s->n_restarts = 0`, with the comment
     "This is not an automatic restart? Flush the restart counter then."
     ([`src/core/service.c`](https://github.com/systemd/systemd/blob/main/src/core/service.c)).
     A value above 0 before the window ends means the unit's own `Restart=` policy
     ([`systemd.service(5)`](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html);
     spec 0004, section 6; `cloud_init.yaml.tftpl`) fired at least once on its own since, which
     only happens on a crash, **and**
   - only when some probe's cadence fits the 15-minute cap: the new invocation's journal has at
     least 1 `probe.done` line, meaning at least 1 probe ran this window and returned without
     raising ([`scheduler.py`](../prober/ftg_prober/scheduler.py)). An active unit, a logged
     `run.start`, and `NRestarts` still at `0` are all still true of a build where every single
     probe fails its own call and only ever logs `probe.failed`: nothing in the first 3 checks
     notices that, because `run_probe` already turns every probe failure into a caught
     exception and a log line, never a crash (rule 4). This 4th check is what catches it: no
     `probe.done` line in the whole window, with 1 or more `probe.failed` lines instead, is also
     a failed build. When no probe's cadence fits the cap, this decision accepts the gap, rather
     than waiting arbitrarily long for a slower probe's first cycle: a config of only slow
     probes can still pass the health check on a build where every one of them silently fails,
     until that probe's own cadence is due and someone reads its rows, or lack of them.

   A restart that `NRestarts` reports is not on its own proof the new build is bad: something
   else on the VM could restart the unit in the same window for an unrelated reason. Section 6
   gives the operator a way to force a retry when this check was wrong. `sync.sh` can stop
   early and call it a failure the moment any of these checks first fails; it cannot call the
   build healthy before the full window passes with all of them still true.
7. If the health check of rule 6 fails, `sync.sh` first reads `/etc/ftg/previous-release` and
   compares it to the release that just failed (`current`'s target at this point, the release
   rule 6 just ran the health check against). If they are the same release ID, a rollback would
   only restart the exact build that just failed: `sync.sh` skips the rollback restart below
   entirely and goes straight to the terminal failure branch, as if the rollback itself had
   failed, because there is nothing left to roll back to that is not this same build. This
   guards the interrupted-sync case of rule 5: the skip-past path there (`current` already at
   the release being gated) never advances `previous-release`, so a 2nd failure of that same,
   already-current release must not pretend a different release is available.

   Otherwise, `sync.sh` rolls back: it swaps the `current` symlink to the release ID
   `/etc/ftg/previous-release` names, with the same atomic `ln -s` then `mv -T`, **without**
   touching `/etc/ftg/previous-release` itself this time (unlike rule 5, the rollback does not
   advance it; it must keep pointing at the last known-good release, not at the one that just
   failed), and runs `systemctl restart ftg-prober.service` again to start the previous build.
   `sync.sh` then runs the same health check of rule 6 on this restart too, over the same
   window: a rollback is itself a restart, with the same chance of landing on a broken unit,
   for example 1 that another change left in a bad state since the last time it ran, so rule
   6's check applies to it unconditionally, not only to a forward update.
   - If the rollback's own health check passes, `sync.sh` deletes the failed release's own
     directory, but only after it confirms that release ID is not the current value of
     `current` or of `/etc/ftg/previous-release`: it already proved bad, and nothing later
     reuses it, because the next step blacklists its manifest too, and it never matches
     either of those 2 names at this point, but `sync.sh` checks instead of assuming so, and
     never deletes a release directory either of them names, under any path in this design. It
     writes the failed manifest's content, not the release
     directory, to a separate marker file, `/etc/ftg/manifest.failed`, and does not write the
     new manifest to the cache. Any of rule 3's own failures, a sha256 mismatch, a failed
     `pip install`, a failed gate, or rule 3's download-failure limit, writes this same marker:
     whichever check catches a bad manifest, it is blacklisted the same way, not only a health-check
     failure. `sync.sh` logs the failure, so an operator reading the log (section 6) sees why
     the VM is still on the previous build, and a failed build this way costs 2 restarts in
     total: the one that started it (rule 6) and the one that starts the rollback, each its own
     queue-loss event (the restart-effects table earlier in this decision). The next tick of
     the timer (rule 12) fetches the manifest as rule 1 already does; if it still matches
     `/etc/ftg/manifest.failed`, `sync.sh` does nothing further that tick, rather than
     retrying the same failing manifest on every tick forever. It retries the whole sequence
     from rule 2 again only once the fetched manifest no longer matches the marker, which means
     a new deploy changed it, **or** an operator clears the marker by hand (section 6) to force
     a retry of a manifest this check only failed by a false positive.
   - If the rollback's own health check also fails, or the guard above skipped the rollback
     restart entirely because `previous-release` already named the same build that just
     failed, `sync.sh` stops: it leaves `current` exactly where it is (there is no 3rd release
     to fall back to further), writes a separate marker, `/etc/ftg/rollback.failed`, with the
     manifest's content (and, when a rollback restart did run, the earlier release it could not
     bring back healthy either), logs the failure, and exits non-zero. This failure is not
     retried on a later tick the way a 1-sided failure is: both the new build and the release
     this VM was already running are now suspect, and nothing in `sync.sh` can tell which one,
     if either, is actually fine, so it stops rather than guessing. `ftg-prober-sync.service` then shows
     as a failed unit, which `systemctl --failed` lists on this VM over the same session access
     as section 6; the journal (`journalctl -u ftg-prober-sync`) carries the same detail a
     passing rollback would have logged, for both restarts. Recovery is manual: an operator
     reads the journal, decides what is actually wrong, and either fixes it and deploys a 3rd
     manifest (which starts rule 2 again, fresh, the next tick), or reaches the VM directly
     (the SSM Run Command or Azure Run Command path of section 6) to install a known-good build
     by hand. `sync.sh` does not try a 2nd rollback target on its own, because this decision
     keeps only 2 releases (rule 9); inventing a 3rd kept release to guess further back is the
     hot-reload-adjacent feature creep this decision avoids elsewhere (the Risks section). A
     failed unit must stay failed: the timer (rule 12) still fires on its own interval after
     this, so on every later tick, `sync.sh` checks for `/etc/ftg/rollback.failed` right after
     rule 1's fetch and before rule 2, **and exits non-zero again if it is present even when
     rule 1's own fetch fails or, on Azure, returns `BlobNotFound`**: a tick that cannot read
     the manifest at all has no way to tell whether a new deploy arrived, so it must assume the
     unit is still the one `rollback.failed` named and stay failed, not silently succeed because
     this tick happened to do nothing. When the fetch does return a manifest, and it matches the
     content that file holds, `sync.sh` exits non-zero again at once, without running rule 2
     onward, so this tick's fresh start does not quietly clear the failed unit the way a plain
     exit 0 would. If the fetched manifest no longer matches, a new deploy changed it: `sync.sh`
     removes `/etc/ftg/download-failing-since` first, if present, so this new manifest's own
     download starts its own clock rather than reading a time left over from whatever manifest
     `rollback.failed` named, then proceeds with rule 2 as usual, the same reasoning this rule
     already applies to `/etc/ftg/manifest.failed`. The file is never removed by `sync.sh` on
     its own; an operator who reaches the VM directly can remove it by hand instead of waiting
     for a 3rd manifest.
   This rollback is the same on both clouds, but the failure it catches looks different without
   it: the AWS unit's `Restart=on-failure` plus `RestartPreventExitStatus=2` (spec 0004,
   section 6) stops a config error outright, while the Azure unit's `Restart=always`, with no
   `RestartPreventExitStatus` (`cloud_init.yaml.tftpl`), restarts a crashing build every
   `RestartSec` (30 seconds) indefinitely instead of stopping. The health check of rule 6
   treats both the same way: a unit that is not `active`, that never logs a matching
   `run.start` line, whose `NRestarts` climbs above 0, or (when it applies) that never logs a
   `probe.done` line, before the window ends, is a failed build either way, whether the unit
   then sits stopped or loops.
8. If the health check of rule 6 passes, `sync.sh` writes the new manifest to the cache and logs
   the release ID and the keys it just installed. The log line is the version an operator reads
   after an update, over the same session access used today.
9. `sync.sh` keeps the release that `current` points to and the 1 release before it, and
   deletes any older release directory once a newer one is confirmed healthy by rule 8, so a
   rollback in rule 7 always has a previous release to swap back to, and `/opt/ftg/releases/`
   does not grow without bound.
10. If the manifest is unchanged, `sync.sh` does nothing, except that it removes
    `/etc/ftg/download-failing-since` first if that file exists (rule 3): the manifest that
    matches the cache is the 1 already running, so any earlier download-failure streak is over,
    whether it was for this same manifest's own install (long since completed) or for a
    different one that has since been abandoned.
11. The first boot runs `systemctl start --no-block ftg-prober-sync.service` once, from
    `runcmd` (Azure) or the end of the boot script (AWS), rather than calling `sync.sh`
    directly: systemd runs only 1 instance of a service unit at a time, so this first run and
    the timer's own first tick (rule 12) cannot both invoke `sync.sh` at once. `--no-block`
    (optional) lets the rest of the boot script finish without waiting out rule 6's window
    (up to 15 minutes, rule 6) first. The cache does not exist yet, so this first run always
    installs, and there is
    no previous release to roll back to; `/etc/ftg/previous-release` does not exist yet either.
    This keeps 1 code path for the first install and every later update, instead of 2. The
    first boot still runs rule 5's swap (so `current` does exist, pointing at the new release,
    by the time rule 6's health check runs) and the health check of rule 6 itself, but rule 7's
    rollback has nothing to swap back to. On a first-boot failure, `sync.sh` runs
    `systemctl stop ftg-prober.service` (the health
    check may have left it `active`, or looping, or stopped already; this makes the end state
    certain either way), removes the `current` symlink rule 5 had just created, and deletes the
    failed release's own directory, the same choice rule 7 makes for the same reason: nothing
    else in this design ever prunes it otherwise, since rule 9's pruning only runs after a
    healthy build, which this VM may never get. `sync.sh` writes the failed-manifest marker the
    same as rule 7, writes no `/etc/ftg/previous-release` file (there was never a previous
    release to name), logs the failure, and exits non-zero: `ftg-prober-sync.service` itself
    then shows as a failed unit, a visible signal that the first boot never reached a running
    prober at all, rather than silently waiting for the next tick to notice. This is a
    distinct branch from rule 7's rollback, not a variant of it, because there is no earlier
    build to fall back to. Rule 3's repeated-download-failure limit counts as a first-boot
    failure too: with no `ftg-prober.service` ever started on this VM, there is nothing for
    `systemctl stop ftg-prober.service` to do, but `sync.sh` still removes the empty release
    directory of rule 2, writes the failed-manifest marker and logs
    `sync.download_failed_repeatedly` as rule 3 states, and exits non-zero, so
    `ftg-prober-sync.service` shows as a failed unit the same way any other first-boot failure
    does. A failed unit must stay failed here too: the timer (rule 12) still fires on its own
    interval after a first-boot failure, so on every later tick, `sync.sh` checks, right after
    rule 1's fetch and before rule 2, whether `/opt/ftg/current` is absent and the fetched
    manifest matches the content of `/etc/ftg/manifest.failed`. `sync.sh` exits non-zero again
    if `/opt/ftg/current` is absent and `/etc/ftg/manifest.failed` exists, **even when rule 1's
    own fetch fails or, on Azure, returns `BlobNotFound`**, the same reasoning rule 7 applies:
    a tick with no manifest to compare cannot prove a new deploy arrived, so it must assume
    this VM never got past its first boot and stay failed. When the fetch does return a
    manifest and it still matches, this is the same first-boot failure: `sync.sh` exits
    non-zero again at once, without running rule 2 onward, instead of a fresh start quietly
    clearing the failed unit. If the manifest no longer matches, a new deploy changed it:
    `sync.sh` removes `/etc/ftg/download-failing-since` first, if present, for the same reason
    rule 7 does, then `current`'s absence correctly drives a fresh first-boot-style install
    through rule 2 onward, the same as rule 7's matching check already does for a build that
    failed after an earlier success.
12. A new systemd timer, `ftg-prober-sync.timer`, runs `ftg-prober-sync.service` (not `sync.sh`
    directly) on an interval (for example every 5 minutes) after boot, the same unit rule 11
    starts by hand, so the 2 triggers share systemd's own guarantee of 1 instance at a time. The
    interval is a setting of the timer unit, not a new Terraform variable. Where an egress proxy
    is set (`var.egress_proxy_url` on AWS, `egress_proxy_url` on Azure), `ftg-prober-sync.service`
    gets the same proxy drop-in as `ftg-prober.service` itself, on both clouds (AWS:
    `user_data.sh.tftpl`'s `/etc/systemd/system/ftg-prober.service.d/proxy.conf`; Azure: the
    equivalent `.service.d/proxy.conf` the stack already writes for `ftg-prober.service` in
    `cloud_init.yaml.tftpl`), because `sync.sh` itself calls `aws s3api`/`fetch-blob.sh` and
    `pip install` through the same proxy, and `install.sh`'s own shell exports (Azure) do not
    reach a unit systemd invokes on its own. This decision accepts 1 risk around the clock
    rule 3's `download-failing-since` reads: a forward correction of more than 1 hour, applied
    to the wall clock sometime soon after boot, can trip rule 3's limit on the very next tick
    that happens to be mid-failure, even though real time has not actually passed. The negative-
    gap rule of rule 3 only catches a correction the other direction.

### 5. The IAM it needs

Both roles already read the `code` prefix or container; the manifest adds 1 object, not a new
prefix or a new permission type:

- **AWS, the read grant**: `aws_iam_role_policy.prober`'s `ReadCode` statement (`iam.tf`) turns
  `Resource` into a list of 2 ARNs: the existing `"${aws_s3_bucket.data.arn}/code/*"` and the
  new `"${aws_s3_bucket.data.arn}/manifest.json"`. The action stays `s3:GetObject`, read-only,
  as today. No `s3:ListBucket` grant is added: `manifest.json` is never a missing key on AWS
  (section 3), so rule 1 of section 4 has no 404 to tell apart from any other fetch error.
- **Azure**: no change. `azurerm_role_assignment.prober_code_reader` (`rbac.tf`) already grants
  "Storage Blob Data Reader" on the whole `code` container
  (`azurerm_storage_container.code.id`), which already covers the new `manifest.json` blob.

- **AWS, a security fix this design needs**: the role's `WriteRecords` statement grants
  `s3:PutObject` on `"${aws_s3_bucket.data.arn}/*"` (`iam.tf`) today, every key in the bucket,
  which already covers `manifest.json` and every key under `code/`. That grant was loose but
  harmless before this design, because nothing read those keys back to install and run
  anything. It is not harmless with a manifest and a sync timer: a process on the VM that can
  call `s3:PutObject` can overwrite `manifest.json`, point it at a pair of attacker-written zips
  (the same grant can write those too), and have the VM's own `ftg-prober-sync.timer` install
  and run that code as root within 1 timer interval. The fix is a new, explicit statement with
  `Effect = "Deny"` on `s3:PutObject` and `s3:DeleteObject`, `Resource` = `manifest.json` and
  `code/*`, added to the same policy. An explicit `Deny` wins over any `Allow` in the same
  policy, no matter how `WriteRecords`'s own `Resource` is written, so this does not need to
  narrow `WriteRecords` itself or track the prober's data key layout: [ADR 0010](0010-one-experiment-file-of-modules-and-jobs-with-a-billable-guard.md)
  (accepted, not implemented yet) moves that layout from `run_mode=*/` to `experiment=*/`, and
  the `Deny` keeps working unchanged either way, because it names the 2 keys to protect, not
  the keys to allow.
- **Azure is not affected**: `azurerm_role_assignment.prober_data_writer` (`rbac.tf`) already
  scopes its write role, "Storage Blob Data Contributor", to `azurerm_storage_container.data.id`,
  the `data` container only. The `code` container, which holds the new manifest blob, is a
  separate container; the prober's write role has no grant on it, scoped or not.

### 6. How an operator triggers or sees an update

An operator runs the same command as today, `make plan/<cloud>` then `make apply/<cloud>`. Terraform
uploads the 2 new zips and rewrites `manifest.json` (or the Azure blob of the same name). The
apply no longer touches the VM resource, so there is no replacement and no downtime at apply
time. The VM's own timer notices the new manifest within its interval and does the pull and the
restart on its own; the only downtime is that restart, a few seconds.

An operator who wants the update sooner than the next timer tick can use the access path the
stack already has: AWS SSM Run Command (`var.enable_ssm`) or Azure Run Command, to run
`systemctl start ftg-prober-sync.service` once. This is an existing access path used for 1 more
command, not a new one.

An operator sees that the update ran by reading the unit's own log
(`journalctl -u ftg-prober-sync`) over the same session access used today, or by watching rows
resume in the data store after the restart.

Section 4, rule 6's health check can be wrong: a restart that `NRestarts` reports in the window
can come from something unrelated to the new build. When an operator reads the log and
confirms the build was actually good, they clear the block by hand, over the same access path:
`rm /etc/ftg/manifest.failed`, plus `rm -f /etc/ftg/download-failing-since` (it may not exist,
when the marker came from a sha256, `pip install`, or gate failure rather than a download),
then `systemctl start ftg-prober-sync.service` once, to force a retry of the same manifest
right away, rather than waiting for a new deploy to change it. Removing
`download-failing-since` (section 4, rule 3) too matters here: without it, the forced retry's
first download attempt would read a time stamp over 1 hour old and trip the limit again
immediately, instead of starting its own clock fresh.

## Evidence

- [`aws_instance`: `user_data_replace_on_change` forces a replacement on a user data change](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/instance)
- [`azurerm_linux_virtual_machine`: "Changing this forces a new resource to be created" for `custom_data`](https://registry.terraform.io/providers/hashicorp/azurerm/4.81.0/docs/resources/linux_virtual_machine)
- [`azurerm_storage_blob`: `source_content` "Changing this forces a new resource to be created"](https://github.com/hashicorp/terraform-provider-azurerm/blob/main/website/docs/r/storage_blob.html.markdown)
- [`aws_s3_object`: `bucket` and `key` are the only `ForceNew: true` fields in the resource
  schema; `content`, `source`, and `source_hash` are not](https://github.com/hashicorp/terraform-provider-aws/blob/main/internal/service/s3/object.go)
- [`org.freedesktop.systemd1(5)`: `NRestarts` (the `Service` interface) and `InvocationID`
  (the `Unit` interface), the D-Bus properties `systemctl show` reports](https://manpages.ubuntu.com/manpages/noble/man5/org.freedesktop.systemd1.5.html);
  [`systemd.service(5)`: `Restart=`](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html);
  [`systemd.journal-fields(7)`: `_SYSTEMD_INVOCATION_ID`](https://manpages.ubuntu.com/manpages/noble/man7/systemd.journal-fields.7.html)
- [systemd `service_start()`, `src/core/service.c`: "This is not an automatic restart? Flush
  the restart counter then."](https://github.com/systemd/systemd/blob/main/src/core/service.c)
- [POSIX `rename()`: atomic on the same filesystem](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html)
- Spec 0003, section 6A (the call queue) and section 6, rule 4 (rows written right after each
  call, never held for a later write).
- Spec 0008, section 4.4, rule 5 (the Spot score freeze ends only when the process stops).
- Spec 0009, section 2.6, rule 14 (the Azure 429 pause ends the same way).
- `prober/ftg_prober/callqueue.py` (the in-memory `CallQueue` and `TokenBucket`).
- `prober/ftg_prober/cli.py` (`cmd_check_config` validates the file only; `cmd_run` additionally
  calls `config.section(args.provider)` and `load_probes`, and logs `run.start` only once both
  have already succeeded).
- [`create_before_destroy` and a dependent resource: "the creation of `B` is inserted in
  between the creation of `A` and the destruction of `A`"](https://github.com/hashicorp/terraform/blob/main/docs/destroying.md)
- [IAM policy evaluation logic: an explicit `Deny` always overrides an `Allow`](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html)

## Risks

- A manifest the VM cannot parse (a bad apply, a half-written object) stops the sync without
  stopping the already-running prober: `sync.sh` must fail closed, log, and leave the current
  install and the current `ftg-prober.service` running. This is the same shape as the existing
  `retry()` pattern in `user_data.sh.tftpl` and needs no new design.
- Without the `create_before_destroy` setting of section 3, Terraform's default replace order
  is destroy-then-create: it would delete the object or blob at the old key before creating the
  one at the new key, so the still-live, not-yet-updated manifest could briefly name a key that
  no longer exists, on both clouds. Section 3's `lifecycle { create_before_destroy = true }` on
  the 4 zip resources (2 AWS objects, 2 Azure blobs), together with the manifest's existing
  `depends_on` on them, closes this: Terraform defers each old key's destroy until after the
  manifest that could reference it has already been rewritten to the new keys. This closes the
  gap fully on AWS, where `manifest.json` itself updates in place (section 3) and so always
  exists. On Azure, `manifest.json` itself still has a brief gap on every such apply, because
  its own `source_content` forces its own replace (section 3); rule 1 of section 4 is the fix
  for that gap, not the zips' `create_before_destroy`, which only keeps what the manifest names
  correct once it exists again. A `sync.sh` read can still get a 404 for an unrelated reason (a
  transient network fault), which the existing `retry()` pattern already covers.
- A build that fails a sha256 check, the gate, or the health check does not retry forever: rule
  7 of section 4 records the failed manifest and skips it until a new deploy changes it, or an
  operator clears the marker by hand (section 6), so 1 failed build costs 2 restarts total (the
  attempt and the rollback), each its own cycle's worth of queue loss (the table above), not 1
  restart and 1 loss on every timer tick until someone intervenes.
- This decision does not persist the call queue (option 4) or add a config hot reload
  (option 2). Both stay out, on the evidence in the table above: with rule 7's retry limit, the
  loss a restart causes is bounded to 2 cycles' worth of queue (the failed attempt's restart,
  then the rollback's) per failed update, and 1 cycle's worth per real one, not an unbounded,
  repeating loss, and nothing already written is at risk. A hot reload is the subject of the
  later change the project asked for.

## Consequences

- `terraform/aws/main.tf`, `terraform/aws/s3.tf`, `terraform/aws/iam.tf`,
  `terraform/aws/user_data.sh.tftpl`, and the equivalent Azure files (`control.tf`,
  `cloud_init.yaml.tftpl`) change in the implementation pull request. This decision records the
  target shape; it does not change those files.
- Spec 0003 (section 6A), spec 0004 (the AWS stack), and spec 0005 (the Azure stack) gain the
  manifest object, the sync script, the releases layout, the `ExecStart` path, and the timer, in
  the implementation pull request.
- `prober/ftg_prober/cli.py` gains the stronger gate of section 4, rule 4 (for example a
  `--provider` flag on `check-config`), in the implementation pull request.
- A later change covers a hot reload with no restart, for adding or removing probes or changing
  the config, as the project asked. A separate, later change covers detecting a stale build
  before it is zipped (Context).
- `ftg-prober.service`'s `TimeoutStopSec` (systemd default 90
  seconds, [`systemd.service(5)`](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html))
  must be at least as long as the slowest teardown an in-flight probe can need, so rule 6's
  `systemctl restart` does not reach that timeout and SIGKILL a probe mid-teardown, for example
  an AWS allocation probe cleaning up a reservation (spec 0002, section 4.9; ADR 0009). The
  bound comes from the config, so it ships with the release: before each restart, `sync.sh`
  writes `TimeoutStopSec=max(180, probe timeout + teardown timeout + 30)` to a systemd drop-in
  and runs `systemctl daemon-reload`. The value is never in the user data or the custom data,
  so it does not replace the VM.
- A later rule for `ftg-prober-sync.service` (for example the revert on a kill) also goes in a
  systemd drop-in that `sync.sh` writes, as for the stop timeout, not in the unit file: a change
  to the unit file replaces the VM. This helps only a VM that already runs a `sync.sh` that
  writes the drop-in. The rules that exist today stay in the unit file.
- FYI, unrelated to this decision: Terraform's own docs call this "Forced Create Before
  Destroy": "all resources that precede a resource with `create_before_destroy` must in turn
  be handled in the same manner"
  ([`docs/destroying.md`](https://github.com/hashicorp/terraform/blob/main/docs/destroying.md)),
  to avoid a cycle in the graph. The 4 zip resources' own dependencies, the AWS bucket and the
  Azure storage account and container they sit in, would need the same setting if either were
  ever replaced in the same apply as a zip. Today neither is: nothing in either stack gives the
  bucket, the storage account, or the container a reason to replace.

## Tests the implementation must have

This decision has gone through enough review that the edge cases are now known; the
implementation pull request's test suite is what closes this design record, not more text
here.

- First boot, a good build: `current` ends up set, the manifest is cached, and the journal has
  both `run.start` and (cadence permitting) `probe.done` for the new invocation.
- First boot, a bad config (fails the gate): the sync unit exits non-zero and shows failed;
  `current` never gets created; `manifest.failed` is written.
- First boot, passes the gate but fails the health check (rule 11's branch, not rule 7's): same
  failed-unit outcome as the bad-config case, with no release to roll back to.
- A normal update, good build: `/etc/ftg/previous-release` is set to the old `current` target
  at the swap, not before; the manifest is cached only after the health check passes; rule 9
  keeps exactly 2 releases and prunes the rest.
- A normal update that fails the sha256 check, a failed `pip install`, or the gate: `current`
  and the running service are untouched; `manifest.failed` is written in every one of these
  cases, not only a health-check failure.
- A normal update with a failed zip download that heals before 1 hour passes:
  `/etc/ftg/download-failing-since` is written on the first failure, removed on the next
  successful download, `current` and the running service are untouched, and no
  `manifest.failed` is written.
- A normal update whose zip download keeps failing for 1 hour or more since
  `download-failing-since` was first written: `current` and the running service are untouched,
  `manifest.failed` is written, and the journal carries a `sync.download_failed_repeatedly`
  event with the recorded failing-since time and the last download error as returned.
- 2 different bad manifests whose downloads fail on alternating ticks: `download-failing-since`
  is written on the first failure and is not reset by the 2nd manifest's failure, so the limit
  still trips after 1 hour of unbroken failure, even though no single manifest sha256 failed on
  every tick.
- A simulated crash that lands exactly when now minus `download-failing-since` equals 1 hour:
  the `>=` comparison trips the limit on this tick, not only on a later one, so a crash timed to
  land on the boundary cannot skip it the way a strict `>` or an exact-count `==` could.
- A simulated crash right after `sync.sh` decides the limit is reached but before it writes
  `manifest.failed`: `download-failing-since` is still on disk, unchanged, so the next tick
  recomputes the same elapsed time, finds it still `>=` 1 hour, and writes the marker then,
  with no in-memory count to have lost.
- `download-failing-since` exists but is unreadable (a truncated write from an earlier crash):
  `sync.sh` logs this, treats it as a failure starting now, and overwrites it with the current
  time, rather than failing the tick or trusting a corrupt time stamp.
- `download-failing-since` reads a time after now (a simulated backward clock jump): `sync.sh`
  logs this and rewrites it with now, the same as an unreadable file, instead of waiting out a
  negative gap.
- A manifest that already tripped the limit and is now named in `manifest.failed` is replaced
  by a genuinely new manifest: rule 7 (or rule 11, on first boot) removes the stale
  `download-failing-since` the old manifest left behind before letting the new one through, so
  the new manifest's own first download failure starts its own clock and does not trip the
  limit immediately from a time stamp that is already over 1 hour old.
- A download-failure streak that ends with a revert to the manifest already in the cache,
  short of either hitting the limit or an operator's clear: rule 10 removes the leftover
  `download-failing-since` on the tick that matches the cache, so a later manifest's own first
  download failure, whenever it happens, does not trip the limit immediately from a time stamp
  that this revert retired.
- First boot whose zip download keeps failing for 1 hour or more: the empty release directory
  of rule 2 is removed, `manifest.failed` is written, and `ftg-prober-sync.service` exits
  non-zero and shows as a failed unit, the same as any other first-boot failure, even though no
  `ftg-prober.service` was ever started to stop.
- An operator's `rm /etc/ftg/manifest.failed` also removes `/etc/ftg/download-failing-since`:
  the forced retry's first download attempt starts its own clock instead of reading a time
  stamp already over 1 hour old and tripping the limit again immediately.
- `ftg-prober-sync.service` already shows `rollback.failed`, or already shows `manifest.failed`
  with no `current` from a first-boot failure: a later timer tick exits non-zero again without
  rebuilding, so the unit stays shown as failed across ticks, rather than a fresh start
  quietly clearing it. This holds even when that later tick's own manifest fetch fails, or, on
  Azure, returns `BlobNotFound`: with no fetched manifest to compare, `sync.sh` still exits
  non-zero rather than treat a fetch error as proof a new deploy arrived.
- A normal update that passes the gate but fails the health check: the rollback restart runs,
  and the same health check (rule 6) runs again on it.
- The rollback's own health check passes: `current` ends on the previous release,
  `previous-release` is unchanged (still the one before that), the failed release's directory
  is deleted, and the next tick skips the same manifest until it changes or an operator clears
  the marker.
- The rollback's own health check also fails: `current` stays on the previous release,
  `rollback.failed` is written, the sync unit exits non-zero and shows failed, and no further
  tick retries automatically.
- `sync.sh` is killed during the rollback's own health-check window, after the rollback's
  symlink swap and before `manifest.failed` or `rollback.failed` is written: `current` is left
  on the old release, and the cache still holds the old manifest, not the one the rollback was
  testing. The next tick fetches that same manifest again, finds it matches neither the cache
  nor `manifest.failed`, and treats it as new: rule 2 reuses the existing, complete failed
  release instead of rebuilding it, and the gate and the swap set `previous-release` again. The
  run then ends the same way an uninterrupted one would.
- A build that fails is retried once a new manifest replaces the blacklisted one, and also once
  an operator clears `manifest.failed` by hand and starts the sync unit directly.
- A revert to a manifest whose release directory is still present, complete, and passes the
  `.sha256` recheck: the fetch and the install are skipped; the gate and the swap still run.
- The same revert, but the release directory's `.sha256` recheck fails (simulated corruption):
  a full rebuild runs instead of a reuse.
- An install killed mid-build leaves a release directory with no `.complete`: the next tick's
  cleanup removes it before any retry.
- Azure: a sync poll that lands during the brief window where `manifest.json` itself does not
  exist (mid-apply) gets `BlobNotFound` and is treated as unchanged, not a failure.
- On AWS, every manifest fetch error (a network fault, an expired token, `AccessDenied`) is
  logged as it is returned and retried next tick, never written to `manifest.failed`. On Azure,
  the same holds for a fetch error that is not `BlobNotFound`.
- A config whose fastest job is slower than the 15-minute cap: the health check window is the
  capped 15 minutes, and the `probe.done` check does not run.
- The first-boot start and the timer's own first tick cannot both run `sync.sh` at once
  (systemd's single-instance guarantee for `ftg-prober-sync.service`).
- The prober's own unprivileged user cannot write under `/opt/ftg/releases`, `/opt/ftg/current`,
  or `/etc/ftg` (a permission check, or on Azure, that `ReadWritePaths=` is not set for these).
- A `terraform test` (mirroring the existing `egress_proxy`/`existing_network` fixtures) that
  the explicit `Deny` of section 5 blocks `s3:PutObject` on `manifest.json` and `code/*` even
  though `WriteRecords`'s own `Resource` still matches them.
- An interrupted sync that lands back on a manifest `current` already names: the swap is
  skipped, `/etc/ftg/previous-release` is left untouched, and the restart still runs and is
  still health-checked.
- A health check failure on that same, already-current release: `previous-release` already
  equals the release that just failed, so `sync.sh` skips the rollback restart entirely and
  goes straight to the terminal `rollback.failed` branch.
- Before any release directory delete, in rule 7 or in rule 11's first-boot branch: the
  directory named is never the one `current` or `previous-release` points to at that moment.
- Azure: `fetch-blob.sh manifest.json <output file>`'s own exit code tells "not found" apart
  from every other failure.
- `ftg-prober.service` is not enabled before the first successful sync (no unit to enable
  usefully, nothing to run yet); it is enabled after, so a plain VM reboot starts it again
  without waiting for the timer.
