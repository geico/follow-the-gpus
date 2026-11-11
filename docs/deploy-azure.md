# Deploy to Azure

This page shows how to deploy the Azure stack: one small prober VM, a data store, and a sweeper function. Do the steps in order.

## Before you start

Read [prerequisites](prerequisites.md). Read [config](config.md) for the settings. If a step fails, read [troubleshooting](troubleshooting.md).

Copy the example settings once, and put in your values. A deploy needs `resource_group_name` and `subnet_id`, and usually `subscription_id`. Log in with `az login`.

```sh
cp terraform/azure/terraform.tfvars.example terraform/azure/terraform.tfvars
```

## 1. Ask for GPU quota (first time only)

```sh
make quota/azure
```

This is a dry run. It prints the requests and sends none. To send them, run `make quota/azure QUOTA_ARGS=--apply`. The subscription comes from `az account set`, `ARM_SUBSCRIPTION_ID`, or `QUOTA_ARGS=--subscription <id>`. To read the status of your requests, run `make quota/status`.

## 2. Preflight

```sh
make preflight/azure
```

The preflight only reads. If it finds a gap, it fails and gives the fix.

## 3. Plan and apply

Use the two-step way. You then apply only the plan that you read.

```sh
make plan/azure
make apply/azure
```

`plan/azure` writes `terraform/azure/tfplan`. `apply/azure` applies that file and nothing else. If you change `terraform.tfvars` after the plan, OpenTofu stops the apply with `Mismatch between input and plan variable value`. Make a new plan.

For one step with a prompt, run `make deploy/azure`. `AUTO_APPROVE=1` skips the prompt.

If the subnet is in another resource group, set `subnet_id` to its full ID. For other regions, set `probe_subnet_ids` with one isolated subnet for each region ([spec 0005](../spec/0005-azure-stack-spec.md), section 4.3).

## 4. Verify

```sh
make verify/azure
```

It shows the newest row object and the newest log object, the release on the VM, and the state of the service. It exits 1 unless the release matches the manifest and the service is active. It also runs `make verify/azure-sweeper`. If it fails, read [troubleshooting](troubleshooting.md). For a look at the VM and the newest blobs, run `make status/azure`.

## State backend

By default the state is a local file, `terraform/azure/terraform.tfstate`. Git ignores it. The state stays in this folder. For shared state, use `backend.hcl`. See [Backend](config.md#backend).

## Cost guards

- `grant_billable_provisioning` is `false` by default. The prober gets no right to create reservations or scale sets until you set it to `true`.
- Set `alert_email` and `budget_amount` for budget alerts at 80 and 100 percent. With an empty `alert_email` there is no budget and no alert, and nobody is told when the sweeper stops or the budget is near. Cost data lags by hours, so the budget is a late warning.
- The sweeper removes leaked capacity. It is the real protection.

Read [cost and consent](cost-and-consent.md).

## Destroy

You run destroy yourself.

1. Copy the data first. The destroy deletes the data container with its storage account: `azcopy copy 'https://<account>.blob.core.windows.net/data' ./backup --recursive`.
2. Stop the prober: `az vm deallocate --ids <prober_vm_id>`.
3. Run `make status/azure`. The line "leaked resources" must show `0`. If not, wait 4 minutes for the sweeper and check again.
4. Run `make destroy/azure`.

A scale set with running VMs blocks its own deletion, and each VM bills until it is gone.

Azure keeps the name of a deleted Log Analytics workspace for 14 days. A new deploy that uses another sweeper region in that time fails on the name.
