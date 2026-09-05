"""Retained monthly figures from KHRG's public meeting packet.

The packet contains images. The checked transcription is explicit; other
report versions require their own inspection. The live Tableau export failed.
"""

from variant_gaming.common import collect_reports, read_transcribed_report

LANDING_URL = "https://khrc.ky.gov/newstatic_info.aspx/utils/newstatic_Info.aspx?menuid=80&static_ID=722"
REPORT_URL = "https://dcg.ky.gov/Documents/2025-06-24%20Meeting%20Materials%20PUBLIC.pdf"


def parse_report(path):
    rows = read_transcribed_report(path)
    if rows is None:
        raise ValueError("Kentucky image packet needs a checked transcription; source bytes changed")
    return rows


def collect_history(root=None, db_path=None):
    return collect_reports(state_code="KY", jurisdiction="Kentucky", vertical="online_sports_betting",
                           landing_url=LANDING_URL, urls=[REPORT_URL], parse_report=parse_report,
                           root=root, db_path=db_path)
