from __future__ import annotations

from typing import Any

import pytest

from ftg_preflight import azure
from ftg_preflight.report import Level, exit_code
from tests.fakes import FakeAz, az_error, recorded

OWNER = [
    {
        "principalName": "dev@example.com",
        "roleDefinitionId": "/subscriptions/test-subscription/providers/Microsoft.Authorization/"
        "roleDefinitions/owner-id",
        "roleDefinitionName": "Owner",
        "scope": "/subscriptions/test-subscription",
        "condition": None,
    }
]
SUB = "/subscriptions/test-subscription"
SUBNET = f"{SUB}/resourceGroups/network-rg/providers/Microsoft.Network/virtualNetworks/test-vnet/subnets/test-subnet"  # noqa: E501
REGISTERED = [
    {"namespace": n, "registrationState": "Registered"}
    for n in (*azure.AZURERM_REGISTERS, azure.BUDGET_PROVIDER, azure.QUOTA_PROVIDER)
]


def inputs(**kw: Any) -> azure.AzureInputs:
    base = {
        "location": "eastus",
        "prober_vm_size": "Standard_D2as_v4",
        "sweeper_plan_sku": "Y1",
        "resource_group_name": "ftg-rg",
        "subnet_id": SUBNET,
        "sweeper_location": "centralus",
        "regions": ["eastus", "centralus"],
        "spot_sizes": ["Standard_NC4as_T4_v3"],
        "provisioning_probes": [
            {"sku": "Standard_D2as_v4", "region": "eastus"},
            {"sku": "Standard_NC4as_T4_v3", "region": "centralus", "simulate": True},
        ],
    }
    base.update(kw)
    return azure.AzureInputs(**base)


def page(rows: list[Any]) -> dict[str, Any]:
    return {"value": rows, "nextLink": None}


def vnet(**subnet: Any) -> dict[str, Any]:
    """The recorded virtual network, with fields of the subnet test-subnet replaced."""
    data = recorded("azure", "vnet")
    target = data["subnets"][1]
    target.update(subnet)
    for key in [k for k, v in target.items() if v is None]:
        del target[key]
    return data


def fake(**overrides: Any) -> FakeAz:
    replies = {
        "account show": recorded("azure", "account"),
        "provider list": REGISTERED,
        "role assignment list": OWNER,
        "role definition list": recorded("azure", "role_definitions"),
        "group show": recorded("azure", "group"),
        "network vnet show": recorded("azure", "vnet"),
        "rest skus eastus": page(recorded("azure", "skus_eastus")),
        "rest skus centralus": page(recorded("azure", "skus_centralus")),
        "rest skus eastus2": page([]),
        "vm list-usage eastus": recorded("azure", "usage_eastus"),
        "rest quota eastus": recorded("azure", "web_quota_eastus"),
        "rest quota centralus": recorded("azure", "web_quota_centralus"),
        "rest quota centralus page2": recorded("azure", "web_quota_centralus_page2"),
    }
    replies.update({k.replace("_", " "): v for k, v in overrides.items()})
    return FakeAz(replies)


def by_check(findings, check: str):
    return [f for f in findings if f.check == check]


def test_all_checks_pass():
    findings = azure.run_checks(fake(), inputs())
    assert [f for f in findings if f.level is not Level.PASS] == []
    assert exit_code(findings) == 0


def test_not_logged_in_stops_early():
    az = fake(account_show=az_error("Please run 'az login' to setup account."))
    findings = azure.run_checks(az, inputs())
    assert [f.check for f in findings] == ["azure/network-inputs", "azure/login"]
    assert findings[1].level is Level.FAIL
    assert len(az.calls) == 1


def test_arm_subscription_id_selects_the_subscription():
    az = fake()
    azure.run_checks(az, inputs(subscription="test-subscription"))
    assert az.calls[0] == ("account", "show", "--subscription", "test-subscription")


