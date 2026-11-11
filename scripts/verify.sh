#!/usr/bin/env bash
# Post-apply proof for one cloud.
# Usage: verify.sh aws|azure (make verify/aws, make verify/azure). The cloud CLI uses your own
# login (AWS_PROFILE, az login).
# It reads only. It prints the newest row object and log object under experiment=<name>/, the
# sha256 of the store's manifest.json, and the running release, the service state and start, the
# process-maps proof, the restart count, and the rollback marker of the VM. The VM read is one
# command (aws ssm send-command, az vm run-command invoke).
# Exit 0 only when ALL hold: the release equals the manifest sha256 and the service is active;
# the process-maps proof is OK; the rollback marker is absent; the cached manifest equals the
# store manifest; NRestarts is 0 (a person explains any other count); a raw row object and a log
# object of the prober itself exist (a daily file and a sweeper log do not count); the newest row
# and the newest log are newer than the service start; every store time ends in Z or +00:00. Each failed
# check prints one "NO: <reason>" line.
# Exit 1 otherwise, also when a read fails (nothing is proven then). Names come from
# `tofu output -raw` in terraform/<cloud>, never from the user.
set -u

cloud="${1:-}"
case "$cloud" in aws | azure) ;; *)
  echo "usage: verify.sh aws|azure" >&2
  exit 2
  ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/ftg-verify.XXXXXX") || exit 1
trap 'rm -rf "$tmp"' EXIT INT TERM

die() {
  echo "verify/$cloud: $*" >&2
  exit 1
}
out() { # name: one stack output
  local v
  v=$(tofu -chdir="$here/../terraform/$cloud" output -no-color -raw "$1" 2>"$tmp/err") || die "cannot read output $1: $(cat "$tmp/err")"
  # A state with no outputs prints a warning on stdout and exits 0, so white space means no value.
  case "$v" in "" | *[[:space:]]*) die "output $1 has no value; apply terraform/$cloud first" ;; esac
  printf '%s' "$v"
}
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi | cut -d' ' -f1; }

# The VM script. It reaches the VM as base64, so no quote of the shell or the JSON can break it.
read -r -d '' vm_script <<'VM' || true
pid=$(systemctl show ftg-prober -p MainPID --value)
cur=$(readlink -f /opt/ftg/current)
maps=""
[ "${pid:-0}" -gt 0 ] && maps=$(grep -o '/opt/ftg/releases/[^/ ]*' /proc/$pid/maps | sort -u)
n=$(printf '%s' "$maps" | grep -c .)
echo active=$(systemctl show ftg-prober -p ActiveState --value)
echo release=$(test -d /opt/ftg/current/. && basename "$cur")
st=$(systemctl show ftg-prober -p ActiveEnterTimestamp --value)
echo start=$st
echo host=$(hostname | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9][^a-z0-9]*/-/g; s/^-*//; s/-*$//')
echo startiso=$(date -u -d "$st" +%Y-%m-%dT%H:%M:%S 2>/dev/null)
echo restarts=$(systemctl show ftg-prober -p NRestarts --value)
echo cached=$(sha256sum /etc/ftg/manifest.json 2>/dev/null | cut -d' ' -f1)
if [ "$n" = 1 ] && [ "$maps" = "$cur" ]; then echo proof=OK; else echo proof=FAILED; fi
if [ -e /etc/ftg/rollback.failed ]; then echo rollback=present; else echo rollback=absent; fi
VM
vm_cmd="echo $(printf '%s\n' "$vm_script" | base64 | tr -d '\n') | base64 -d | bash"

experiment=$(out experiment) || exit 1
prefix="experiment=${experiment}/"

