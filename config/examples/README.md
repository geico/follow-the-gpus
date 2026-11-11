# Example experiment files

These files show the shape of an experiment file (`config/targets.yaml`). They hold no account number and no private value. The schema is in [spec 0003](../../spec/0003-prober-core-spec.md), section 3.

| File | What it shows |
|---|---|
| [signals-only.yaml](signals-only.yaml) | Signal probes only (Spot score, Spot price, Spot Advisor). They spend no money. |
| [provisioning-hourly.yaml](provisioning-hourly.yaml) | Provisioning probes every hour (`1h`). |
| [provisioning-2h.yaml](provisioning-2h.yaml) | Provisioning probes every 2 hours (`2h`). |

## How to use one

1. Copy the file over `config/targets.yaml`, or set `targets_file` in `terraform.tfvars` to its path. Keep a private file in a folder of its own: the stack uploads the whole folder.
2. Change `experiment` to a name of your own. Use a date and a short word.
3. Run `make prober/check-config`. It must print `ok`.

## Provisioning probes spend money

Every provisioning entry in these files has `simulate: true`. A copy of the file creates no capacity. To run an entry for real, turn on 2 keys. Both spend money:

1. `grant_billable_provisioning = true` in `terraform/<cloud>/terraform.tfvars`.
2. On the entry: remove `simulate: true` and add `allow_billable_provisioning: true`.

Check your quota and the price before you do this. Start with one entry. See [cost and consent](../../docs/cost-and-consent.md). To deploy the stack, see [deploy on AWS](../../docs/deploy-aws.md) or [deploy on Azure](../../docs/deploy-azure.md).
