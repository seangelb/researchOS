"""Shared native first-page context checks for retained public search responses.

A validated context means the response reported the exact requested ZIP, year
bounds and make/model application. Two retained September 19 layouts leave that
context unknown on an otherwise valid empty first page: omitted `facetData.makes`,
and a zero-count model omitted from its applied make's native children. Those
stay explicitly unverified. An unverified context is not an observed absence and
never admits vehicle rows; a populated response can never be unverified.
"""
import json

from vehicle_tracker.collect import CollectionStopped
from vehicle_tracker.search import _empty_first_page, build_search_request

UNVERIFIED_EMPTY_STATUSES = ('empty_make_context_unavailable', 'empty_model_context_unavailable',
                             'empty_filter_context_unavailable')


def _same(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def validate_request_context(capture, facets, query):
    """Check the parts a response can never legitimately contradict.

    The request, returned ZIP, observation clock, pagination agreement and every
    applied year boundary must match exactly, whether or not any vehicle matched.
    """
    request = build_search_request(filters=query['filters'], zip_code=query['zip_code'])
    if (not _same(capture['request'], request) or not _same(facets['request'], request)
            or capture['zip_code'] != query['zip_code'] or facets['zip_code'] != query['zip_code']
            or facets['captured_at_utc'] != capture['captured_at_utc']
            or not _same(facets['pagination'], capture['pagination'])):
        raise CollectionStopped('First-page source context differs from the requested query')
    bounds = query['filters'].get('year', {})
    year = facets['facet_data']['year']
    for key, native in [('min', 'appliedMin'), ('max', 'appliedMax')]:
        value = year.get(native)
        if ((key in bounds and (type(value) is not int or value != bounds[key]))
                or (key not in bounds and value is not None)):
            raise CollectionStopped('Native applied year differs, including the open tail')


def validate_first_page(capture, facets, query):
    """Return True when validated, or one known unverified empty-context status.

    Raise for any contradiction: a differing request, returned ZIP, applied year
    boundary, or applied make/model. The caller decides whether an unverified
    status may continue; this function never reports coverage or absence.
    """
    validate_request_context(capture, facets, query)
    empty = _empty_first_page(capture['pagination'], capture['vehicles'])
    if facets['facet_data'].get('makes_present') is False:
        if facets['facet_data']['makes'] != {} or not empty:
            raise CollectionStopped('Unavailable make context requires the exact empty first page')
        return 'empty_make_context_unavailable'
    makes = facets['facet_data']['makes']
    requested = query['filters'].get('makes', [])
    applied = [name for name, bucket in makes.items() if bucket['isApplied'] is True]
    applied_models = [child['key'] for bucket in makes.values()
                      for child in bucket['parentModels'] if child['isApplied'] is True]
    if not requested:
        if applied or applied_models:
            raise CollectionStopped('Unexpected applied make/model for an unfiltered request')
        return True
    if len(requested) != 1 or len(requested[0].get('parentModels', [])) > 1:
        raise CollectionStopped('Require one requested make or make/model parent')
    make = requested[0]['name']
    if applied != [make]:
        raise CollectionStopped('Native applied make differs from the request')
    requested_models = [model['name'] for model in requested[0].get('parentModels', [])]
    if not requested_models:
        if applied_models:
            raise CollectionStopped('Unexpected applied model for a make-only request')
        return True
    present = {child['key'] for child in makes[make]['parentModels']}
    if not present & set(requested_models):
        # Retained evidence: Carvana omits a zero-count model from its applied
        # make's native children, so this model context cannot be confirmed.
        if not empty:
            raise CollectionStopped('Requested model is missing from a populated response')
        return 'empty_model_context_unavailable'
    if applied_models != requested_models:
        raise CollectionStopped('Native applied model differs from the request')
    return True


def empty_context_status(capture, facets, query):
    """Validate an empty first page only; a populated page defers to row checks.

    Row parsing already rejects any vehicle outside the requested make, model
    and year, which is stronger evidence than a facet flag. An empty page has no
    rows to check, so an unconfirmed filter context stays explicitly visible
    rather than stopping every other query in the invocation.
    """
    if not _empty_first_page(capture['pagination'], capture['vehicles']):
        return True
    try:
        return validate_first_page(capture, facets, query)
    except CollectionStopped:
        # A contradicted request, ZIP or applied year remains fatal; only the
        # unconfirmed make/model application of an empty page is tolerated.
        validate_request_context(capture, facets, query)
        return 'empty_filter_context_unavailable'