def test_unregistered_providers():
    az = fake(provider_list=recorded("azure", "providers"))
    findings = azure.run_checks(az, inputs(alert_email="ops@example.com"))
    prov = by_check(findings, "azure/providers")
    assert [f.level for f in prov] == [Level.WARN, Level.FAIL]
    assert "Microsoft.Web" in prov[0].message and "insights" not in prov[0].message.lower()
    assert prov[0].fix == "az provider register --namespace Microsoft.Web"
    assert "Microsoft.Consumption" in prov[1].message


def test_consumption_needed_only_with_alert_email():
    az = fake(provider_list=recorded("azure", "providers"))
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/providers")] == [Level.WARN]


def test_contributor_cannot_create_role_assignments():
    az = fake(role_assignment_list=recorded("azure", "assignments_contributor"))
    findings = azure.run_checks(az, inputs())
    perm = by_check(findings, "azure/permissions")
    assert [f.level for f in perm] == [Level.FAIL]
    assert "Contributor" in perm[0].message
    assert "User Access Administrator plus Contributor" in perm[0].fix
    assert exit_code(findings) == 1


def test_permission_message_counts_the_custom_roles_in_the_stack():
    from pathlib import Path

    rbac = Path(__file__).parents[2] / "terraform" / "azure" / "rbac.tf"
    count = rbac.read_text().count('resource "azurerm_role_definition"')
    az = fake(role_assignment_list=recorded("azure", "assignments_contributor"))
    perm = by_check(azure.run_checks(az, inputs()), "azure/permissions")
    assert f"creates {count} custom roles" in perm[0].message


def test_contributor_plus_inherited_group_uaa_passes():
    az = fake(role_assignment_list=recorded("azure", "assignments_contributor_uaa"))
    findings = azure.run_checks(az, inputs(alert_email="ops@example.com"))
    perm = by_check(findings, "azure/permissions")
    assert [f.level for f in perm] == [Level.PASS]
    assert "Contributor, User Access Administrator" in perm[0].message
    call = next(c for c in az.calls if c[:3] == ("role", "assignment", "list"))
    assert "--include-groups" in call and "--all" in call


def test_reader_lacks_the_create_actions():
    reader = [dict(OWNER[0], roleDefinitionId="/x/roleDefinitions/reader-id")]
    findings = azure.run_checks(fake(role_assignment_list=reader), inputs())
    assert [f.level for f in by_check(findings, "azure/permissions")] == [Level.FAIL, Level.FAIL]


def test_condition_on_an_assignment_is_a_warning():
    az = fake(role_assignment_list=recorded("azure", "assignments_owner_with_condition"))
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/permissions")] == [Level.WARN, Level.PASS]


def test_role_assignment_read_error_is_a_warning():
    az = fake(role_assignment_list=az_error("AuthorizationFailed"))
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/permissions")] == [Level.WARN]


def test_restricted_prober_size_fails():
    findings = azure.run_checks(fake(), inputs(prober_vm_size="Standard_B2s"))
    sku = by_check(findings, "azure/prober-sku")
    assert [f.level for f in sku] == [Level.FAIL]
    assert "NotAvailableForSubscription" in sku[0].message
    assert by_check(findings, "azure/prober-quota") == []


def test_zone_restriction_does_not_block_the_prober():
    findings = azure.run_checks(fake(), inputs())
    assert [f.level for f in by_check(findings, "azure/prober-sku")] == [Level.PASS]


def test_family_quota_zero_fails():
    findings = azure.run_checks(fake(), inputs(prober_vm_size="Standard_B2ats_v2"))
    quota = by_check(findings, "azure/prober-quota")
    assert [f.level for f in quota] == [Level.FAIL, Level.PASS]
    assert "standardBasv2Family family has 0 free" in quota[0].message


