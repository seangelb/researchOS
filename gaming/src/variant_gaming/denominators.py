"""Printed-control eligibility for the explicitly reviewed FLUT source families.

This recognizes parser contracts, not analyst approval. Callers must still verify
retained bytes, reject conflicting versions, and reconcile the complete operator
population for the same period and metric. A matching sum alone is not provenance.
"""

from collections.abc import Mapping


# Each status is emitted only for the native printed control described below.
# Do not add a generic ``ok`` or globally accepted ``printed`` status here.
_PRINTED_CONTROLS = {
    # massachusetts.parse_revenue_pdf: printed online subtotal reconciled to operators.
    ("MA", "online_sports_betting", "monthly"): {"reconciled_printed_total"},
    # michigan.parse_michigan_wide_sheet: the "All Internet ... Operators" block.
    ("MI", "online_sports_betting", "monthly"): {"ok"},
    ("MI", "online_casino", "monthly"): {"ok"},
    # kansas.parse_report: the printed "Subtotal Online" row (unaudited).
    ("KS", "online_sports_betting", "monthly"): {"unaudited"},
    # wyoming.parse_report/parse_older_report: the printed online "Total" row.
    ("WY", "online_sports_betting", "monthly"): {"reported"},
    # ohio.parse_month_sheet: the printed "Subtotal Type A Online" row.
    ("OH", "online_sports_betting", "monthly"): {"ok"},
    # new_york.collect_history: the separately published statewide weekly workbook.
    ("NY", "online_sports_betting", "weekly"): {"ok"},
}


def denominator_provenance(row: Mapping) -> str | None:
    """Name an eligible source/state control; return None for every unknown case.

    Derived totals and visually transcribed rows require separate evidence review
    and are deliberately excluded. Missing keys, wrong products/channels, and a
    status borrowed from another source never establish statewide coverage.
    """
    values = {key: row.get(key) for key in (
        "state_code", "vertical", "frequency", "channel", "operator", "row_type", "report_status"
    )}
    # Missing pandas scalar values must fail closed without truth-value errors.
    if not all(isinstance(value, str) for value in values.values()):
        return None
    if (values["channel"] != "online" or values["operator"] != "STATEWIDE"
            or values["row_type"] != "official_statewide_total"):
        return None
    key = (values["state_code"], values["vertical"], values["frequency"])
    if values["report_status"] not in _PRINTED_CONTROLS.get(key, ()):
        return None
    return f"printed:{values['state_code']}:{values['report_status']}"
