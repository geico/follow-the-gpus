# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3[crt]>=1.40,<2"]
# ///
"""Print the state of the prober VM, the leaked Capacity Reservations, and the newest 5 object keys.

Usage: uv run status.py INSTANCE_ID BUCKET REGION EXPERIMENT REGIONS_JSON
The stack outputs give the arguments: `experiment` and `target_regions` (a JSON list).
The credentials come from the default chain (AWS_PROFILE or the environment). All calls are
read-only: `ec2:DescribeInstances`, `ec2:DescribeCapacityReservations`, and `s3:ListBucket`.
"""

import json
import sys
from datetime import UTC, datetime

# The sweeper grace period: a reservation older than this is leaked.
GRACE_SECONDS = 180
STATES = ["active", "pending"]
NO_REGIONS_MESSAGE = (
    "no Region was checked: target_regions is empty, so the leaked-resource count is unknown."
    " Name Regions in the aws section of the targets file (jobs[].regions,"
    " provisioning_probes[].region, or quota.regions)."
)


def list_reservations(ec2, experiment: str) -> list[dict]:
    """Reservations with the experiment tag in state active or pending. Read-only."""
    found: list[dict] = []
    token = None
    while True:
        kwargs = {
            "Filters": [
                {"Name": "tag:experiment", "Values": [experiment]},
                {"Name": "state", "Values": STATES},
            ]
        }
        if token:
            kwargs["NextToken"] = token
        resp = ec2.describe_capacity_reservations(**kwargs)
        found.extend(resp.get("CapacityReservations") or [])
        token = resp.get("NextToken")
        if not token:
            return found


def report_reservations(clients: dict, experiment: str, now: datetime) -> int:
    """Print the reservations per Region and the leaked count. Return the count."""
    leaked = 0
    total = 0
    for region, ec2 in clients.items():
        found = list_reservations(ec2, experiment)
        total += len(found)
        print(f"  {region}: {len(found)}")
        for r in sorted(found, key=lambda r: r["CreateDate"]):
            age = (now - r["CreateDate"]).total_seconds()
            is_leaked = age > GRACE_SECONDS
            leaked += is_leaked
            end = r.get("EndDate")
            end_text = f"{end:%Y-%m-%dT%H:%M:%SZ}" if end else "none"
            print(
                f"    {r['CapacityReservationId']}  {r['InstanceType']}  count {r['TotalInstanceCount']}"
                f"  {r['State']}  created {r['CreateDate']:%Y-%m-%dT%H:%M:%SZ}  ends {end_text}"
                f"{'  LEAKED' if is_leaked else ''}"
            )
    print(
        f"Capacity reservations with experiment tag {experiment}, active or pending: {total}"
    )
    print(
        f"leaked resources: {leaked} (a reservation older than {GRACE_SECONDS} s;"
        " must be 0 before the stack is destroyed)"
    )
    return leaked


def main() -> int:
    if len(sys.argv) != 6:
        print(__doc__, file=sys.stderr)
        return 2
    instance_id, bucket, region, experiment, regions_json = sys.argv[1:6]
    regions = json.loads(regions_json)
    if not regions:
        print(NO_REGIONS_MESSAGE, file=sys.stderr)
        return 1

    import boto3  # imported here so the unit tests need no boto3

    ec2 = boto3.client("ec2", region_name=region)
    reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
    state = reservations[0]["Instances"][0]["State"]["Name"]
    print(f"instance {instance_id}: {state}")

    print()
    print("Probe Capacity Reservations per Region:")
    clients = {r: boto3.client("ec2", region_name=r) for r in regions}
    report_reservations(clients, experiment, datetime.now(UTC))

    print()
    s3 = boto3.client("s3", region_name=region)
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        objects.extend(page.get("Contents", []))
    objects.sort(key=lambda o: o["LastModified"], reverse=True)
    print(f"bucket {bucket}: {len(objects)} objects, newest first:")
    for obj in objects[:5]:
        print(f"  {obj['LastModified']:%Y-%m-%dT%H:%M:%SZ}  {obj['Key']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
