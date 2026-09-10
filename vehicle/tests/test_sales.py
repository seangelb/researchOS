"""Sale candidates are reviewable episodes, never automatic daily sales."""
import pandas as pd
import pytest

from test_daily_events import cycles, observations
from vehicle_tracker.sales import REVIEW_COLUMNS, sale_candidates


def cutoff(day):
    return f'2026-09-{day:02}T23:00:00Z'


def test_threshold_once_per_episode_and_reappearance_with_new_listing():
    schedule = cycles(range(1, 9))
    source = observations((1, 'V1', 'L1'), (5, 'V1', 'L2'))
    before = source.copy(deep=True)
    candidates, daily = sale_candidates(schedule, source, as_of=cutoff(8))
    assert candidates.detected_date.tolist() == ['2026-09-04', '2026-09-08']
    assert candidates.followup_state.tolist() == ['reappeared', 'still_absent']
    assert candidates.reappeared_date.iloc[0] == '2026-09-05'
    assert candidates.listing_id.tolist() == ['L1', 'L2']
    assert candidates.capture_id.tolist() == ['capture1', 'capture5']
    assert candidates.candidate_id.is_unique
    assert daily.new_candidates.sum() == 2 and daily.candidate_reappearances.sum() == 1
    assert daily.estimated_sales.isna().all() and candidates.review_outcome.eq('unreviewed').all()
    pd.testing.assert_frame_equal(source, before)


def test_cutoff_excludes_future_return_and_future_conflicting_identity():
    schedule = cycles(range(1, 6))
    source = observations((1, 'V1', 'L1'), (5, 'V2', 'L1'))
    early, _ = sale_candidates(schedule, source, as_of=cutoff(3))
    assert early.empty
    detected, daily = sale_candidates(schedule, source, as_of=cutoff(4))
    assert detected.followup_state.tolist() == ['still_absent']
    assert detected.detected_date.tolist() == ['2026-09-04']
    assert daily.cycle_date.max() == '2026-09-04'
    with pytest.raises(ValueError, match='Conflicting'):
        sale_candidates(schedule, source, as_of=cutoff(5))


def test_partial_and_missing_dates_delay_detection_and_do_not_become_zero():
    schedule = cycles((1, 2, 4, 5, 6, 7), partial=(5,))
    candidates, daily = sale_candidates(schedule, observations((1, 'V1', 'L1')),
                                        as_of=cutoff(8), absence_days=2)
    assert candidates.detected_date.tolist() == ['2026-09-07']
    assert candidates.first_absent_date.tolist() == ['2026-09-02']
    assert candidates.timing_uncertain.all()
    assert candidates.followup_state.tolist() == ['coverage_gap']
    assert daily.set_index('cycle_date').loc[['2026-09-03', '2026-09-05', '2026-09-08'], 'new_candidates'].isna().all()


def review_for(candidate_id, **changes):
    row = dict(candidate_id=candidate_id, outcome='confirmed_sale', sale_date='2026-09-03',
        reviewer='Test analyst', source='synthetic://explicit-outcome',
        available_at='2026-09-04T20:00:00Z', note='Invented independent sale-date evidence for test only')
    return pd.DataFrame([dict(row, **changes)], columns=REVIEW_COLUMNS)


def test_reviewed_date_is_distinct_from_detection_and_available_only_after_review():
    schedule, source = cycles(), observations((1, 'V1', 'L1'))
    candidates, _ = sale_candidates(schedule, source, as_of=cutoff(4))
    review = review_for(candidates.candidate_id.iloc[0])
    before, before_daily = sale_candidates(schedule, source, as_of='2026-09-04T19:00:00Z', reviews=review)
    assert before.review_outcome.eq('unreviewed').all()
    assert before_daily.reviewed_sales_with_known_date.sum() == 0
    after, daily = sale_candidates(schedule, source, as_of=cutoff(4), reviews=review)
    assert after.review_outcome.tolist() == ['confirmed_sale']
    daily = daily.set_index('cycle_date')
    assert daily.loc['2026-09-03', 'reviewed_sales_with_known_date'] == 1
    assert daily.loc['2026-09-04', 'new_candidates'] == 1
    assert daily.estimated_sales.isna().all()


