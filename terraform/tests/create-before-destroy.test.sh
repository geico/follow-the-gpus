#!/usr/bin/env bash
# Guard for . OpenTofu applies create_before_destroy to every resource that a
# create_before_destroy resource depends on
# (https://opentofu.org/docs/language/resources/behavior/#lifecycle-customizations). A fixed-name
# resource (the prober VM, the function app, manifest.json) then creates before it destroys, and the
# create fails because the name is taken. A role assignment that reads the principal of such a
# resource must not set the option. This test lists each resource that sets it and compares the
# list with the 3 hash-named blobs and the 3 hash-keyed objects, which are safe because the name
# changes with the content. A new entry needs a review for this defect.
#
# (walker in cbd_walk.py): the list alone does not see the propagation. The second check below follows the
# references of each such resource (attributes, depends_on, and through locals and data sources)
# and fails when the chain reaches a managed resource that does not set the option itself and is
# not in the ALLOW list. The check reads the HCL, so it needs no init, no provider, and no cloud
# credentials; a `tofu graph` or a plan needs both. It is conservative: a reference that sits in
# an unused branch of a conditional still counts. Run: make test/lifecycle
set -u

here=$(cd "$(dirname "$0")" && pwd)
tf="$here/.."

found=$(
  for f in "$tf"/azure/*.tf "$tf"/aws/*.tf; do
    awk -v file="${f#"$tf"/}" '
      /^resource "/ { res = $2 "." $3; gsub(/"/, "", res) }
      /^[[:space:]]*#/ { next }
      /create_before_destroy[[:space:]]*=[[:space:]]*true/ { print file ": " res }
    ' "$f"
  done | sort
)

expected=$(
  cat <<'LIST' | sort
aws/main.tf: aws_s3_object.config
aws/main.tf: aws_s3_object.prober
aws/main.tf: aws_s3_object.sync_script
azure/control.tf: azurerm_storage_blob.config
azure/control.tf: azurerm_storage_blob.prober
azure/control.tf: azurerm_storage_blob.sync_script
LIST
)

rc=0
if [ "$found" = "$expected" ]; then
  echo "ok   create_before_destroy is set only on the hash-named code objects"
else
  echo "FAIL create_before_destroy is set on a resource outside the hash-named code objects" >&2
  diff <(echo "$expected") <(echo "$found") >&2
  rc=1
fi

python3 -I "$here/cbd_walk.py" "$tf" || rc=1

# the walker fails closed on HCL it cannot parse. Each fixture below must fail.
python3 -I "$here/cbd_walk_fixtures.py" "$here/cbd_walk.py" || rc=1
exit $rc
