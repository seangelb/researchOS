"""Invented forecasts and results only; never actual Carvana estimates."""
import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from vehicle_tracker.expectations import RETAIL_UNIT_DEFINITION, freeze_forecast, forecast_review_rows


@pytest.fixture(autouse=True)
def fixed_save_clock(monkeypatch):
    """Synthetic forecast timestamps are repeatable; production saves use the clock."""
    def set_clock(value):
        class FixedClock:
            @staticmethod
            def now(tz):
                return datetime.fromisoformat(value).astimezone(tz)
        monkeypatch.setattr('vehicle_tracker.expectations.datetime', FixedClock)
    set_clock('2026-09-12T12:00:00+00:00')
    return set_clock


def example():
    return dict(forecast_id='synthetic-v1', model_version='synthetic-model-v1', model_description='Invented test arithmetic',
        quarter='2099Q3', trained_through_quarter='2025Q4', as_of='2026-01-01T05:00:00Z',
        forecast_units=103, definition=RETAIL_UNIT_DEFINITION,
        source_coverage=dict(scope_id='synthetic', description='Invented rows only',
            expected_dates=3, complete_dates=1, partial_dates=1, missing_dates=1))


def save(tmp_path, record=None):
    source = tmp_path/'synthetic_source.txt'
    source.write_text('SYNTHETIC ONLY')
    path = tmp_path/'forecast.json'
    saved = freeze_forecast(record or example(), destination=path, source_paths=[source])
    return path, saved, source


def result(published='2099-10-01T12:00:00Z'):
    return dict(quarter='2099Q3', definition=RETAIL_UNIT_DEFINITION, reported_units=100,
        available_at=published, source='synthetic://reported-result',
        first_published_at=published, first_publication_source='synthetic://first-publication')


def test_prepublished_vintage_signed_and_absolute_errors(tmp_path):
    path, saved, _ = save(tmp_path)
    rows = forecast_review_rows([path], [result()], as_of='2099-10-02T00:00:00Z')
    assert rows.status.item() == 'eligible'
    assert rows.prospective_eligible.item()
    assert rows.reported_first_published_at.item() == rows.reported_available_at.item()
    signed = 100*(rows.forecast_units-rows.reported_units)/rows.reported_units
    assert signed.item() == 3 and signed.abs().item() == 3
    assert pd.Timestamp(saved['saved_at']) >= pd.Timestamp(example()['as_of'])


@pytest.mark.parametrize('training, target, boundary', [
    ('2025Q4', '2026Q1', '2026-01-01T05:00:00Z'),
    ('2026Q2', '2026Q3', '2026-07-01T04:00:00Z'),
])
def test_training_quarter_must_end_on_the_new_york_calendar(tmp_path, training, target, boundary):
    record = example() | dict(trained_through_quarter=training, quarter=target)
    first_after_quarter = pd.Timestamp(boundary)
    for early_cutoff in [first_after_quarter.normalize(), first_after_quarter-pd.Timedelta(nanoseconds=1)]:
        with pytest.raises(ValueError, match='Training quarter had not ended'):
            save(tmp_path, record | dict(as_of=early_cutoff.isoformat()))
        assert not (tmp_path/'forecast.json').exists()
        assert not (tmp_path/'forecast.json.sources').exists()
    path, saved, _ = save(tmp_path, record | dict(as_of=first_after_quarter.isoformat()))
    rows = forecast_review_rows([path], [], as_of=saved['saved_at'])
    assert rows.as_of.item() == first_after_quarter.isoformat()


def test_future_result_and_future_forecast_not_leaked(tmp_path):
    path, saved, _ = save(tmp_path)
    assert forecast_review_rows([path], [result()], as_of='2026-01-01T00:00:00Z').empty
    rows = forecast_review_rows([path], [result()], as_of=saved['saved_at'])
    assert rows.status.item() == 'awaiting reported result' and rows.reported_units.isna().all()


def test_saved_after_report_is_not_prospective(tmp_path):
    path, saved, _ = save(tmp_path, example() | dict(quarter='2026Q2'))
    late_result = result('2026-07-02T00:00:00Z') | dict(quarter='2026Q2')
    rows = forecast_review_rows([path], [late_result], as_of=saved['saved_at'])
    assert rows.status.item() == 'saved after result; exclude from accuracy'


