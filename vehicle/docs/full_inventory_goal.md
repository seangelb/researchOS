# Successfully complete full Carvana inventory runs

Updated September 19, 2026 following the user's requests to update the goal,
expand the scraper to the full inventory, extend its budget and improve speed.
This is the revised repository goal. After the user deleted the previous app goal,
a new active app goal was created with this successful-full-runs objective.
The user reiterated that successful full runs are the objective and requested
Ultra reasoning. Implementation progress must not be reported as a completed run.

## Objective and definition of done

Set up a script that successfully completes full public searchable Carvana
inventory runs, then demonstrate repeatability. Collect the population daily within a
declared request and time allowance, and compare each VIN with its complete
retained observation history. Stop using approximately 10,000 VINs as the target.
Measure discovery gaps, geographic differences, changing counts and failed queries.
Do not turn an observed union or native count equality into national completeness.

The goal is complete only after:

1. A full-inventory baseline reconciles discovery categories, retained pages,
   VIN/listing identities, collected SQLite, source clocks and exported tables.
2. Opening and closing native counts, unknown/unclassified categories and ZIP
   differences are explicit. Additional categories or ZIP-only vehicles lead to
   reviewed expansion of the next plan, not silent omission or mid-run retries.
3. At least a second complete run demonstrates repeatability; a newly declared
   seven-date full-inventory operating trial retains all planned
   dates, including partial, failed and missed dates, and demonstrates reliability.
4. The broader history is compared with prior evidence at compatible query scopes.
   Old partial dates contribute positive observations, not proof of absence.
   Newly discovered vehicles are newly observed, not necessarily newly listed.
5. Pending transitions, disappearances and reappearances produce a follow-up queue;
   native Sold checks validate the signal with explicit missingness and uncertainty.
   Companywide sales estimation remains withheld until coverage and labeling support it.
6. Source and data backups are restored and hash-verified; notebooks reproduce the
   published findings offline without altering source evidence or approvals.

## Current implementation and limits

The new [full-inventory workflow](full_inventory.md) discovers current makes and
model families, includes all vehicle years, uses a single 6,000-request/six-hour
budget and checks additional ZIP contexts. It is implemented and tested offline;
a full live baseline and sustained reliability are separate acceptance milestones.
Three-second sequential spacing, no automatic retry, and global fatal-error stops
remain. The new budget is a ceiling, not a promise that every changing population fits.

Preserve the original 101-query trial, its original seven-date denominator, the
failed facet experiment, all Tesla/status-study evidence and the frozen 32 VINs.
Do not rewrite their approvals, schedules or data to fit the expanded scope.
The existing September 21 final audit still covers those original experiments.
The full-inventory workflow uses separate dated captures and analysis exports.

No gaming work, purchases, vendor outreach or transaction-price claims are added.
Operator labor remains unknown unless separately measured.
