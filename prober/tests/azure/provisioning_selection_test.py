"""A provisioning probe selects its Azure resource by tags, never by a computed name. ADR 0022.

The scale set of an `az_vmss_flex` entry and the reservation group of an `az_capacity_reservation`
entry are found in the probe resource group by the tags `experiment`, `sku`, and `region`
(`experiment` and `region` for the group). Exactly 1 match is probed. No match, more than 1 match,
or a failed list gives 1 `error` row and no Azure write.
"""

from types import SimpleNamespace as NS

import pytest

from ftg_prober.azure.capacity_reservation import CapacityReservationProbe
from ftg_prober.azure.prechecks import PrecheckProbe
from ftg_prober.azure.provisioning_adapter import (
    AzureAllocationAdapter,
    AzureCallError,
    ReservationGroupInfo,
    ScaleSetInfo,
)
from ftg_prober.azure.vmss_flex import VmssFlexProbe
from tests.azure.provisioning_fakes import (
    FakeAdapter,
    FakeClock,
    PlannedMember,
    b2s,
    entry,
    make_ctx,
    usages,
    vmss_tags,
    wire,
)
from tests.conftest import import_or_skip

# Calls that change Azure. A refused selection makes none of them.
WRITES = {
    "create_reservation",
    "delete_reservation",
    "update_scale_set",
    "delete_vm",
    "tag_resource",
}


def scale_set(name, **tags):
    return ScaleSetInfo(
        id=f"/fake/virtualMachineScaleSets/{name}",
        name=name,
        capacity=0,
        sku_name="Standard_B2s",
        tags=tags,
    )


def vmss_one(adapter, **section):
    (row,) = wire(VmssFlexProbe(), adapter).run(make_ctx([entry()], **section))
    return row


def crg_one(adapter, **section):
    (row,) = wire(CapacityReservationProbe(), adapter).run(make_ctx([entry()], **section))
    return row


def made_no_write(adapter):
    return not any(call[0] in WRITES for call in adapter.calls)


# --- az_vmss_flex ---


def test_the_scale_set_is_selected_by_tags_with_an_arbitrary_name():
    name = "any-name-the-network-team-likes"
    adapter = FakeAdapter(
        FakeClock(),
        scale_sets=[scale_set(name, **vmss_tags())],
        plan=[PlannedMember("placed")] * 2,
    )
    row = vmss_one(adapter)
    assert (row.outcome, row.n_placed) == ("success", 2)
    assert row.details["scale_set"] == name
    assert ("update_scale_set", name, 0, None) in adapter.calls
    assert not any(c[0] == "get_scale_set" and c[2] != name for c in adapter.calls)


def test_a_scale_set_of_another_experiment_or_an_untagged_one_is_ignored():
    ours = scale_set("ours", **vmss_tags())
    adapter = FakeAdapter(
        FakeClock(),
        scale_sets=[
            scale_set("other-experiment", **{**vmss_tags(), "experiment": "ftg-20260101-other"}),
            scale_set("untagged"),
            scale_set("other-size", **vmss_tags(sku="Standard_D2s_v5")),
            scale_set("other-region", **vmss_tags(region="westus")),
            ours,
        ],
        plan=[PlannedMember("placed")] * 2,
    )
    assert vmss_one(adapter).details["scale_set"] == "ours"


def test_the_tags_are_compared_in_lower_case():
    tags = vmss_tags(sku="STANDARD_B2S", region="EastUS")
    adapter = FakeAdapter(
        FakeClock(), scale_sets=[scale_set("mixed", **tags)], plan=[PlannedMember("placed")] * 2
    )
    assert vmss_one(adapter).details["scale_set"] == "mixed"


def test_no_matching_scale_set_gives_an_error_row_and_no_write():
    adapter = FakeAdapter(FakeClock(), scale_sets=[scale_set("untagged")])
    row = vmss_one(adapter)
    assert (row.outcome, row.n_placed, row.error_code) == ("error", None, None)
    assert row.details["reason"] == "no scale set for this experiment entry"
    assert row.details["match"] == {"experiment": "ftg", "sku": "Standard_B2s", "region": "eastus"}
    assert row.details["scale_sets"] == []
    assert made_no_write(adapter)


def test_2_matching_scale_sets_give_an_ambiguous_error_row_and_no_write():
    adapter = FakeAdapter(
        FakeClock(),
        scale_sets=[scale_set("b-second", **vmss_tags()), scale_set("a-first", **vmss_tags())],
    )
    row = vmss_one(adapter)
    assert (row.outcome, row.n_placed, row.error_code) == ("error", None, None)
    assert row.details["reason"] == "ambiguous: 2 scale sets match this experiment entry"
    assert row.details["scale_sets"] == ["a-first", "b-second"]
    assert made_no_write(adapter)


