"""Copy researched gaps into coverage; never invent revenue observations."""

from functools import partial

import pandas as pd

from variant_gaming.common import project_root
from variant_gaming.coverage import record_coverage


def record_inventory_gap(state_code, vertical="online_sports_betting", root=None, db_path=None):
    """Running this is not a fresh web check; preserve the source-check date."""
    root = root or project_root()
    inventory = pd.read_csv(root / "config" / "state_gaming_source_inventory.csv").fillna("")
    row = inventory[(inventory.state_code == state_code) & (inventory.vertical == vertical)].iloc[0]
    record_coverage(state_code=state_code, vertical=vertical, status=row.collection_status,
                    reason=f"Source checked {row.source_checked_at_utc}: {row.status_note}",
                    official_url=row.official_landing_url, available_frequency=row.reporting_frequency,
                    root=root, db_path=db_path)
    return pd.DataFrame()


collect_arizona_coverage = partial(record_inventory_gap, "AZ")
collect_arkansas_coverage = partial(record_inventory_gap, "AR")
collect_florida_coverage = partial(record_inventory_gap, "FL")
collect_maine_casino_coverage = partial(record_inventory_gap, "ME", "online_casino")
collect_virginia_coverage = partial(record_inventory_gap, "VA")
collect_mississippi_coverage = partial(record_inventory_gap, "MS")
collect_montana_coverage = partial(record_inventory_gap, "MT")