def test_gpu_prober_size_evaluates_no_quota_row():
    skus = recorded("azure", "skus_eastus")
    t4 = next(x for x in skus if x["name"] == "Standard_NC4as_T4_v3")
    t4["capabilities"].append({"name": "GPUs", "value": "1"})
    findings = azure.run_checks(
        fake(rest_skus_eastus=page(skus)), inputs(prober_vm_size="Standard_NC4as_T4_v3")
    )
    quota = by_check(findings, "azure/prober-quota")
    assert [f.level for f in quota] == [Level.WARN]
    assert "evaluates no GPU quota" in quota[0].message


def test_deployed_prober_skips_the_free_quota_check():
    findings = azure.run_checks(
        fake(), inputs(prober_vm_size="Standard_B2ats_v2", prober_deployed=True)
    )
    assert [f.level for f in by_check(findings, "azure/prober-quota")] == [Level.PASS]


def test_sweeper_quota_zero_in_the_control_region_fails():
    findings = azure.run_checks(fake(), inputs(sweeper_location=None))
    plan = by_check(findings, "azure/sweeper-plan")
    assert [f.level for f in plan] == [Level.FAIL]
    assert "Y1 App Service plan quota is 0 in eastus" in plan[0].message
    assert "sweeper_location" in plan[0].fix


def test_sweeper_quota_follows_next_link():
    az = fake()
    findings = azure.run_checks(az, inputs(sweeper_plan_sku="B1"))
    assert [f.level for f in by_check(findings, "azure/sweeper-plan")] == [Level.PASS]
    assert sum(1 for c in az.calls if FakeAz.key(c).startswith("rest quota")) == 2


def test_sweeper_quota_unreadable_is_a_warning():
    az = fake(rest_quota_centralus=az_error("(MissingSubscriptionRegistration)"))
    findings = azure.run_checks(az, inputs())
    plan = by_check(findings, "azure/sweeper-plan")
    assert [f.level for f in plan] == [Level.WARN]
    assert "Microsoft.Quota" in plan[0].fix


def test_sweeper_quota_without_a_row_is_a_warning():
    findings = azure.run_checks(fake(), inputs(sweeper_plan_sku="S1"))
    assert [f.level for f in by_check(findings, "azure/sweeper-plan")] == [Level.WARN]


def test_restricted_spot_size_is_a_warning():
    findings = azure.run_checks(fake(), inputs(spot_sizes=["Standard_ND96isr_H100_v5"]))
    spot = by_check(findings, "azure/spot-sizes")
    assert [f.level for f in spot] == [Level.WARN]
    assert "eastus (NotAvailableForSubscription)" in spot[0].message
    assert "centralus" not in spot[0].message
    assert exit_code(findings) == 0


def test_restricted_allocation_probe_is_a_warning_and_a_simulated_entry_is_skipped():
    probes = [
        {"sku": "Standard_B2s", "region": "eastus"},
        {"sku": "Standard_B2s", "region": "eastus", "simulate": True},
    ]
    findings = azure.run_checks(fake(), inputs(provisioning_probes=probes))
    warnings = by_check(findings, "azure/allocation-probes")
    assert [f.level for f in warnings] == [Level.WARN]
    assert "eastus is NotAvailableForSubscription" in warnings[0].message
    assert exit_code(findings) == 0


def test_probe_size_that_the_region_does_not_offer_fails_one_line_per_entry():
    probes = [
        {"sku": "Standard_NC4as_T4_v3", "region": "eastus"},
        {"sku": "Standard_NC24ads_A100_v4", "region": "centralus"},
        {"sku": "Standard_NC24ads_A100_v4", "region": "centralus", "simulate": True},
        {"sku": "Standard_B2s", "region": "eastus"},
    ]
    findings = azure.run_checks(fake(), inputs(provisioning_probes=probes))
    rows = by_check(findings, "azure/allocation-probes")
    failed = [f for f in rows if f.level is Level.FAIL]
    assert len(failed) == 2
    assert failed[0].message.startswith(
        "the experiment lists Standard_NC24ads_A100_v4 in centralus (real), but centralus "
        "does not offer it to this subscription."
    )
    assert "(simulated)" in failed[1].message
    assert "scale set" not in failed[0].message
    assert "az vm list-skus --location centralus" in failed[0].fix
    # A restricted size is offered: a warning, never a failure.
    assert [f.level for f in rows if "Standard_B2s" in f.message] == [Level.WARN]
    assert exit_code(findings) == 1