def test_later_revision_does_not_make_post_original_forecast_prospective(tmp_path, fixed_save_clock):
    fixed_save_clock('2099-10-02T12:00:00+00:00')
    path, _, _ = save(tmp_path)
    original = result()
    revision = original | dict(available_at='2099-10-03T12:00:00Z', reported_units=102,
                               source='synthetic://revision')
    # Even a revision supplied alone must use the separately sourced first clock.
    for supplied in ([revision], [revision, original]):
        rows = forecast_review_rows([path], supplied, as_of='2099-10-04T00:00:00Z')
        assert rows.status.item() == 'saved after result; exclude from accuracy'
        assert not rows.prospective_eligible.item()
        assert rows.reported_units.item() == 102
        assert rows.reported_source.item() == 'synthetic://revision'
        assert rows.reported_first_publication_source.item() == 'synthetic://first-publication'
        assert pd.Timestamp(rows.reported_first_published_at.item()) < pd.Timestamp(rows.saved_at.item())
        assert pd.Timestamp(rows.saved_at.item()) < pd.Timestamp(rows.reported_available_at.item())


def test_original_and_revised_denominators_follow_cutoff_without_changing_frozen_sources(tmp_path):
    path, saved, _ = save(tmp_path)
    original = result()
    revision = original | dict(available_at='2099-10-03T12:00:00Z', reported_units=102,
                               source='synthetic://revision')
    before = {p: Path(p).read_bytes() for p in [path, *saved['source_sha256']]}
    original_rows = forecast_review_rows([path], [revision, original], as_of='2099-10-03T11:59:59Z')
    revised_rows = forecast_review_rows([path], [original, revision], as_of=revision['available_at'])
    assert original_rows.reported_units.item() == 100
    assert original_rows.known_result_revisions.item() == 1
    assert revised_rows.reported_units.item() == 102
    assert revised_rows.known_result_revisions.item() == 2
    assert original_rows.prospective_eligible.item() and revised_rows.prospective_eligible.item()
    assert original_rows.reported_first_published_at.item() == revised_rows.reported_first_published_at.item()
    assert original_rows.reported_source.item() != revised_rows.reported_source.item()
    assert all(Path(p).read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize('first_clock', ['missing', None])
def test_unknown_first_publication_is_unresolved_even_with_known_result(tmp_path, first_clock):
    path, _, _ = save(tmp_path)
    unknown = result()
    unknown.pop('first_published_at')
    unknown.pop('first_publication_source')
    if first_clock is None:
        unknown['first_published_at'] = None
    rows = forecast_review_rows([path], [unknown], as_of='2099-10-02T00:00:00Z')
    assert rows.status.item() == 'first publication unknown; prospective eligibility unresolved'
    assert rows.reported_units.item() == 100
    assert rows.prospective_eligible.isna().all()
    assert rows.reported_first_published_at.isna().all()


def test_future_revision_cannot_resolve_unknown_first_clock(tmp_path):
    path, _, _ = save(tmp_path)
    original = result() | dict(first_published_at=None, first_publication_source=None)
    later = result() | dict(available_at='2099-10-03T12:00:00Z', source='synthetic://revision')
    earlier = forecast_review_rows([path], [original, later], as_of='2099-10-02T00:00:00Z')
    assert earlier.prospective_eligible.isna().all()
    assert earlier.reported_first_publication_source.isna().all()
    known = forecast_review_rows([path], [original, later], as_of=later['available_at'])
    assert known.prospective_eligible.item()


def test_known_first_clock_survives_revision_with_unknown_first_metadata(tmp_path):
    path, _, _ = save(tmp_path)
    original = result()
    later = original | dict(available_at='2099-10-03T12:00:00Z', first_published_at=None,
                            first_publication_source=None, reported_units=102)
    rows = forecast_review_rows([path], [original, later], as_of=later['available_at'])
    assert rows.prospective_eligible.item()
    assert rows.reported_units.item() == 102
    assert rows.reported_first_publication_source.item() == original['first_publication_source']


def test_forecast_saved_at_first_release_is_not_strictly_prospective(tmp_path, fixed_save_clock):
    fixed_save_clock(result()['available_at'])
    path, _, _ = save(tmp_path)
    rows = forecast_review_rows([path], [result()], as_of=result()['available_at'])
    assert not rows.prospective_eligible.item()


def test_all_forecast_vintages_survive_revision_review(tmp_path, fixed_save_clock):
    early_dir, late_dir = tmp_path/'early', tmp_path/'late'
    early_dir.mkdir()
    late_dir.mkdir()
    early, _, _ = save(early_dir)
    fixed_save_clock('2099-10-02T12:00:00+00:00')
    late, _, _ = save(late_dir, example() | dict(forecast_id='synthetic-v2', forecast_units=102))
    revision = result() | dict(available_at='2099-10-03T12:00:00Z', reported_units=102)
    rows = forecast_review_rows([early, late], [result(), revision], as_of=revision['available_at'])
    assert list(rows.forecast_id) == ['synthetic-v1', 'synthetic-v2']
    assert list(rows.prospective_eligible) == [True, False]
    assert list(rows.forecast_units) == [103, 102]
    assert rows.reported_units.eq(102).all()


def test_future_revision_alone_leaves_report_and_first_clock_unavailable(tmp_path):
    path, _, _ = save(tmp_path)
    later = result() | dict(available_at='2099-10-03T12:00:00Z')
    rows = forecast_review_rows([path], [later], as_of='2099-10-02T12:00:00Z')
    assert rows.status.item() == 'awaiting reported result'
    assert rows.reported_units.isna().all() and rows.reported_first_published_at.isna().all()


@pytest.mark.parametrize('change, message', [
    (dict(first_published_at='2099-09-30T12:00:00Z'), 'end of its quarter'),
    (dict(first_published_at='2099-10-02T12:00:00Z'), 'revision availability'),
    (dict(first_published_at='2099-10-01T12:00:00'), 'timezone aware'),
    (dict(first_publication_source=None), 'first_publication_source'),
])
def test_first_publication_requires_valid_sourced_chronology(tmp_path, change, message):
    path, _, _ = save(tmp_path)
    with pytest.raises(ValueError, match=message):
        forecast_review_rows([path], [result() | change], as_of='2099-10-04T00:00:00Z')


def test_conflicting_first_clocks_rejected_only_when_both_revisions_are_known(tmp_path):
    path, _, _ = save(tmp_path)
    conflicting = result() | dict(available_at='2099-10-03T12:00:00Z',
                                  first_published_at='2099-10-01T13:00:00Z')
    assert forecast_review_rows([path], [result(), conflicting], as_of='2099-10-02T00:00:00Z').prospective_eligible.item()
    with pytest.raises(ValueError, match='Conflicting first-publication clocks'):
        forecast_review_rows([path], [result(), conflicting], as_of=conflicting['available_at'])


def test_claimed_first_release_cannot_follow_an_already_known_result(tmp_path):
    path, _, _ = save(tmp_path)
    original = result() | dict(first_published_at=None, first_publication_source=None)
    later = result() | dict(available_at='2099-10-03T12:00:00Z', first_published_at='2099-10-02T12:00:00Z')
    with pytest.raises(ValueError, match='earlier known reported result'):
        forecast_review_rows([path], [original, later], as_of=later['available_at'])


@pytest.mark.parametrize('change', [dict(trained_through_quarter='2099Q3'), dict(forecast_units=-1),
    dict(forecast_units=True), dict(forecast_units=float('nan')), dict(as_of='2200-01-01T00:00:00Z'),
    dict(definition='native_sold'), dict(model_version=''), dict(source_coverage={})])
def test_bad_forecast_not_saved(tmp_path, change):
    with pytest.raises(ValueError):
        save(tmp_path, example() | change)
    assert not (tmp_path/'forecast.json').exists()


def test_no_overwrite_and_retained_copy_survives_model_edits(tmp_path):
    path, saved, source = save(tmp_path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        freeze_forecast(example(), destination=path, source_paths=[source])
    assert path.read_bytes() == original
    source.write_text('Changed synthetic input')
    assert len(forecast_review_rows([path], [], as_of=saved['saved_at'])) == 1
    Path(next(iter(saved['source_sha256']))).write_text('Changed retained snapshot')
    with pytest.raises(ValueError, match='source evidence'):
        forecast_review_rows([path], [], as_of=saved['saved_at'])


def test_changed_saved_forecast_rejected(tmp_path):
    path, saved, _ = save(tmp_path)
    saved['forecast_units'] = 100
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='forecast changed'):
        forecast_review_rows([path], [], as_of=saved['saved_at'])


def test_duplicate_ids_or_result_versions_rejected(tmp_path):
    path, saved, _ = save(tmp_path)
    with pytest.raises(ValueError, match='Duplicate forecast'):
        forecast_review_rows([path, path], [], as_of=saved['saved_at'])
    with pytest.raises(ValueError, match='one explicit reported-result'):
        forecast_review_rows([path], [result(), result()], as_of='2099-10-02T00:00:00Z')


@pytest.mark.parametrize('units', [0, -1, float('nan'), float('inf'), True])
def test_bad_report_denominator_rejected(tmp_path, units):
    path, _, _ = save(tmp_path)
    with pytest.raises(ValueError, match='Reported units'):
        forecast_review_rows([path], [result() | dict(reported_units=units)], as_of='2099-10-02T00:00:00Z')


def test_definition_mismatch_does_not_score(tmp_path):
    path, _, _ = save(tmp_path)
    rows = forecast_review_rows([path], [result() | dict(definition='website_sold')], as_of='2099-10-02T00:00:00Z')
    assert rows.status.item() == 'awaiting reported result'


def test_notebook_displays_visible_signed_error_and_excludes_late_vintage(tmp_path):
    from test_daily_notebook import checker
    path, saved, _ = save(tmp_path, example() | dict(quarter='2026Q2'))
    book = json.loads((Path(__file__).resolve().parents[1]/'notebooks/30_carvana_sales_expectations.ipynb').read_text(encoding='utf-8'))
    cell = next(c for c in book['cells'] if c['id'] == 'quarter-accuracy')
    scope = dict(pd=pd, display=lambda *args: None, AS_OF='2099-10-02T00:00:00Z',
        FORECAST_PATHS_OVERRIDE=[path], REPORTED_RESULTS_OVERRIDE=[result() | dict(quarter='2026Q2')])
    with checker.offline_guards():
        exec(''.join(cell['source']), scope)
    assert scope['accuracy_rows'].signed_percentage_error.item() == 3
    assert scope['accuracy_rows'].absolute_percentage_error.item() == 3
    scope['AS_OF'] = saved['saved_at']
    scope['REPORTED_RESULTS_OVERRIDE'] = [result('2026-07-02T00:00:00Z') | dict(quarter='2026Q2')]
    with checker.offline_guards():
        exec(''.join(cell['source']), scope)
    assert scope['accuracy_rows'].empty


@pytest.mark.parametrize('case', ['post_original_revision', 'unknown_first', 'eligible_revision'])
def test_notebook_scores_only_prospective_vintages_and_displays_known_revisions(tmp_path, fixed_save_clock, case):
    from test_daily_notebook import checker
    if case == 'post_original_revision':
        fixed_save_clock('2099-10-02T12:00:00+00:00')
    path, _, _ = save(tmp_path)
    original = result()
    revision = original | dict(available_at='2099-10-03T12:00:00Z', reported_units=102,
                               source='synthetic://revision')
    supplied = [original, revision]
    if case == 'unknown_first':
        supplied = [dict(row, first_published_at=None, first_publication_source=None) for row in supplied]
    future = revision | dict(available_at='2099-10-05T12:00:00Z', reported_units=103,
                             source='synthetic://future-perfect-match')
    book = json.loads((Path(__file__).resolve().parents[1]/'notebooks/30_carvana_sales_expectations.ipynb').read_text(encoding='utf-8'))
    cell = next(c for c in book['cells'] if c['id'] == 'quarter-accuracy')
    scope = dict(pd=pd, display=lambda *args: None, AS_OF='2099-10-04T00:00:00Z',
        FORECAST_PATHS_OVERRIDE=[path], REPORTED_RESULTS_OVERRIDE=[*supplied, future])
    with checker.offline_guards():
        exec(''.join(cell['source']), scope)
    assert len(scope['known_reported_results']) == 2
    assert not scope['known_reported_results'].source.eq(future['source']).any()
    assert scope['forecast_vintages'].reported_units.item() == 102
    if case == 'eligible_revision':
        assert scope['accuracy_rows'].signed_error_units.item() == 1
        assert scope['accuracy_rows'].absolute_percentage_error.item() == pytest.approx(100/102)
    else:
        assert scope['accuracy_rows'].empty


def test_notebook_accepts_mixed_iso_precision_for_result_and_forecast_clocks(tmp_path, fixed_save_clock):
    from test_daily_notebook import checker
    paths = []
    for forecast_id, quarter, clock in [
        ('synthetic-whole-seconds', '2099Q3', '2099-09-01T12:00:00+00:00'),
        ('synthetic-fractional-seconds', '2099Q3', '2099-09-01T12:00:00.123456+00:00'),
        ('synthetic-earlier-quarter', '2099Q2', '2099-06-01T12:00:00+00:00'),
    ]:
        folder = tmp_path/forecast_id
        folder.mkdir()
        fixed_save_clock(clock)
        path, _, _ = save(folder, example() | dict(forecast_id=forecast_id, quarter=quarter))
        paths.append(path)
    original = result()
    revision = original | dict(available_at='2099-10-03T12:00:00.123456Z', reported_units=102,
                               source='synthetic://fractional-revision')
    earlier_quarter = result('2099-07-01T12:00:00.456Z') | dict(quarter='2099Q2')
    future = revision | dict(available_at='2099-10-05T12:00:00Z', reported_units=103)
    book = json.loads((Path(__file__).resolve().parents[1]/'notebooks/30_carvana_sales_expectations.ipynb').read_text(encoding='utf-8'))
    cell = next(c for c in book['cells'] if c['id'] == 'quarter-accuracy')
    scope = dict(pd=pd, display=lambda *args: None, AS_OF='2099-10-04T00:00:00Z',
        FORECAST_PATHS_OVERRIDE=paths, REPORTED_RESULTS_OVERRIDE=[original, revision, earlier_quarter, future])
    with checker.offline_guards():
        exec(''.join(cell['source']), scope)
    assert len(scope['known_reported_results']) == 3
    scored = scope['accuracy_rows']
    assert len(scored) == 3 and scored.prospective_eligible.all()
    assert scored.loc[scored.quarter.eq('2099Q3'), 'reported_units'].eq(102).all()
    assert scored.loc[scored.quarter.eq('2099Q3'), 'signed_error_units'].eq(1).all()
    assert scored.loc[scored.quarter.eq('2099Q2'), 'absolute_percentage_error'].item() == 3


def test_notebook_readonly_export_contract_preserves_selected_results_and_frozen_inputs(tmp_path, monkeypatch):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot  # Initialize display caches before the notebook's write guard.
    from test_daily_notebook import checker
    vehicle = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(vehicle)
    path, saved, _ = save(tmp_path)
    scope = dict(AS_OF_OVERRIDE='2099-10-02T00:00:00Z', QUARTER_OVERRIDE='2099Q3',
        FORECAST_PATHS_OVERRIDE=[path], REPORTED_RESULTS_OVERRIDE=[result()],
        DATABASE_OVERRIDE=tmp_path/'absent.sqlite', CYCLE_REPORTS_OVERRIDE=[])
    book = json.loads((vehicle/'notebooks/30_carvana_sales_expectations.ipynb').read_text(encoding='utf-8'))
    with checker.offline_guards():
        for cell in book['cells']:
            if cell['cell_type'] == 'code':
                exec(compile(''.join(cell['source']), cell['id'], 'exec'), scope)
    tables = scope['research_tables']
    assert tables['known_reported_results'].first_publication_source.item() == result()['first_publication_source']
    assert tables['accuracy_rows'].absolute_percentage_error.item() == 3
    assert path in scope['input_paths']
    assert set(map(Path, saved['source_sha256'])) <= set(scope['input_paths'])
    assert not (tmp_path/'absent.sqlite').exists()
    assert scope['EXPORT_DIRECTORY'] is None


def test_result_before_quarter_end_cannot_establish_accuracy(tmp_path):
    path, _, _ = save(tmp_path)
    with pytest.raises(ValueError, match='end of its quarter'):
        forecast_review_rows([path], [result('2099-09-20T00:00:00Z')], as_of='2099-10-02T00:00:00Z')


def test_cli_preview_is_readonly_then_explicit_save_refuses_replacement(tmp_path):
    source = tmp_path/'SYNTHETIC_analyst_input.json'
    source.write_text(json.dumps(example()))
    destination = tmp_path/'SYNTHETIC_forecast.json'
    script = Path(__file__).resolve().parents[1]/'scripts/freeze_quarter_forecast.py'
    command = [sys.executable, '-B', str(script), '--input', str(source), '--sources', str(source),
               '--destination', str(destination)]
    preview = subprocess.run(command, capture_output=True, text=True)
    assert preview.returncode == 0 and not destination.exists()
    saved = subprocess.run([*command, '--save'], capture_output=True, text=True)
    assert saved.returncode == 0 and destination.is_file()
    before = destination.read_bytes()
    repeated = subprocess.run([*command, '--save'], capture_output=True, text=True)
    assert repeated.returncode != 0 and destination.read_bytes() == before