def test_a_failed_list_gives_an_error_row_with_the_code_and_no_write():
    adapter = FakeAdapter(
        FakeClock(), list_scale_sets_error=AzureCallError("AuthorizationFailed", "provider text")
    )
    row = vmss_one(adapter)
    assert (row.outcome, row.n_placed, row.error_code) == ("error", None, "AuthorizationFailed")
    assert row.details["reason"] == "scale sets not listable"
    assert row.details["scale_sets"] == []
    assert made_no_write(adapter)
    assert "provider text" not in row.to_json()  # the message goes to the log, not the row


@pytest.mark.parametrize(
    ("adapter_args", "outcome"),
    [
        ({"usage": usages(family_free=1)}, "quota"),
        ({"skus": [b2s(name="Standard_D2s_v5")]}, "restricted"),
    ],
)
def test_the_precheck_row_is_written_before_any_list_call(adapter_args, outcome):
    """A quota 0 or a restricted entry never needs its scale set."""
    adapter = FakeAdapter(FakeClock(), scale_sets=[], **adapter_args)
    row = vmss_one(adapter)
    assert row.outcome == outcome
    assert not any(c[0] == "list_scale_sets" for c in adapter.calls)


def test_each_type_prechecks_only_its_own_entries():
    targets = [
        entry(probe_type="az_vmss_flex", region="eastus"),
        entry(probe_type="az_capacity_reservation", region="eastus2"),
    ]
    adapter = FakeAdapter(FakeClock(), plan=[PlannedMember("placed")] * 2)
    wire(VmssFlexProbe(), adapter).run(make_ctx(targets))
    assert [c for c in adapter.calls if c[0] == "list_skus"] == [("list_skus", "eastus")]
    adapter = FakeAdapter(FakeClock())
    wire(CapacityReservationProbe(), adapter).run(make_ctx(targets))
    assert [c for c in adapter.calls if c[0] == "list_skus"] == [("list_skus", "eastus2")]


def test_the_precheck_probe_covers_the_entries_of_both_types():
    targets = [
        entry(probe_type="az_vmss_flex"),
        entry(probe_type="az_capacity_reservation"),
    ]
    rows = wire(PrecheckProbe(), FakeAdapter(FakeClock())).run(make_ctx(targets))
    assert [r.details["entry_probe_type"] for r in rows] == [
        "az_vmss_flex",
        "az_capacity_reservation",
    ]


def test_the_probe_type_decides_not_the_capability_flag():
    """An H100 under `az_capacity_reservation` makes a reservation, although the size reports no
    reservation support; an A100 under `az_vmss_flex` scales a scale set, although the size
    supports reservations (ADR 0022)."""
    h100 = b2s(name="Standard_NC40ads_H100_v5", capacity_reservation_supported=False)
    adapter = FakeAdapter(FakeClock(), skus=[h100])
    target = entry(probe_type="az_capacity_reservation", sku=h100.name)
    (row,) = wire(CapacityReservationProbe(), adapter).run(make_ctx([target]))
    assert (row.probe_type, row.outcome) == ("az_capacity_reservation", "success")
    assert any(c[0] == "create_reservation" for c in adapter.calls)

    a100 = b2s(name="Standard_NC24ads_A100_v4", capacity_reservation_supported=True)
    adapter = FakeAdapter(FakeClock(), skus=[a100], plan=[PlannedMember("placed")] * 2)
    target = entry(probe_type="az_vmss_flex", sku=a100.name)
    (row,) = wire(VmssFlexProbe(), adapter).run(make_ctx([target]))
    assert (row.probe_type, row.outcome) == ("az_vmss_flex", "success")
    assert any(c[0] == "update_scale_set" and c[2] == 2 for c in adapter.calls)


# --- az_capacity_reservation ---


def group(name, **tags):
    return ReservationGroupInfo(name, tags)


def test_the_reservation_group_is_selected_by_tags_with_an_arbitrary_name():
    tags = {"experiment": "ftg", "region": "eastus"}
    adapter = FakeAdapter(
        FakeClock(),
        reservation_groups=[
            group("group-of-another-experiment", experiment="ftg-20260101-other", region="eastus"),
            group("untagged"),
            group("group-of-another-region", experiment="ftg", region="westus"),
            group("the-one", **tags),
        ],
    )
    row = crg_one(adapter)
    assert row.outcome == "success"
    assert row.details["reservation_group"] == "the-one"
    assert row.details["match"] == tags
    create = next(c for c in adapter.calls if c[0] == "create_reservation")
    assert create[2] == "the-one"