def test_location_restricted_size_in_the_rest_list_warns_and_does_not_fail():
    skus = recorded("azure", "skus_eastus") + [recorded("azure", "skus_eastus_a100_restricted")]
    probes = [
        {"sku": "Standard_NC24ads_A100_v4", "region": "eastus"},
        {"sku": "Standard_NC24ads_A100_v4", "region": "eastus", "simulate": True},
    ]
    findings = azure.run_checks(
        fake(rest_skus_eastus=page(skus)), inputs(provisioning_probes=probes)
    )
    rows = by_check(findings, "azure/allocation-probes")
    assert [f.level for f in rows] == [Level.WARN]
    assert "NotAvailableForSubscription" in rows[0].message
    assert exit_code(findings) == 0


def test_region_in_the_experiment_file_may_have_upper_case_or_spaces():
    probes = [
        {"sku": "Standard_NC4as_T4_v3", "region": "EastUS"},
        {"sku": "Standard_NC24ads_A100_v4", "region": "Central US"},
    ]
    az = fake()
    findings = azure.run_checks(az, inputs(provisioning_probes=probes))
    rows = by_check(findings, "azure/allocation-probes")
    assert [f.level for f in rows] == [Level.FAIL]
    assert "in centralus (real)" in rows[0].message
    keys = {FakeAz.key(c) for c in az.calls if "skus" in FakeAz.key(c)}
    assert keys == {"rest skus eastus", "rest skus centralus"}


def test_probe_entry_in_a_region_with_an_empty_size_list_fails():
    probes = [{"sku": "Standard_NC4as_T4_v3", "region": "eastus2"}]
    findings = azure.run_checks(fake(), inputs(provisioning_probes=probes))
    assert [f.level for f in by_check(findings, "azure/allocation-probes")] == [Level.FAIL]


def test_az_vm_list_skus_without_all_omits_location_restricted_sizes():
    # Recorded from a real `az vm list-skus --location eastus --resource-type virtualMachines
    # -o json` (exit 0, empty stderr): 2 of its entries, without the 12-digit byte counts of
    # the capabilities. The CLI without --all omits a size that has a
    # Location restriction: Standard_NC24ads_A100_v4 is in the REST list of eastus, restricted,
    # and not in this output. So the check reads the REST list, not this output.
    rows = recorded("azure", "list_skus_eastus_real")
    assert azure.restriction(azure.find_sku(rows, "Standard_NC4as_T4_v3"), "eastus") is None
    assert azure.restriction(azure.find_sku(rows, "Standard_D2as_v4"), "eastus") is None
    missing = azure.find_sku(rows, "Standard_NC24ads_A100_v4")
    assert azure.restriction(missing, "eastus") == azure.NOT_OFFERED


def test_each_region_is_read_once_with_a_location_filter():
    az = fake()
    azure.run_checks(az, inputs())
    keys = sorted(FakeAz.key(c) for c in az.calls if "skus" in FakeAz.key(c))
    assert keys == ["rest skus centralus", "rest skus eastus"]
    assert not any(c[:2] == ("vm", "list-skus") for c in az.calls)


def test_sku_list_follows_next_link():
    first = {"value": [], "nextLink": f"https://management.azure.com{SUB}/skus-page-2"}
    az = fake(rest_skus_eastus=first)
    az.replies["rest skus page2"] = page(recorded("azure", "skus_eastus"))
    original = FakeAz.key

    def key(args):
        url = args[args.index("--url") + 1] if args[0] == "rest" else ""
        return "rest skus page2" if url.endswith("skus-page-2") else original(args)

    az.key = key
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/prober-sku")] == [Level.PASS]