if [ "$cloud" = aws ]; then
  bucket=$(out bucket_name) || exit 1
  instance=$(out instance_id) || exit 1
  region=$(out region) || exit 1
  echo "verify/aws: bucket $bucket, prefix $prefix"
  list=$(aws s3api list-objects-v2 --region "$region" --bucket "$bucket" --prefix "$prefix" \
    --query 'Contents[].[LastModified,Key]' --output text 2>"$tmp/err") || die "list failed: $(cat "$tmp/err")"
  modified=$(aws s3api head-object --region "$region" --bucket "$bucket" --key manifest.json \
    --query LastModified --output text 2>"$tmp/err") || die "manifest.json head failed: $(cat "$tmp/err")"
  aws s3 cp "s3://$bucket/manifest.json" "$tmp/manifest.json" --region "$region" --only-show-errors 2>"$tmp/err" \
    || die "manifest.json read failed: $(cat "$tmp/err")"
  cmd=$(aws ssm send-command --region "$region" --instance-ids "$instance" --document-name AWS-RunShellScript \
    --parameters "{\"commands\":[\"$vm_cmd\"]}" --query Command.CommandId --output text 2>"$tmp/err") \
    || die "release unavailable (ssm send-command failed): $(cat "$tmp/err")"
  aws ssm wait command-executed --region "$region" --command-id "$cmd" --instance-id "$instance" 2>/dev/null
  vm=$(aws ssm get-command-invocation --region "$region" --command-id "$cmd" --instance-id "$instance" \
    --query StandardOutputContent --output text 2>"$tmp/err") \
    || die "release unavailable (ssm get-command-invocation failed): $(cat "$tmp/err")"
else
  account=$(out data_storage_account) || exit 1
  vm_id=$(out prober_vm_id) || exit 1
  sub=$(printf '%s' "$vm_id" | sed -n 's#^/subscriptions/\([^/]*\)/.*#\1#ip')
  [ -n "$sub" ] || die "no subscription in the prober_vm_id output; refusing to call az"
  env_sub="${AZURE_SUBSCRIPTION_ID:-${ARM_SUBSCRIPTION_ID:-}}"
  if [ -n "$env_sub" ] && [ "$(printf '%s' "$env_sub" | tr 'A-Z' 'a-z')" != "$(printf '%s' "$sub" | tr 'A-Z' 'a-z')" ]; then
    die "the target subscription differs from the subscription of the prober VM. Refusing to read it."
  fi
  echo "verify/azure: account $account, prefix $prefix"
  list=$(az storage blob list --auth-mode login --subscription "$sub" --account-name "$account" --container-name data \
    --prefix "$prefix" --num-results "*" --query '[].[properties.lastModified,name]' --output tsv 2>"$tmp/err") \
    || die "list failed: $(cat "$tmp/err")"
  deny="cannot read manifest.json in the code container. The identity needs the role Storage Blob Data Reader on that container (the stack assigns it to the deployer; data_reader_principal_ids adds more). Ask the owner of the storage account for the role; do not grant it yourself."
  modified=$(az storage blob show --auth-mode login --subscription "$sub" --account-name "$account" --container-name code \
    --name manifest.json --query properties.lastModified --output tsv 2>"$tmp/err") || die "$deny az said: $(cat "$tmp/err")"
  az storage blob download --auth-mode login --subscription "$sub" --account-name "$account" --container-name code \
    --name manifest.json --file "$tmp/manifest.json" --overwrite --no-progress --only-show-errors >/dev/null 2>"$tmp/err" \
    || die "$deny az said: $(cat "$tmp/err")"
  vm=$(az vm run-command invoke --subscription "$sub" --ids "$vm_id" --command-id RunShellScript \
    --scripts "$vm_cmd" --query 'value[0].message' --output tsv 2>"$tmp/err") \
    || die "release unavailable (az vm run-command failed): $(cat "$tmp/err")"
fi

