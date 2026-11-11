# ftg-preflight

Read-only checks before a deploy, and the Azure GPU quota requests ([Request GPU quota](#request-gpu-quota)). The checks find a gap in the inputs, the network, the access, or the quota before a plan or apply fails. The specs are [spec 0004, section 12](../spec/0004-aws-stack-spec.md) (AWS) and [spec 0005, section 11](../spec/0005-azure-stack-spec.md) (Azure). The list for the operator is in the [prerequisites](../docs/prerequisites.md).

## Use

From the repo root:

```sh
make preflight/aws      # AWS_PROFILE and AWS_REGION select the account
make preflight/azure    # ARM_SUBSCRIPTION_ID or the az login selects the subscription
make preflight/test     # unit tests with recorded responses; no network, no credentials
make preflight/lint     # ruff check and ruff format --check
```

Each check prints 1 line: `PASS`, `WARN`, or `FAIL`, the check name, and a message. A `WARN` or `FAIL` line can have a `fix:` line under it.

- `FAIL`: a gap that blocks the deploy. The command exits with 1.
- `WARN`: a signal that the account cannot get, or a check that cannot run. The deploy works.
- Exit code 2: OpenTofu or the targets file cannot be read.

The checks read the stack variables with `tofu console`, so they see the values of your `terraform/<cloud>/terraform.tfvars`. The network variables are required: `vpc_id` and `subnet_id` for AWS, `resource_group_name` and `subnet_id` for Azure. The checks make no change to the account and write no file. The VM quota checks use only the quota of the prober size, and they skip a GPU size.

The AWS `spot-quota` check reads the GPU Spot quotas "All P Spot Instance Requests" (`L-7212CCBC`) and "All G and VT Spot Instance Requests" (`L-3819A6DF`) in the deploy region ([Spot Instance quotas](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-spot-limits.html)). A value of 0 gives a `WARN` that names the quota. A value above 0 gives a `PASS`. A read error gives the `aws/api` `FAIL`, as for the other quota reads. The check itself is never a `FAIL`, and it never requests a quota increase. Request the increase with `make quota/aws` on day 1 ([spec 0012](../spec/0012-aws-quota-requests-spec.md)).

The Azure `allocation-probes` check reads the SKU list of each Region of `azure.provisioning_probes` and gives 1 `FAIL` line for each entry whose size the Region does not offer. This holds for a simulated entry too. An offered size that is restricted for the subscription is a `WARN`, because its rows show `restricted`. Run `make preflight/azure` after you change the experiment file and before the plan.

Outbound internet access from the subnet is a prerequisite that the network owner sets up. The `outbound` check reads the route table (AWS) or the subnet (Azure). It cannot prove a path to the internet, so it gives `PASS` or `WARN`, never `FAIL`.

## Request GPU quota

`python -m ftg_preflight.quota` requests the Azure GPU vCPU quota of the `azure.quota` section of `config/targets.yaml`. The spec is [spec 0011](../spec/0011-azure-quota-requests-spec.md).

`python -m ftg_preflight.quota_aws` does the same for AWS, from the `aws.quota` section: the On-Demand and the Spot vCPU quota of the P and the G and VT groups. Run `make quota/aws` for a dry run. The spec is [spec 0012](../spec/0012-aws-quota-requests-spec.md).

These 2 commands are the only parts of `preflight/` that can change the account, and only with `--apply`. The rest of this section is about Azure.

```sh
make quota/azure                          # dry run: reads the quotas and prints the requests
make quota/azure QUOTA_ARGS=--apply       # sends the requests
make quota/azure QUOTA_ARGS="--subscription <id> --require-vnet"
```

Prerequisites: the `az` CLI, signed in, and its `quota` extension. If the extension is missing, the command stops with exit code 2 and prints the install command, `az extension add --name quota`. Add `--install-extension` to let the command install it.

The subscription comes from `--subscription`, then `AZURE_SUBSCRIPTION_ID`, then `ARM_SUBSCRIPTION_ID`, then `az account show`.

What the command does:

1. It reads the SKU list of each quota region with 1 REST call, and takes the quota family name and the vCPU count of each size from it. The YAML holds only the size, the VM count, and optional regions.
2. It skips a size that the SKU list does not show, or that has a `Location` restriction for the subscription (for example `NotAvailableForSubscription`). A `Zone` restriction is not a skip.
3. It prints `warning: no VNet in <region>` for a region with no virtual network, and still requests the quota. `--require-vnet` skips such a region.
4. Pass 1 requests the regional `cores` limit: the current usage plus the family asks of the region, rounded up to the next multiple of 100. Pass 2 requests each family limit: VM count x vCPUs. Pass 3 requests the regional Spot limit (`lowPriorityCores`): the sum of the family asks of the region.
5. It requests only a limit that is lower than the target.

The output holds the subscription ID in each `--scope`. Remove it before you paste the output into a public place. Exit codes: 0 no error, 1 a read or a request failed, 2 a config, login, or extension error.

### Status of the requests

`status` shows the state of the quota requests for each region. It only reads.

```sh
make quota/status                                       # a table
make quota/status/md                                    # Markdown, grouped by region
make quota/status QUOTA_STATUS_ARGS="--format json"
```

```text
REGION          GPU   ASKED (GPUs / vCPU)  NOW (vCPU)  STATE       TICKET
eastus          T4    32 / 128             0           failed      1000
eastus          A100  8 / 192              —           restricted  1000
```

There is 1 row for each size of `azure.quota` in each region, with the short GPU class (H100, A100, A10, T4, RTX PRO 6000, or `cores`), the GPUs and vCPUs that the config asks for, and the current vCPU limit. The state is `granted`, `failed`, `pending`, `restricted` (the size has a `Location` restriction and no request), or `—`. The ticket is the newest support ticket whose name starts with `ftg-gpu-quota-<region>-`. File 1 ticket for each region with such a name.

It needs the `support` extension of the `az` CLI too. If it is missing, the command stops with exit code 2 and prints `az extension add --name support`. `--install-extension` installs the missing extensions.

The `az` calls run one at a time, because parallel `az` processes share 1 MSAL token cache and one of them can fail. A call that fails on the token cache is repeated once. If a region still fails, the other regions stay, the command prints 1 line for each failed region, and the exit code is 1. The output holds the ticket numbers. Do not commit it.

`status` does not cover AWS. To see the AWS quota requests, run `aws service-quotas list-requested-service-quota-change-history --service-code ec2`. A `CaseId` in the output is a support case; `aws support describe-cases` reads it and needs a Business, Enterprise On-Ramp, or Enterprise support plan ([AWS Support API](https://docs.aws.amazon.com/awssupport/latest/user/about-support-api.html)).

## Probes of the experiment

`make probes/status` prints one Markdown table of the probes of the active experiment: the signal probes (the `jobs` of a targets file, for each size and Region) and the provisioning probes (the `provisioning_probes` entries, and for Azure the pre-check). Each row has the provider, the probe type, the size, the Region, the zone (a dash when the entry has none), N (provisioning only), real or simulated, the cadence, and the last store row (outcome, time in UTC, and `n_placed` for a provisioning probe). A search that went up (ADR 0023) shows its base result as `N of M`, then `max X of n_max` with the stop reason of the upward steps when one applies (the same rule as the dashboard), and `teardown not verified` when any row of the search has an unverified teardown. A search that stopped in its halving phase shows the stop after the outcome, for example `success (stopped: deadline)` (the `details.search_stopped` of its rows that are not upward rows, as the dashboard shows it). The outcome of a simulated probe starts with `sim:`, and the summary counts those outcomes apart (`sim: success 2`). The command reads only the newest files of each probe type first (the file names start with the write time). A probe type with a probe that has no row there is read again over the whole window (`PROBES_DAYS`). `aws_spot_price` is always read over the whole window, because its newest file can hold a poll with no price. A provisioning probe with a CPU size is marked `(control)`. A summary per provider follows: the probes by type, real and simulated, and the count of each last-row outcome (`no row` when the store has none). A `simulate` key must be `true` or `false`; a quoted string or any other value is rejected, as the prober config check does. Write `true` or `false`, never `y` or `n`: an unquoted `y` is a boolean to Terraform but the string `'y'` to the Python readers, which reject it, so the plan and the check disagree only by failing closed. The command only reads. It reads the targets files and the store, and it calls no cloud API for capacity or quota; a real provisioning probe records its own quota outcome.

`make probes/spend` prints a daily spend estimate of the real provisioning probes. It takes the same inputs as `make probes/status` (the `targets_file` of `terraform.tfvars`, the store names from the deploy outputs, `PROBES_DAYS`). A targets file with no experiment name is an error for that cloud when a store is given for it (a cloud with no store is skipped before that check), and the command reads no store for it. For each real successful attempt of `aws_odcr`, `az_capacity_reservation`, and `az_vmss_flex` (upward steps included) the cost is `n_placed` x max(60 s, hold) x list price per hour (a failed scale-set attempt counts the VMs it placed, from `details`), with hold = `latency_ms` / 1000 + `details.teardown.seconds`. 60 s is the AWS minimum ([pricing and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/capacity-reservations-pricing-billing.html)); for Azure it is the conservative full-minute reading, because the docs give no granularity ([brief](../research/learnings/azure-capacity-reservation-billing-granularity-not-documented.md)). List prices come from the AWS Price List API (On-Demand, Linux; needs read access to `pricing:GetProducts`) and the Azure Retail Prices API (no sign-in), once per size and Region. A failed lookup shows "price unknown" and is never guessed. The output has a total per day and cloud, a total per probe for the window, and the total for the window. Rows that may have cost money and are not fully priced (an `aws_odcr` timeout, a teardown that was not verified, a failed scale-set row with no count of placed VMs, a cleanup row) are counted in a "Not counted" section, and the total is then a lower bound. Disks, NICs, and public IPs of scale-set VMs are not counted. The output names the clouds read and the number of files read for each; a store with 0 matching files is an error (exit 1), and the total is then marked incomplete. A cloud with a store is always read, whatever its targets file lists, and every real row found is counted; if the file lists no real (not simulated) provisioning probe, the output adds a note with the number of real rows counted when at least 1 real row is counted, and a store with 0 matching files is then not an error. When the targets file lists no real probe and the current experiment has no file in the window, the output adds a note that names the experiment searched. The command cannot see rows under another experiment name. A cloud with no store and no real provisioning probe is skipped, so a run for 1 cloud is not marked incomplete; a cloud with a real probe and no store is marked incomplete. A store given without its targets file is an error (exit 1) and marks the total incomplete. A `skipped` scale-set row (call deadline) placed nothing and is not counted as a row with a missing count; a `pre_teardown` that was not verified counts like an unverified `teardown`. A failed price lookup also gives exit 1. A failed `aws_odcr` attempt is not priced: a reservation is all or nothing. The Azure price answer is described in a [brief](../research/learnings/azure-retail-prices-spot-and-low-priority-share-armskuname.md). It is an estimate, not a bill. It only reads.

```bash
make probes/status                    # the targets_file of terraform.tfvars (default config/targets.yaml); the stores from the deploy outputs
make probes/status OUT=probes.md      # the same, also written to a file
```

- Last row: 1 `duckdb` run over the rows of the experiment in the last `PROBES_DAYS` UTC days (default 3), like the old dashboard snapshot (`web/scripts/sync.sh`, removed at the cut-over; the board now reads a DuckDB file built from the mirror, spec 0006 sections 4.3 and 5.5). A probe with no row in the window shows `—`. The row must have the same real or simulated mode as the probe, so a probe that has just changed mode shows `—` until it runs.
- An Azure provisioning entry takes the probe type that lists it in the file (`az_capacity_reservation` or `az_vmss_flex`, [ADR 0022](../decisions/0022-azure-provisioning-probes-are-listed-by-probe-type-and-find-their-resources-by-tags.md)). The table shows that type and takes the last row of that type.
- Exit 0: done. Exit 1: a store read failed; the table still prints, with the error in the "Errors" section. Exit 2: a targets file cannot be read.

## Layout

- `ftg_preflight/aws.py`: the AWS checks, with boto3. The `crt` extra lets boto3 read a profile from `aws login`.
- `ftg_preflight/azure.py`: the Azure checks, with the `az` CLI.
- `ftg_preflight/terraform.py`: reads the variables and the state of a stack.
- `ftg_preflight/quota.py`: the GPU quota requests, with the `az quota` extension.
- `ftg_preflight/quota_aws.py`: the AWS GPU quota requests, with boto3 and Service Quotas.
- `ftg_preflight/probes.py`: the read-only `make probes/status` table.
- `ftg_preflight/spend.py`: the read-only `make probes/spend` estimate.
- `ftg_preflight/quota_status.py`: the read-only `status` command: the requests and their support tickets.
- `ftg_preflight/report.py`: the levels, the output, and the exit code.
- `tests/fixtures/`: recorded responses in the shape of the cloud APIs. They hold no account ID, subscription ID, or resource name.
