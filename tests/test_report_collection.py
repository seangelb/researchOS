"""Download failures are recorded while valid reports keep their original bytes."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from variant_gaming import common
from variant_gaming.states import vermont
from variant_gaming.storage import connect_readonly


def test_failed_pdf_does_not_hide_later_valid_report(monkeypatch, tmp_path):
    source = Path(__file__).parent / "fixtures/VT/january_2024.pdf"
    content = source.read_bytes()
    monkeypatch.setattr(common, "http_get", lambda url, **kwargs: SimpleNamespace(
        content=b"<html>unavailable</html>" if url.endswith("bad.pdf") else content))
    database = tmp_path / "staging/check.sqlite"
    result = common.collect_reports(state_code="VT", jurisdiction="Vermont",
        vertical="online_sports_betting", landing_url="https://example.gov/reports",
        urls=["https://example.gov/bad.pdf", "https://example.gov/good.pdf"],
        parse_report=vermont.parse_report, root=tmp_path, db_path=database)
    assert len(result) == 1
    assert result.iloc[0].adjusted_revenue == 3554437
    assert (tmp_path / result.iloc[0].source_file).read_bytes() == content
    connection = connect_readonly(database)
    assert connection.execute("SELECT status FROM source_coverage").fetchone()[0] == "partial"
    assert connection.execute("SELECT COUNT(*) FROM gaming_results").fetchone()[0] == 1
    connection.close()
    log = pd.read_csv(database.parent / "VT_online_sports_betting_collection.csv").fillna("")
    assert log.rows.tolist() == [0, 1]
    assert log.iloc[0].error
    assert not (tmp_path / "data/gaming.sqlite").exists()
