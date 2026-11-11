#!/usr/bin/env bash
# Show the state of the Azure stack: the prober VM power state, the sweeper state, the count of
# leaked resources, and the newest 5 data blobs. Usage: status.sh [terraform dir]. Needs tofu outputs and the az CLI.
# Every az call is scoped to the target subscription (AZURE_SUBSCRIPTION_ID, else ARM_SUBSCRIPTION_ID, else the
# subscription of the prober VM id). It never reads another subscription.
set -euo pipefail

tf_dir="${1:-terraform/azure}"

if ! command -v az >/dev/null 2>&1; then
  echo "status: the az CLI is not installed. See terraform/azure/README.md." >&2
  exit 1
fi

output() {
  tofu -chdir="$tf_dir" output -raw "$1"
}

control_rg=$(output control_resource_group)
probe_rg=$(output probe_resource_group)
account=$(output data_storage_account)
function_app=$(output function_app_name)
vm_id=$(output prober_vm_id)

# The subscription of the stack: the setting, else the one in the prober VM id. Both must agree.
from_id=$(printf '%s' "$vm_id" | sed -n 's#^/subscriptions/\([^/]*\)/.*#\1#ip')
subscription="${AZURE_SUBSCRIPTION_ID:-${ARM_SUBSCRIPTION_ID:-$from_id}}"
if [ -z "$subscription" ]; then
  echo "status: no target subscription. Set AZURE_SUBSCRIPTION_ID or ARM_SUBSCRIPTION_ID." >&2
  exit 1
fi
if [ -n "$from_id" ] && [ "$(printf '%s' "$subscription" | tr 'A-Z' 'a-z')" != "$(printf '%s' "$from_id" | tr 'A-Z' 'a-z')" ]; then
  echo "status: the target subscription differs from the subscription of the prober VM. Refusing to read it." >&2
  exit 1
fi

# Run az scoped to the target subscription.
azs() {
  az "$@" --subscription "$subscription"
}

echo "Prober VM power state:"
azs vm get-instance-view --ids "$vm_id" \
  --query "instanceView.statuses[?starts_with(code, 'PowerState/')].displayStatus" -o tsv

echo
echo "Sweeper state:"
azs functionapp show --resource-group "$control_rg" --name "$function_app" --query "state" -o tsv

echo
# The group also holds the prober VM and other stack resources. Only probe resources carry the
# experiment tag. Capacity reservations are counted by group, because the group holds the tag.
# Disks and NICs also count when an experiment-tagged VM left them behind. Read-only.
count_tagged="length([?tags.experiment])"
vms=$(azs vm list --resource-group "$probe_rg" --query "$count_tagged" -o tsv)
vmss=$(azs vmss list --resource-group "$probe_rg" --query "[?tags.experiment].name" -o tsv)
instances=0
for vmss_name in $vmss; do
  n=$(azs vmss list-instances --resource-group "$probe_rg" --name "$vmss_name" --query "length(@)" -o tsv)
  instances=$((instances + n))
done
disks=$(azs disk list --resource-group "$probe_rg" --query "$count_tagged" -o tsv)
nics=$(azs network nic list --resource-group "$probe_rg" --query "$count_tagged" -o tsv)
reservations=0
# Assign first: a failed command substitution in a for list does not stop the script under set -e.
groups=$(azs capacity reservation group list --resource-group "$probe_rg" --query "[?tags.experiment].name" -o tsv)
for group in $groups; do
  n=$(azs capacity reservation list --resource-group "$probe_rg" --capacity-reservation-group "$group" \
    --query "length(@)" -o tsv)
  reservations=$((reservations + n))
done
echo "Probe resources with an experiment tag in $probe_rg: VMs $vms, scale set instances $instances," \
  "disks $disks, NICs $nics, capacity reservations $reservations"
echo "leaked resources: $((vms + instances + disks + nics + reservations))" \
  "(must be 0 before the grant is switched off or the stack is destroyed)"

echo
echo "Newest 5 data blobs in $account/data:"
azs storage blob list --account-name "$account" --container-name data --auth-mode login \
  --query "reverse(sort_by([].{name: name, modified: properties.lastModified}, &modified))[:5]" -o table