def test_no_matching_reservation_group_gives_an_error_row_and_no_write():
    adapter = FakeAdapter(FakeClock(), reservation_groups=[group("untagged")])
    row = crg_one(adapter)
    assert (row.outcome, row.n_placed, row.error_code) == ("error", None, None)
    assert row.details["reason"] == "no reservation group for this experiment entry"
    assert row.details["reservation_groups"] == []
    assert made_no_write(adapter)


def test_2_matching_reservation_groups_give_an_ambiguous_error_row_and_no_write():
    tags = {"experiment": "ftg", "region": "eastus"}
    adapter = FakeAdapter(
        FakeClock(), reservation_groups=[group("two", **tags), group("one", **tags)]
    )
    row = crg_one(adapter)
    assert row.outcome == "error"
    assert row.details["reason"] == "ambiguous: 2 reservation groups match this experiment entry"
    assert row.details["reservation_groups"] == ["one", "two"]
    assert made_no_write(adapter)


def test_a_failed_group_list_gives_an_error_row_with_the_code_and_no_write():
    adapter = FakeAdapter(FakeClock(), list_groups_error=AzureCallError("Throttled", "slow down"))
    row = crg_one(adapter)
    assert (row.outcome, row.error_code) == ("error", "Throttled")
    assert row.details["reason"] == "reservation groups not listable"
    assert made_no_write(adapter)


def test_the_reservation_precheck_runs_before_the_group_list():
    adapter = FakeAdapter(FakeClock(), usage=usages(family_free=1), reservation_groups=[])
    assert crg_one(adapter).outcome == "quota"
    assert not any(c[0] == "list_reservation_groups" for c in adapter.calls)


# --- fake against real: the shape of the SDK list responses ---

# The REST shape of a list response, with the IDs scrubbed. The SDK deserializes it into models.
SCALE_SET_LIST = {
    "value": [
        {
            "name": "a-scale-set",
            "id": "/subscriptions/SUBSCRIPTION/resourceGroups/RG/providers/Microsoft.Compute"
            "/virtualMachineScaleSets/a-scale-set",
            "type": "Microsoft.Compute/virtualMachineScaleSets",
            "location": "eastus2",
            "tags": {
                "experiment": "ftg",
                "sku": "Standard_NC40ads_H100_v5",
                "region": "eastus2",
                "target": "nc40ads-h100-v5-eastus2-multi",
                "run_id": "none",
            },
            "sku": {"name": "Standard_NC40ads_H100_v5", "tier": "Standard", "capacity": 0},
            "properties": {"orchestrationMode": "Flexible", "provisioningState": "Succeeded"},
        },
        {
            "name": "a-scale-set-without-sku",
            "id": "/subscriptions/SUBSCRIPTION/resourceGroups/RG/providers/Microsoft.Compute"
            "/virtualMachineScaleSets/a-scale-set-without-sku",
            "type": "Microsoft.Compute/virtualMachineScaleSets",
            "location": "eastus2",
            "tags": {"owner": "another-team"},
        },
        {
            "name": "an-untagged-scale-set",
            "id": "/subscriptions/SUBSCRIPTION/resourceGroups/RG/providers/Microsoft.Compute"
            "/virtualMachineScaleSets/an-untagged-scale-set",
            "type": "Microsoft.Compute/virtualMachineScaleSets",
            "location": "eastus2",
            "sku": {"name": "Standard_B2s", "tier": "Standard", "capacity": 3},
            "properties": {"orchestrationMode": "Flexible"},
        },
    ]
}
GROUP_LIST = {
    "value": [
        {
            "name": "a-group",
            "id": "/subscriptions/SUBSCRIPTION/resourceGroups/RG/providers/Microsoft.Compute"
            "/capacityReservationGroups/a-group",
            "type": "Microsoft.Compute/capacityReservationGroups",
            "location": "eastus2",
            "tags": {"experiment": "ftg", "region": "eastus2"},
        },
        {
            "name": "an-untagged-group",
            "id": "/subscriptions/SUBSCRIPTION/resourceGroups/RG/providers/Microsoft.Compute"
            "/capacityReservationGroups/an-untagged-group",
            "type": "Microsoft.Compute/capacityReservationGroups",
            "location": "eastus2",
        },
    ]
}


