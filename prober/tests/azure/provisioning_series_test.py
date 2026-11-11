"""The series rule: probes of 1 (region, SKU) never overlap. Spec 0010 section 5."""

import threading
import time

from ftg_prober.azure.prechecks import series_lock


def test_series_lock_blocks_the_same_region_and_sku_across_threads():
    active: dict[tuple[str, str], int] = {}
    peak: dict[tuple[str, str], int] = {}
    guard = threading.Lock()

    def work(region: str, sku: str) -> None:
        key = (region, sku)
        with series_lock(region, sku):
            with guard:
                active[key] = active.get(key, 0) + 1
                peak[key] = max(peak.get(key, 0), active[key])
            time.sleep(0.02)
            with guard:
                active[key] -= 1

    threads = [
        threading.Thread(target=work, args=(region, sku))
        for region, sku in [("eastus", "Standard_B2s")] * 4 + [("eastus2", "Standard_B2s")] * 2
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[("eastus", "Standard_B2s")] == 1
    assert peak[("eastus2", "Standard_B2s")] == 1


def test_lock_key_ignores_case():
    with series_lock("EastUS", "standard_b2s"):
        acquired = threading.Event()

        def other() -> None:
            with series_lock("eastus", "Standard_B2s"):
                acquired.set()

        thread = threading.Thread(target=other)
        thread.start()
        assert not acquired.wait(0.05)
    thread.join()
    assert acquired.is_set()
