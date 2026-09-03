"""Collector mapping follows inventory wave order."""

from variant_gaming.collect import COLLECTORS, inventory_collector_order


def test_inventory_order_starts_with_wave_one() -> None:
    order = inventory_collector_order()
    waves = [wave for wave, _, _ in order]
    assert waves == sorted(waves)
    first_wave = [item for item in order if item[0] == 1]
    assert ("IL", "online_sports_betting") in [(s, v) for _, s, v in first_wave]


def test_every_collector_is_scheduled() -> None:
    scheduled = {(s, v) for _, s, v in inventory_collector_order()}
    assert scheduled == set(COLLECTORS)