def real_adapter():
    """The real adapter on the real SDK models, built from the REST payloads. The list calls
    check their arguments against the signatures of the real SDK operations."""
    import inspect

    models = import_or_skip("azure.mgmt.compute.models")
    from azure.mgmt.compute.operations import (
        CapacityReservationGroupsOperations,
        VirtualMachineScaleSetsOperations,
    )

    def list_scale_sets(*args, **kwargs):
        inspect.signature(VirtualMachineScaleSetsOperations.list).bind(NS(), *args, **kwargs)
        return iter([models.VirtualMachineScaleSet(v) for v in SCALE_SET_LIST["value"]])

    def list_groups(*args, **kwargs):
        inspect.signature(CapacityReservationGroupsOperations.list_by_resource_group).bind(
            NS(), *args, **kwargs
        )
        return iter([models.CapacityReservationGroup(v) for v in GROUP_LIST["value"]])

    compute = NS(
        virtual_machine_scale_sets=NS(list=list_scale_sets),
        capacity_reservation_groups=NS(list_by_resource_group=list_groups),
    )
    return AzureAllocationAdapter(compute, lambda: None)


def test_the_real_adapter_reads_the_tags_of_the_real_list_models():
    adapter = real_adapter()
    tagged, no_sku, untagged = adapter.list_scale_sets("rg")
    assert (no_sku.name, no_sku.capacity, no_sku.sku_name) == (
        "a-scale-set-without-sku",
        None,
        None,
    )
    assert tagged.name == "a-scale-set"
    assert tagged.tags["sku"] == "Standard_NC40ads_H100_v5"
    assert (tagged.capacity, tagged.sku_name) == (0, "Standard_NC40ads_H100_v5")
    assert (untagged.tags, untagged.capacity) == ({}, 3)  # no `tags` in the payload: an empty dict
    tagged_group, untagged_group = adapter.list_reservation_groups("rg")
    assert (tagged_group.name, tagged_group.tags) == (
        "a-group",
        {"experiment": "ftg", "region": "eastus2"},
    )
    assert untagged_group.tags == {}


def test_the_fake_and_the_real_adapter_return_the_same_types():
    real = real_adapter()
    fake = FakeAdapter(FakeClock())
    assert {type(x) for x in real.list_scale_sets("rg")} == {
        type(x) for x in fake.list_scale_sets("rg")
    }
    assert {type(x) for x in real.list_reservation_groups("rg")} == {
        type(x) for x in fake.list_reservation_groups("rg")
    }


def test_the_probe_selects_from_the_real_list_models():
    adapter = real_adapter()
    from ftg_prober.azure.prechecks import select_by_tags

    match = {"experiment": "ftg", "sku": "Standard_NC40ads_H100_v5", "region": "eastus2"}
    found, failure = select_by_tags(
        lambda: adapter.list_scale_sets("rg"), match, "scale set", "az_vmss_flex", "eastus2"
    )
    assert failure is None and found.name == "a-scale-set"
    found, failure = select_by_tags(
        lambda: adapter.list_reservation_groups("rg"),
        {"experiment": "ftg", "region": "eastus2"},
        "reservation group",
        "az_capacity_reservation",
        "eastus2",
    )
    assert failure is None and found.name == "a-group"
    found, failure = select_by_tags(
        lambda: adapter.list_scale_sets("rg"),
        {**match, "experiment": "ftg-20260101-other"},
        "scale set",
        "az_vmss_flex",
        "eastus2",
    )
    assert found is None and failure.details["reason"] == "no scale set for this experiment entry"


@pytest.mark.parametrize("probe_cls", [CapacityReservationProbe, VmssFlexProbe])
@pytest.mark.parametrize(
    ("err", "code", "raw"),
    [
        (RuntimeError("secret store unreachable"), "RuntimeError", "secret store unreachable"),
        (
            AzureCallError("AuthorizationFailed", "no access to vault"),
            "AuthorizationFailed",
            "no access to vault",
        ),
    ],
    ids=["non-azure", "azure-call-error"],
)
def test_adapter_build_failure_gives_an_error_row_per_entry(probe_cls, err, code, raw, caplog):
    """A client build that raises writes 1 `error` row per entry, with the raw error code or type,
    and the raw message reaches the log unfiltered."""

    def broken(ctx):
        raise err

    probe = probe_cls()
    probe.adapter_factory = broken
    entries = [entry(sku="Standard_B2s"), entry(sku="Standard_B4ms")]
    for e in entries:
        e["probe_type"] = probe.probe_type
    store_ctx = make_ctx(entries)
    with caplog.at_level("DEBUG"):
        rows = probe.run_allocation(store_ctx)
    assert [r.sku for r in rows] == ["Standard_B2s", "Standard_B4ms"]
    assert {r.outcome for r in rows} == {"error"}
    assert {r.error_code for r in rows} == {code}
    assert {r.details["reason"] for r in rows} == {"Adapter build failed"}
    assert raw in caplog.text
