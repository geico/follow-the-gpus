"""Preflight checks for the Azure stack (spec 0005, section 11). Each call only reads.

The checks call the az CLI through an `Az` function, so the tests pass a fake with recorded output.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ftg_preflight.report import Finding, fail, ok, warn

README_QUOTA = "terraform/azure/README.md#quota"
README_PREREQ = "terraform/azure/README.md#prerequisites"
ARM = "https://management.azure.com"

Az = Callable[..., Any]

# azurerm 4.x registers its "legacy" set of resource providers when it starts. These are in it.
AZURERM_REGISTERS = (
    "Microsoft.Compute",
    "Microsoft.Network",
    "Microsoft.Storage",
    "Microsoft.Web",
    "Microsoft.Insights",
    "Microsoft.OperationalInsights",
    "Microsoft.ManagedIdentity",
)
# Not in that set. The budget needs Microsoft.Consumption; this check needs Microsoft.Quota.
BUDGET_PROVIDER = "Microsoft.Consumption"
QUOTA_PROVIDER = "Microsoft.Quota"

# The main create actions of `make apply/azure`. The stack creates no group and no network.
# The role actions need the subscription scope, the subnet join needs the subnet scope, and the
# other actions need the resource group scope.
DEPLOY_ACTIONS = (
    "Microsoft.Authorization/roleDefinitions/write",
    "Microsoft.Authorization/roleAssignments/write",
    "Microsoft.Compute/virtualMachines/write",
    "Microsoft.Compute/virtualMachineScaleSets/write",
    "Microsoft.Compute/capacityReservationGroups/write",
    "Microsoft.Network/networkInterfaces/write",
    "Microsoft.Network/virtualNetworks/subnets/join/action",
    "Microsoft.Storage/storageAccounts/write",
    "Microsoft.Storage/storageAccounts/listKeys/action",
    "Microsoft.Web/serverfarms/write",
    "Microsoft.Web/sites/write",
    "Microsoft.OperationalInsights/workspaces/write",
    "Microsoft.Insights/components/write",
)
ALERT_ACTIONS = (
    "Microsoft.Consumption/budgets/write",
    "Microsoft.Insights/actionGroups/write",
    "Microsoft.Insights/scheduledQueryRules/write",
)
RBAC_ACTIONS = (
    "Microsoft.Authorization/roleDefinitions/write",
    "Microsoft.Authorization/roleAssignments/write",
)
JOIN_ACTION = "Microsoft.Network/virtualNetworks/subnets/join/action"
ASSIGN_ACTION = "Microsoft.Authorization/roleAssignments/write"

_SUBNET_ID = re.compile(
    r"^/subscriptions/(?P<sub>[^/]+)/resourceGroups/(?P<rg>[^/]+)/providers/"
    r"Microsoft\.Network/virtualNetworks/(?P<vnet>[^/]+)/subnets/(?P<subnet>[^/]+)$",
    re.IGNORECASE,
)


class AzError(RuntimeError):
    """An az CLI error. `code` is the Azure error code, for example ResourceGroupNotFound."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def az_cli(*args: str) -> Any:
    """Run `az <args> -o json` and parse the output."""
    try:
        proc = subprocess.run(
            ["az", *args, "-o", "json"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise AzError("the az CLI is not installed") from exc
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
        code = next((m.group(1) for ln in lines if (m := re.search(r"\((\w+)\)", ln))), "")
        raise AzError(lines[-1] if lines else f"az exited with {proc.returncode}", code)
    if not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AzError(f"the az output is not JSON: {proc.stdout.strip()[:200]}") from exc


@dataclass
class AzureInputs:
    location: str
    prober_vm_size: str
    sweeper_plan_sku: str
    resource_group_name: str | None = None
    subnet_id: str | None = None
    sweeper_location: str | None = None
    alert_email: str = ""
    subscription: str | None = None
    prober_deployed: bool = False
    regions: list[str] = field(default_factory=list)
    spot_sizes: list[str] = field(default_factory=list)
    provisioning_probes: list[Mapping[str, Any]] = field(default_factory=list)

    @property
    def sweeper_region(self) -> str:
        return self.sweeper_location or self.location

    @property
    def subnet_in_other_group(self) -> bool:
        """True when the subnet is in a resource group other than resource_group_name.

        Azure compares group names without regard to case. The stack then assigns a role to
        the prober identity at the scope of the subnet (spec 0005, section 5.1).
        """
        m = _SUBNET_ID.match(self.subnet_id or "")
        return bool(m and self.resource_group_name) and (
            m["rg"].lower() != (self.resource_group_name or "").lower()
        )

    def group_scope(self, subscription: str) -> str:
        return f"/subscriptions/{subscription}/resourceGroups/{self.resource_group_name}"


@dataclass
class Account:
    subscription: str
    name: str
    user: str
    user_type: str


def check_account(az: Az, inputs: AzureInputs) -> tuple[Finding, Account | None]:
    args = ["account", "show"]
    if inputs.subscription:
        args += ["--subscription", inputs.subscription]
    try:
        data = az(*args)
    except AzError as exc:
        return (
            fail(
                "azure/login",
                f"no az login for the subscription ({exc})",
                "az login, then az account set --subscription <name>, or set ARM_SUBSCRIPTION_ID",
            ),
            None,
        )
    user = data.get("user") or {}
    acct = Account(data["id"], data.get("name", ""), user.get("name", ""), user.get("type", ""))
    if data.get("state", "Enabled") != "Enabled":
        return fail("azure/login", f"the subscription state is {data['state']}"), None
    return ok("azure/login", f"signed in as a {acct.user_type or 'principal'}"), acct


def check_providers(az: Az, acct: Account, inputs: AzureInputs) -> list[Finding]:
    rows = az(
        "provider",
        "list",
        "--subscription",
        acct.subscription,
        "--query",
        "[].{namespace:namespace, registrationState:registrationState}",
    )
    state = {r["namespace"].lower(): r.get("registrationState", "") for r in rows}

    def missing(names: Iterable[str]) -> list[str]:
        return [n for n in names if state.get(n.lower()) != "Registered"]

    findings = []
    auto = missing(AZURERM_REGISTERS)
    if auto:
        findings.append(
            warn(
                "azure/providers",
                f"not registered: {', '.join(auto)}. The azurerm provider registers them at "
                "the start of a deploy, if the caller has the register right.",
                " ; ".join(f"az provider register --namespace {n}" for n in auto),
            )
        )
    if inputs.alert_email and missing([BUDGET_PROVIDER]):
        findings.append(
            fail(
                "azure/providers",
                f"{BUDGET_PROVIDER} is not registered, and alert_email adds a budget",
                f"az provider register --namespace {BUDGET_PROVIDER}",
            )
        )
    if missing([QUOTA_PROVIDER]):
        findings.append(
            warn(
                "azure/providers",
                f"{QUOTA_PROVIDER} is not registered, so the App Service quota check cannot run",
                f"az provider register --namespace {QUOTA_PROVIDER}",
            )
        )
    if not findings:
        findings.append(ok("azure/providers", "the resource providers are registered"))
    return findings


def _pattern(action: str) -> re.Pattern[str]:
    return re.compile(re.escape(action).replace(r"\*", ".*"), re.IGNORECASE)


def allowed_actions(definitions: Iterable[Mapping[str, Any]], wanted: Iterable[str]) -> set[str]:
    """The wanted actions that 1 or more role definitions allow. NotActions apply per block."""
    result = set()
    for definition in definitions:
        for block in definition.get("permissions") or []:
            allow = [_pattern(a) for a in block.get("actions") or []]
            deny = [_pattern(a) for a in block.get("notActions") or []]
            for action in wanted:
                if any(p.fullmatch(action) for p in allow) and not any(
                    p.fullmatch(action) for p in deny
                ):
                    result.add(action)
    return result


def covers(assignment_scope: str, target: str) -> bool:
    """True when an assignment at `assignment_scope` applies at `target`.

    A management group or root scope is a parent of each subscription. The list call returns
    such an assignment only when it applies (`--include-inherited`).
    """
    scope = assignment_scope.rstrip("/").lower()
    if not scope.startswith("/subscriptions/"):
        return True
    target = target.lower()
    return target == scope or target.startswith(scope + "/")


def action_targets(acct: Account, inputs: AzureInputs) -> list[tuple[str, str]]:
    """The (action, scope) pairs that the deploy needs.

    A subnet in another resource group also needs the role assignment write on the subnet:
    the stack assigns Network Contributor to the prober identity there.
    """
    sub = f"/subscriptions/{acct.subscription}"
    group = inputs.group_scope(acct.subscription) if inputs.resource_group_name else sub
    wanted = DEPLOY_ACTIONS + (ALERT_ACTIONS if inputs.alert_email else ())
    targets = {a: group for a in wanted}
    targets.update({a: sub for a in RBAC_ACTIONS})
    if inputs.subnet_id:
        targets[JOIN_ACTION] = inputs.subnet_id
    pairs = list(targets.items())
    if inputs.subnet_in_other_group:
        pairs.append((ASSIGN_ACTION, inputs.subnet_id))
    return pairs


def check_permissions(az: Az, acct: Account, inputs: AzureInputs) -> list[Finding]:
    sub = f"/subscriptions/{acct.subscription}"
    try:
        assignments = az(
            "role", "assignment", "list",
            "--assignee", acct.user,
            "--all", "--include-groups",
            "--subscription", acct.subscription,
        )  # fmt: skip
        definitions = az(
            "role", "definition", "list", "--scope", sub, "--subscription", acct.subscription
        )
    except AzError as exc:
        return [
            warn(
                "azure/permissions",
                f"cannot read the role assignments of the caller ({exc})",
                "confirm Owner, or User Access Administrator plus Contributor, by hand",
            )
        ]
    by_id = {d["name"].lower(): d for d in definitions}

    def definition(a: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return by_id.get(a["roleDefinitionId"].rsplit("/", 1)[-1].lower())

    known = [a for a in assignments if definition(a) is not None]
    targets = action_targets(acct, inputs)
    lacking_pairs = [
        (action, target)
        for action, target in targets
        if action
        not in allowed_actions(
            [definition(a) for a in known if covers(a.get("scope", ""), target)], [action]
        )
    ]
    subnet_assign = (ASSIGN_ACTION, inputs.subnet_id) in lacking_pairs
    lacking = [a for a, t in lacking_pairs if (a, t) != (ASSIGN_ACTION, inputs.subnet_id)]
    roles = sorted({definition(a).get("roleName", "?") for a in known}) or ["none"]
    findings = []
    if any(a in RBAC_ACTIONS for a in lacking) or subnet_assign:
        # A subnet in another group needs 1 more assignment, at the scope of the subnet.
        extra = " and 1 on the subnet" if inputs.subnet_in_other_group else ""
        findings.append(
            fail(
                "azure/permissions",
                f"the caller cannot create roles or role assignments on the subscription"
                f"{' and the subnet' if subnet_assign else ''} "
                f"(roles: {', '.join(roles)}). The stack creates 5 custom roles and 7 role "
                f"assignments{extra}.",
                "get Owner, or User Access Administrator plus Contributor, on the subscription",
            )
        )
    other = [a for a in lacking if a not in RBAC_ACTIONS]
    if other:
        findings.append(
            fail(
                "azure/permissions",
                f"the caller lacks {len(other)} deploy actions: {', '.join(other)}",
                "get Contributor (or Owner) on the subscription",
            )
        )
    unknown = len(assignments) - len(known)
    if unknown:
        findings.append(
            warn(
                "azure/permissions",
                f"{unknown} role assignments of the caller use a role that the check cannot "
                "read at the subscription scope. The check does not count them.",
            )
        )
    if any(a.get("condition") for a in assignments):
        findings.append(
            warn(
                "azure/permissions",
                "a role assignment of the caller has a condition. The check does not apply it.",
            )
        )
    if not lacking_pairs:
        findings.append(
            ok("azure/permissions", f"the roles {', '.join(roles)} allow each deploy action")
        )
    return findings


def check_network_inputs(inputs: AzureInputs) -> Finding:
    """resource_group_name and subnet_id are required and have no default."""
    unset = [n for n in ("resource_group_name", "subnet_id") if not getattr(inputs, n)]
    if unset:
        return fail(
            "azure/network-inputs",
            f"{' and '.join(unset)} not set. The stack deploys into an existing resource group "
            "and an existing subnet.",
            "copy terraform/azure/terraform.tfvars.example to terraform/azure/terraform.tfvars "
            f"and put in your values ({README_PREREQ})",
        )
    return ok("azure/network-inputs", "resource_group_name and subnet_id are set")


def check_resource_group(az: Az, acct: Account, inputs: AzureInputs) -> Finding:
    name = inputs.resource_group_name
    try:
        az("group", "show", "--name", name, "--subscription", acct.subscription)
    except AzError as exc:
        if exc.code == "ResourceGroupNotFound":
            return fail(
                "azure/resource-group",
                f"the resource group {name} does not exist in the subscription",
                "fix resource_group_name, or ask the owner of the group to create it",
            )
        return warn("azure/resource-group", f"cannot read the resource group {name} ({exc})")
    return ok("azure/resource-group", f"the resource group {name} exists")


def check_subnet(
    az: Az, acct: Account, inputs: AzureInputs
) -> tuple[list[Finding], Mapping[str, Any] | None]:
    """The subnet exists, in the subscription of the stack and in `location`."""
    m = _SUBNET_ID.match(inputs.subnet_id or "")
    if m is None:
        return [fail("azure/subnet", "subnet_id is not a subnet resource ID")], None
    if m["sub"].lower() != acct.subscription.lower():
        return [
            fail(
                "azure/subnet",
                "the subnet is in another subscription. A NIC can use only a virtual network "
                "in its own subscription and location.",
                "use a subnet in the subscription of the deploy",
            )
        ], None
    vnet_id = inputs.subnet_id.rsplit("/subnets/", 1)[0]
    try:
        vnet = az("network", "vnet", "show", "--ids", vnet_id)
    except AzError as exc:
        if exc.code in ("ResourceNotFound", "ResourceGroupNotFound"):
            return [
                fail(
                    "azure/subnet",
                    f"the virtual network {m['vnet']} does not exist",
                    "fix subnet_id",
                )
            ], None
        return [warn("azure/subnet", f"cannot read the virtual network {m['vnet']} ({exc})")], None
    subnet = next(
        (x for x in vnet.get("subnets") or [] if x.get("name", "").lower() == m["subnet"].lower()),
        None,
    )
    if subnet is None:
        return [
            fail(
                "azure/subnet",
                f"the virtual network {m['vnet']} has no subnet {m['subnet']}",
                "fix subnet_id",
            )
        ], None
    region = (vnet.get("location") or "").lower()
    if region != inputs.location.lower():
        return [
            fail(
                "azure/subnet",
                f"the subnet is in {region}, but location is {inputs.location}",
                "set location to the region of the subnet, or use a subnet in location",
            )
        ], None
    return [ok("azure/subnet", f"the subnet {m['subnet']} exists in {region}")], subnet


def check_outbound(subnet: Mapping[str, Any]) -> Finding:
    """Outbound internet access is a prerequisite that the network owner sets up.

    The check reads the subnet only. It cannot prove that a path reaches the internet, so it
    never fails. A NAT gateway on the subnet is a PASS. Anything else is a WARN.
    """
    fix = f"ask the network owner for outbound internet access from the subnet ({README_PREREQ})"
    if subnet.get("natGateway"):
        return ok("azure/outbound", "the subnet has a NAT gateway")
    if subnet.get("routeTable"):
        return warn(
            "azure/outbound",
            "the subnet has a route table and no NAT gateway. The check does not read the "
            "routes, so it cannot confirm a path to the internet.",
            fix,
        )
    if subnet.get("defaultOutboundAccess") is False:
        return warn(
            "azure/outbound",
            "the subnet has defaultOutboundAccess false, no NAT gateway, and no route table. "
            "The prober VM cannot reach apt and PyPI.",
            fix,
        )
    return warn(
        "azure/outbound",
        "the subnet has no NAT gateway and no route table. The check cannot confirm a path to "
        "the internet.",
        fix,
    )


NOT_OFFERED = "not offered"


def region_key(region: str) -> str:
    """The form that Azure uses for a Region in a filter: lower case, no spaces."""
    return region.replace(" ", "").lower()


def find_sku(skus: Iterable[Mapping[str, Any]], size: str) -> Mapping[str, Any] | None:
    for sku in skus:
        if sku.get("resourceType") == "virtualMachines" and sku["name"].lower() == size.lower():
            return sku
    return None


def restriction(sku: Mapping[str, Any] | None, region: str) -> str | None:
    """The reason that the subscription cannot use the size in the region, or None."""
    if sku is None:
        return NOT_OFFERED
    for r in sku.get("restrictions") or []:
        locations = [x.lower() for x in (r.get("restrictionInfo") or {}).get("locations") or []]
        values = [x.lower() for x in r.get("values") or []]
        if r.get("type") == "Location" and region.lower() in locations + values:
            return r.get("reasonCode") or "restricted"
    return None


def capability(sku: Mapping[str, Any], name: str) -> str | None:
    for cap in sku.get("capabilities") or []:
        if cap.get("name") == name:
            return cap.get("value")
    return None


def usage_row(usage: Iterable[Mapping[str, Any]], name: str) -> tuple[int, int] | None:
    """(current, limit) of 1 usage row. The az CLI gives both as strings."""
    for row in usage:
        if (row.get("name") or {}).get("value", "").lower() == name.lower():
            return int(row["currentValue"]), int(row["limit"])
    return None


def check_prober_vm(
    skus: list[Mapping[str, Any]], usage: list[Mapping[str, Any]], inputs: AzureInputs
) -> list[Finding]:
    size, loc = inputs.prober_vm_size, inputs.location
    sku = find_sku(skus, size)
    reason = restriction(sku, region_key(loc))
    if reason:
        return [
            fail(
                "azure/prober-sku",
                f"{size} is {reason} for this subscription in {loc} (SkuNotAvailable)",
                f"set prober_vm_size to a size with no restriction in {loc} "
                f"(az vm list-skus -l {loc} --size <size> --all) ({README_QUOTA})",
            )
        ]
    findings = [ok("azure/prober-sku", f"{size} is available in {loc}")]
    family = sku.get("family") or ""
    vcpus = int(capability(sku, "vCPUs") or 0)
    if inputs.prober_deployed:
        findings.append(ok("azure/prober-quota", "the prober VM exists; its usage is counted"))
        return findings
    if int(capability(sku, "GPUs") or 0) > 0:
        findings.append(
            warn(
                "azure/prober-quota",
                f"{size} is a GPU size. The check evaluates no GPU quota.",
                "set prober_vm_size to a small size without a GPU, for example Standard_D2as_v4",
            )
        )
        return findings
    # list-usage returns each family row. The check reads only the prober family and `cores`.
    for row_name, label in ((family, f"the {family} family"), ("cores", "Total Regional vCPUs")):
        row = usage_row(usage, row_name)
        if row is None:
            findings.append(warn("azure/prober-quota", f"no usage row for {label} in {loc}"))
            continue
        current, limit = row
        if limit - current < vcpus:
            findings.append(
                fail(
                    "azure/prober-quota",
                    f"{size} needs {vcpus} vCPUs, but {label} has {limit - current} free "
                    f"in {loc} (limit {limit}, used {current})",
                    f"pick a prober_vm_size whose family has free cores (az vm list-usage "
                    f"-l {loc}), or request quota ({README_QUOTA})",
                )
            )
        else:
            findings.append(
                ok("azure/prober-quota", f"{label} has {limit - current} free vCPUs in {loc}")
            )
    return findings


def _quota_rows(az: Az, subscription: str, region: str) -> list[Mapping[str, Any]]:
    return _pages(
        az,
        f"{ARM}/subscriptions/{subscription}/providers/Microsoft.Web"
        f"/locations/{region}/providers/Microsoft.Quota/quotas?api-version=2023-02-01",
    )


def plan_quota_limit(rows: Iterable[Mapping[str, Any]], sku: str) -> float | None:
    for row in rows:
        props = row.get("properties") or {}
        names = {
            (row.get("name") or "").lower(),
            ((props.get("name") or {}).get("value") or "").lower(),
        }
        if sku.lower() in names:
            limit = props.get("limit") or {}
            value = limit.get("value") if isinstance(limit, Mapping) else limit
            return float(value) if value is not None else None
    return None


def check_sweeper_plan(az: Az, acct: Account, inputs: AzureInputs) -> Finding:
    sku, region = inputs.sweeper_plan_sku, inputs.sweeper_region
    try:
        rows = _quota_rows(az, acct.subscription, region)
    except AzError as exc:
        return warn(
            "azure/sweeper-plan",
            f"cannot read the App Service quota in {region} ({exc})",
            f"register {QUOTA_PROVIDER}, or check the quota in the portal (Quotas page)",
        )
    limit = plan_quota_limit(rows, sku)
    if limit is None:
        return warn("azure/sweeper-plan", f"no quota row for {sku} in {region}; not checked")
    if limit < 1:
        return fail(
            "azure/sweeper-plan",
            f"the {sku} App Service plan quota is {limit:g} in {region}",
            "set sweeper_location to a region with quota, or sweeper_plan_sku to a SKU with "
            f"quota; find one with az quota list --scope /subscriptions/<id>/providers/"
            f"Microsoft.Web/locations/<region> ({README_QUOTA})",
        )
    return ok("azure/sweeper-plan", f"the {sku} plan quota is {limit:g} in {region}")


def check_signals(
    skus_by_region: Mapping[str, list[Mapping[str, Any]]], inputs: AzureInputs
) -> list[Finding]:
    """Sizes that the subscription cannot use give restricted rows. A warning, not a gap."""
    findings = []
    for size in inputs.spot_sizes:
        blocked = [
            f"{region} ({reason})"
            for region in map(region_key, inputs.regions)
            if (reason := restriction(find_sku(skus_by_region.get(region, []), size), region))
        ]
        if blocked:
            findings.append(
                warn(
                    "azure/spot-sizes",
                    f"{size} is not available to this subscription in {', '.join(blocked)}. "
                    "Expect RestrictedSkuNotAvailable in the az_spot_score rows there.",
                    "no fix needed; the rows record it. Ask Microsoft for access to use the size.",
                )
            )
    for entry in inputs.provisioning_probes:
        size, region = entry["sku"], region_key(entry["region"])
        simulated = entry.get("simulate") is True
        reason = restriction(find_sku(skus_by_region.get(region, []), size), region)
        if reason == NOT_OFFERED:
            kind = "simulated" if simulated else "real"
            findings.append(
                fail(
                    "azure/allocation-probes",
                    f"the experiment lists {size} in {region} ({kind}), but {region} does not "
                    "offer it to this subscription. The experiment file lists only probes that "
                    "can run.",
                    "remove the entry from the targets file, or pick a size and a region that "
                    f"az vm list-skus --location {region} --resource-type virtualMachines lists",
                )
            )
        elif reason and not simulated:
            findings.append(
                warn(
                    "azure/allocation-probes",
                    f"the {size} entry in {region} is {reason}. Its rows will show restricted.",
                    "pick another size in config/targets.yaml (see the comment there)",
                )
            )
    if not findings:
        findings.append(ok("azure/signals", "each configured size is available"))
    return findings


def _pages(az: Az, url: str | None) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    while url:
        page = az("rest", "--method", "get", "--url", url)
        rows.extend(page.get("value") or [])
        url = page.get("nextLink")
    return rows


def sku_rows(az: Az, subscription: str, region: str) -> list[Mapping[str, Any]]:
    """The Resource SKUs of 1 region, with a location filter.

    `az vm list-skus` took more than 120 s for 1 region in a test. The REST call with a location
    filter returns only that region.
    """
    query = quote(f"location eq '{region}'")
    return _pages(
        az,
        f"{ARM}/subscriptions/{subscription}/providers/Microsoft.Compute/skus"
        f"?api-version=2021-07-01&$filter={query}",
    )


def run_checks(az: Az, inputs: AzureInputs) -> list[Finding]:
    findings = [check_network_inputs(inputs)]
    first, acct = check_account(az, inputs)
    findings.append(first)
    if acct is None:
        return findings
    regions = sorted(
        {
            region_key(r)
            for r in (
                inputs.location,
                *inputs.regions,
                *(e["region"] for e in inputs.provisioning_probes),
            )
        }
    )
    has_network = bool(inputs.resource_group_name and inputs.subnet_id)
    try:
        findings.extend(check_providers(az, acct, inputs))
        findings.extend(check_permissions(az, acct, inputs))
        if has_network:
            findings.append(check_resource_group(az, acct, inputs))
            subnet_findings, subnet = check_subnet(az, acct, inputs)
            findings.extend(subnet_findings)
            if subnet is not None:
                findings.append(check_outbound(subnet))
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = pool.map(lambda r: sku_rows(az, acct.subscription, r), regions)
            skus_by_region = dict(zip(regions, rows, strict=True))
        usage = az("vm", "list-usage", "--location", inputs.location, "--subscription",
                   acct.subscription)  # fmt: skip
        findings.extend(check_prober_vm(skus_by_region[region_key(inputs.location)], usage, inputs))
        findings.append(check_sweeper_plan(az, acct, inputs))
        findings.extend(check_signals(skus_by_region, inputs))
    except AzError as exc:
        findings.append(fail("azure/api", f"a read call failed ({exc})", "check the az login"))
    return findings
