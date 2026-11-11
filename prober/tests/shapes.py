"""The value types of a row, to compare a sim row with a real row."""

from dataclasses import asdict

from ftg_prober.record import Record


def type_name(value) -> str:
    """The JSON type of a value: null, bool, number, string, list, or object."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "list" if isinstance(value, list) else "object"


def value_types(row: Record) -> dict[str, str]:
    """The type of each row field and of each `details` key, except `details.steps`. A field
    that differs by run (the time, the run id) is not in the map."""
    skip = {"ts_utc", "run_id", "simulated", "details"}
    types = {k: type_name(v) for k, v in asdict(row).items() if k not in skip}
    types.update({f"details.{k}": type_name(v) for k, v in row.details.items() if k != "steps"})
    return types


def step_types(row: Record) -> dict[str, dict[str, str]]:
    """The key types of the `details.steps` entries, by the `result` of the entry."""
    return {
        step["result"]: {k: type_name(v) for k, v in step.items()}
        for step in row.details.get("steps", [])
    }


def variant(row: Record) -> tuple:
    """The kind of a row, for a comparison: the outcome, and the cases in which a value is null or
    a key is added (the hourly effective price row, an unknown eviction bucket, an open range, a
    row with no zone, the scope of an Azure score, the rule of a Spot score control)."""
    details = row.details
    return (
        row.outcome,
        details.get("effective") is True,
        "eviction_rate" in details and details["eviction_rate"] is None,
        "eviction_rate_max_pct" in details and details["eviction_rate_max_pct"] is None,
        "interruption_range_max_pct" in details and details["interruption_range_max_pct"] is None,
        "price" in details and details["price"] is None,
        row.zone is None,
        details.get("scope"),
        (details.get("control") or {}).get("rule"),
    )