def test_return_preserves_review_label_and_flags_followup():
    schedule, source = cycles(range(1, 6)), observations((1, 'V1', 'L1'), (5, 'V1', 'L2'))
    candidates, _ = sale_candidates(schedule, source, as_of=cutoff(4))
    result, _ = sale_candidates(schedule, source, as_of=cutoff(5),
                               reviews=review_for(candidates.candidate_id.iloc[0], sale_date=None))
    assert result.followup_state.tolist() == ['reappeared']
    assert result.review_outcome.tolist() == ['confirmed_sale']
    assert result.review_needs_followup.all() and result.sale_date.isna().all()


@pytest.mark.parametrize('problem', ['missing_source', 'unknown', 'duplicate', 'future_date', 'predates', 'not_sale_date'])
def test_review_requires_explicit_unambiguous_evidence(problem):
    schedule, source = cycles(), observations((1, 'V1', 'L1'))
    candidates, _ = sale_candidates(schedule, source, as_of=cutoff(4))
    review = review_for(candidates.candidate_id.iloc[0])
    if problem == 'missing_source': review.loc[0, 'source'] = ''
    if problem == 'unknown': review.loc[0, 'candidate_id'] = 'unknown'
    if problem == 'duplicate': review = pd.concat([review, review])
    if problem == 'future_date': review.loc[0, 'sale_date'] = '2026-09-05'
    if problem == 'predates': review.loc[0, 'available_at'] = '2026-09-03T20:00:00Z'
    if problem == 'not_sale_date': review.loc[0, 'outcome'] = 'not_sale'
    with pytest.raises(ValueError):
        sale_candidates(schedule, source, as_of=cutoff(4), reviews=review)


def test_no_cycles_and_complete_empty_cycle_are_distinct():
    candidates, daily = sale_candidates(cycles(()), observations(), as_of=cutoff(4))
    assert candidates.empty and daily.empty
    candidates, daily = sale_candidates(cycles(), observations(), as_of=cutoff(4))
    assert candidates.empty and daily.new_candidates.eq(0).all()
    assert daily.estimated_sales.isna().all()


def test_pending_alone_is_not_a_candidate_and_missing_values_stay_unknown():
    source = observations((1, 'V1', 'L1', {'purchase_pending': True}),
                          (2, 'V1', 'L1', {'purchase_pending': False}))
    candidates, _ = sale_candidates(cycles((1, 2)), source, as_of=cutoff(2))
    assert candidates.empty
    source = observations((1, 'V1', 'L1', {'purchase_pending': None, 'asking_price_usd': 0}))
    candidates, _ = sale_candidates(cycles(), source, as_of=cutoff(4))
    assert candidates.last_purchase_pending.isna().all()
    assert candidates.last_asking_price_usd.tolist() == [0]


def test_later_review_revisions_are_explicit_and_old_cutoff_is_unchanged():
    schedule, source = cycles(), observations((1, 'V1', 'L1'))
    candidates, _ = sale_candidates(schedule, source, as_of=cutoff(4))
    original = review_for(candidates.candidate_id.iloc[0])
    revised = review_for(candidates.candidate_id.iloc[0], outcome='not_sale', sale_date=None,
                         available_at='2026-09-05T20:00:00Z', note='Synthetic corrected outcome')
    old, _ = sale_candidates(schedule, source, as_of=cutoff(4), reviews=pd.concat([original, revised]))
    assert old.review_outcome.tolist() == ['confirmed_sale']
    with pytest.raises(ValueError, match='one review version'):
        sale_candidates(schedule, source, as_of=cutoff(5), reviews=pd.concat([original, revised]))
    current, daily = sale_candidates(schedule, source, as_of=cutoff(5), reviews=revised)
    assert current.review_outcome.tolist() == ['not_sale']
    assert daily.reviewed_sales_with_known_date.sum() == 0