@pytest.mark.parametrize("unset", [{"resource_group_name": None}, {"subnet_id": None}])
def test_unset_network_variable_fails_and_skips_the_network_reads(unset):
    az = fake()
    findings = azure.run_checks(az, inputs(**unset))
    net = by_check(findings, "azure/network-inputs")
    assert [f.level for f in net] == [Level.FAIL]
    want = "copy terraform/azure/terraform.tfvars.example to terraform/azure/terraform.tfvars"
    assert want in net[0].fix
    assert not any(c[0] in ("group", "network") for c in az.calls)
    assert exit_code(findings) == 1


def test_missing_resource_group_fails():
    az = fake(
        group_show=az_error("Resource group 'ftg-rg' could not be found.", "ResourceGroupNotFound")
    )
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/resource-group")] == [Level.FAIL]


def test_unreadable_resource_group_is_a_warning():
    az = fake(group_show=az_error("does not have authorization", "AuthorizationFailed"))
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/resource-group")] == [Level.WARN]


def test_missing_virtual_network_fails():
    az = fake(network_vnet_show=az_error("was not found", "ResourceNotFound"))
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/subnet")] == [Level.FAIL]
    assert by_check(findings, "azure/outbound") == []


def test_missing_subnet_fails():
    findings = azure.run_checks(fake(), inputs(subnet_id=SUBNET.replace("test-subnet", "nope")))
    sub = by_check(findings, "azure/subnet")
    assert [f.level for f in sub] == [Level.FAIL]
    assert "has no subnet nope" in sub[0].message


def test_subnet_in_another_region_fails():
    data = recorded("azure", "vnet")
    data["location"] = "westus2"
    findings = azure.run_checks(fake(network_vnet_show=data), inputs())
    sub = by_check(findings, "azure/subnet")
    assert [f.level for f in sub] == [Level.FAIL]
    assert "westus2" in sub[0].message and "eastus" in sub[0].message


def test_subnet_in_another_subscription_fails_without_a_read():
    az = fake()
    other = SUBNET.replace("test-subscription", "other-subscription")
    findings = azure.run_checks(az, inputs(subnet_id=other))
    sub = by_check(findings, "azure/subnet")
    assert [f.level for f in sub] == [Level.FAIL]
    assert "other-subscription" not in sub[0].message
    assert not any(c[0] == "network" for c in az.calls)


@pytest.mark.parametrize(
    ("subnet", "text"),
    [
        ({"natGateway": None, "routeTable": {"id": "rt"}}, "has a route table"),
        ({"natGateway": None, "defaultOutboundAccess": False}, "defaultOutboundAccess false"),
        ({"natGateway": None}, "no NAT gateway and no route table"),
    ],
)
def test_outbound_gaps_are_warnings(subnet, text):
    findings = azure.run_checks(fake(network_vnet_show=vnet(**subnet)), inputs())
    out = by_check(findings, "azure/outbound")
    assert [f.level for f in out] == [Level.WARN]
    assert text in out[0].message
    assert "network owner" in out[0].fix
    assert exit_code(findings) == 0


def test_group_contributor_cannot_join_a_subnet_in_another_group():
    az = fake(role_assignment_list=recorded("azure", "assignments_group_contributor_sub_uaa"))
    findings = azure.run_checks(az, inputs())
    perm = by_check(findings, "azure/permissions")
    assert [f.level for f in perm] == [Level.FAIL]
    assert perm[0].message.endswith(azure.JOIN_ACTION)


def test_group_contributor_can_join_a_subnet_in_the_same_group():
    az = fake(role_assignment_list=recorded("azure", "assignments_group_contributor_sub_uaa"))
    subnet = SUBNET.replace("network-rg", "ftg-rg")
    findings = azure.run_checks(az, inputs(subnet_id=subnet))
    assert [f.level for f in by_check(findings, "azure/permissions")] == [Level.PASS]


