"""Focused parser tests for Illinois IGB CSVs."""

from pathlib import Path

import pandas as pd
import pytest

from variant_gaming.states.illinois import (
    join_handle_and_revenue,
    parse_sport_detail_handle,
    parse_tax_summary,
)

FIXTURES = Path(__file__).parent / "fixtures" / "IL"


@pytest.fixture(scope="module")
def handle_text() -> str:
    path = FIXTURES / "sample_sport_detail.csv"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tax_text() -> str:
    path = FIXTURES / "sample_tax_summary.csv"
    assert path.exists(), f"Missing fixture {path}"
    return path.read_text(encoding="utf-8")


def test_parse_handle_online_and_retail(handle_text: str) -> None:
    channel_df, check = parse_sport_detail_handle(handle_text)
    assert set(channel_df["channel"]) <= {"online", "retail"}
    assert channel_df["handle"].notna().all()
    assert check["difference"].abs().max() < 0.02


def test_parse_tax_preserves_negative_agr(tax_text: str) -> None:
    tax_df = parse_tax_summary(tax_text)
    # Fixture includes a negative State AGR row
    assert (tax_df["adjusted_revenue"] < 0).any()
    assert "tax" in tax_df.columns


def test_join_online_only_ready(handle_text: str, tax_text: str) -> None:
    handle_df, _ = parse_sport_detail_handle(handle_text)
    tax_df = parse_tax_summary(tax_text)
    joined = join_handle_and_revenue(handle_df, tax_df)
    online = joined[joined["channel"] == "online"]
    assert not online.empty
    # Revenue measure is AGR, not silently copied into gross_revenue
    assert "adjusted_revenue" in online.columns
    assert "handle" in online.columns