# ISO 8601 UTC times sort as text, and the listing is by key, so sort here.
list=$(printf '%s\n' "$list" | grep -v '^None$' | grep .)
# Only the prober's own objects count (spec 0003 sections 4.2, 4.3, 8.2): a raw row object is
# experiment=<name>/provider=<p>/probe_type=<t>/date=<D>/<file>.json[l]; a daily file sits under
# daily/ and does not count. A prober log object is experiment=<name>/logs/provider=<p>/date=<D>/
# <ts>_<host>_<uuid>.jsonl; a sweeper log has the same shape, with the host of the sweeper, so the
# host of the VM tells them apart. The key is the second tab field.
vm=$(printf '%s\n' "$vm" | tr -d '\r')
field() { printf '%s\n' "$vm" | sed -n "s/^$1=//p" | head -n 1; }
vmhost=$(field host)
row_re="^experiment=${experiment}/provider=[^/]+/probe_type=[^/]+/date=[^/]+/[^/]+[.]jsonl?\$"
log_re="^experiment=${experiment}/logs/provider=[^/]+/date=[^/]+/[0-9]{8}T[0-9]{6}Z_${vmhost:-NOHOST}_[0-9a-f-]{36}[.]jsonl\$"
pick() { # regex: the lines of $list whose key (field 2) matches
  local t k
  while IFS=$'\t' read -r t k; do [[ $k =~ $1 ]] && printf '%s\t%s\n' "$t" "$k"; done <<<"$list"
}
rowset=$(pick "$row_re")
logset=$(pick "$log_re")
rows=$(printf '%s\n' "$rowset" | grep . | sort | tail -n 1)
logs=$(printf '%s\n' "$logset" | grep . | sort | tail -n 1)
echo "  newest row: ${rows:-none}"
echo "  newest log: ${logs:-none}"
sha=$(sha256 <"$tmp/manifest.json")
echo "  $cloud manifest.json: sha256 $sha, LastModified $modified"

active=$(field active)
release=$(field release)
start=$(field start)
echo "  $cloud running release: ${release:-unavailable}, service start: ${start:-unavailable}, cached manifest: $(field cached)"
echo "  $cloud process maps = current: $(field proof), restarts: $(field restarts), rollback marker: $(field rollback)"
if [ -n "$release" ] && [ "$release" = "$sha" ] && [ "$active" = active ]; then eq=yes; else eq=NO; fi
echo "  $cloud release = manifest sha256 and service active: $eq (state ${active:-unknown})"
ok=1
no() { echo "NO: $*"; ok=0; }
[ "$eq" = yes ] || no "running release is not the manifest sha256, or the service is not active"
[ "$(field proof)" = OK ] || no "process maps do not show exactly 1 release dir equal to the current link (proof: $(field proof))"
[ "$(field rollback)" = absent ] || no "rollback marker is not absent ($(field rollback))"
[ "$(field cached)" = "$sha" ] || no "cached manifest ($(field cached)) differs from the store manifest ($sha)"
[ "$(field restarts)" = 0 ] || no "NRestarts is $(field restarts), not 0; explain it before you accept"
startiso=$(field startiso)
t19() { printf '%s' "$1" | cut -c1-19; }
utc_re='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|\+00:00)$'
utc_ok() { printf '%s\n' "$1" | grep . | cut -f1 | grep -Evq -e "$utc_re" && return 1; return 0; }
for kind in row log; do
  if [ "$kind" = row ]; then line=$rows; set=$rowset; else line=$logs; set=$logset; fi
  if ! utc_ok "$set"; then
    no "a $kind object time is not UTC (Z or +00:00); the times cannot be compared"
  elif [ "$kind" = log ] && [ -z "$vmhost" ]; then
    no "the VM host name is unavailable; cannot tell the prober log from a sweeper log"
  elif [ -z "$line" ]; then
    no "no $kind object under $prefix"
  elif [ -z "$startiso" ]; then
    no "service start time unavailable; cannot prove the newest $kind is newer"
  elif ! [ "$(t19 "$line")" \> "$startiso" ]; then
    no "newest $kind ($(t19 "$line")) is not newer than the service start ($startiso)"
  fi
done
[[ $modified =~ $utc_re ]] || no "manifest.json LastModified is not UTC (Z or +00:00): $modified"
[ "$ok" = 1 ]
