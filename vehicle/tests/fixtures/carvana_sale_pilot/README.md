# Real browser projections, not raw HTML

`chrome_projections_20260909.json` contains seven actual selected public DOM/RSC
projections captured in Chrome between 11:42:40 and 11:46:18 UTC on September 9,
2026, and transferred from browser tool output to this file. Native values,
source paths, relevant hero text, and actual capture timestamps are preserved.
Only public vehicle fields were selected. The Python parser's input is this
projection format; this fixture is not an original server response or HTML.

The browser routine was exercised against these seven real pages. Unit-test
mutations and constructed React streams are synthetic edge cases, not additional
live observations. The JavaScript routine joins JSON stream chunks before
traversal; it does not execute page script text.

`hold_projections_20260910_11.json` copies two retained public projections without
changing their fields: `pilot_20260910T013743Z-be38fdbd/capture_05.json` and
`pilot_20260911T022302Z-c0295560/capture_11.json` under
`data/experiments/carvana_sale_signals/`. Their exact target badges are
`On Hold\n00:00` and `On Hold\n19:08`. They are observed source fixtures;
mutations in tests are explicitly synthetic conflict/format cases. The original
captures and their manifests remain unchanged.