def test_role_actions_need_the_subscription_scope():
    group_owner = [dict(OWNER[0], scope=f"{SUB}/resourceGroups/ftg-rg")]
    findings = azure.run_checks(
        fake(role_assignment_list=group_owner),
        inputs(subnet_id=SUBNET.replace("network-rg", "ftg-rg")),
    )
    perm = by_check(findings, "azure/permissions")
    assert [f.level for f in perm] == [Level.FAIL]
    assert "on the subscription" in perm[0].message


def acct() -> azure.Account:
    return azure.Account("test-subscription", "test", "dev@example.com", "user")


def test_role_assignment_write_is_needed_on_a_subnet_in_another_group():
    pair = (azure.ASSIGN_ACTION, SUBNET)
    assert pair in azure.action_targets(acct(), inputs())
    # The group name differs in letter case only: the same group.
    same = SUBNET.replace("network-rg", "FTG-RG")
    assert (azure.ASSIGN_ACTION, same) not in azure.action_targets(acct(), inputs(subnet_id=same))


def test_owner_of_the_group_only_cannot_assign_a_role_on_the_subnet():
    group_owner = [dict(OWNER[0], scope=f"{SUB}/resourceGroups/ftg-rg")]
    findings = azure.run_checks(fake(role_assignment_list=group_owner), inputs())
    perm = by_check(findings, "azure/permissions")
    assert all(f.level is Level.FAIL for f in perm)
    assert "and the subnet" in perm[0].message
    assert "1 on the subnet" in perm[0].message


def test_subnet_role_assignment_passes_with_owner_on_the_subscription():
    findings = azure.run_checks(fake(), inputs())
    assert [f.level for f in by_check(findings, "azure/permissions")] == [Level.PASS]


def test_assignment_with_an_unknown_role_is_a_warning():
    custom = [dict(OWNER[0], roleDefinitionId="/x/roleDefinitions/custom-id")]
    az = fake(role_assignment_list=OWNER + custom)
    findings = azure.run_checks(az, inputs())
    assert [f.level for f in by_check(findings, "azure/permissions")] == [Level.WARN, Level.PASS]


def test_deploy_actions_leave_out_group_and_network_creation():
    joined = " ".join(azure.DEPLOY_ACTIONS)
    assert "resourceGroups/write" not in joined
    assert "virtualNetworks/write" not in joined
    assert "networkSecurityGroups/write" not in joined


@pytest.mark.parametrize(
    ("scope", "target", "result"),
    [
        (SUB, f"{SUB}/resourceGroups/ftg-rg", True),
        (f"{SUB}/resourceGroups/ftg-rg", SUB, False),
        (f"{SUB}/resourceGroups/ftg", f"{SUB}/resourceGroups/ftg-rg", False),
        ("/providers/Microsoft.Management/managementGroups/test-group", SUB, True),
        ("/", SUB, True),
    ],
)
def test_covers(scope, target, result):
    assert azure.covers(scope, target) is result


def test_allowed_actions_apply_not_actions_per_block():
    defs = recorded("azure", "role_definitions")
    contributor = [d for d in defs if d["roleName"] == "Contributor"]
    granted = azure.allowed_actions(contributor, azure.DEPLOY_ACTIONS)
    assert "Microsoft.Authorization/roleAssignments/write" not in granted
    assert "Microsoft.Compute/virtualMachines/write" in granted


def test_az_cli_error_keeps_the_azure_code(monkeypatch):
    import subprocess

    stderr = (
        "ERROR: (ResourceGroupNotFound) Resource group 'ftg-rg' could not be found.\n"
        "Code: ResourceGroupNotFound\n"
        "Message: Resource group 'ftg-rg' could not be found.\n"
    )
    monkeypatch.setattr(
        azure.subprocess,
        "run",
        lambda args, **_: subprocess.CompletedProcess(args, 3, "", stderr),
    )
    with pytest.raises(azure.AzError) as info:
        azure.az_cli("group", "show", "--name", "ftg-rg")
    assert info.value.code == "ResourceGroupNotFound"
    assert str(info.value) == "Message: Resource group 'ftg-rg' could not be found."
