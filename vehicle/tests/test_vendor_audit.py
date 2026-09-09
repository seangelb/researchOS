import openpyxl
from vehicle_tracker.vendor_audit import audit_vendor_workbook


def test_formula_cache_is_not_missing_source_and_workbook_is_unchanged(tmp_path):
    from datetime import datetime
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(['Date', 'Count', 'Comparison', 'Formula'])
    sheet.append([datetime(2026, 7, 1), 0, 'nan%', '=B2+1'])
    sheet.append([datetime(2026, 7, 2), 12, 'Null', None])
    path = tmp_path / 'synthetic.xlsx'
    book.save(path)
    before = path.read_bytes()
    cells, coverage = audit_vendor_workbook(path)
    formula = cells.set_index('cell').loc['D2']
    assert formula['formula'] and formula.cache_absent and not formula.missing_token
    assert formula.source_value == '=B2+1'
    assert cells.missing_token.sum() == 2
    assert coverage.set_index('column').loc['B', 'numeric_dates'] == 2  # includes zero
    assert path.read_bytes() == before
