# REVIEW.md

## Behaviors Reviewed and Fixes Applied

| # | Where | Requirement or Behavior | How to Verify | Implementation |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Enforce a strict 10-minute grace period and calculate late minutes relative to the scheduled shift start. | Test an arrival exactly 10 minutes late and an arrival just beyond the grace period. | Apply the strict threshold and calculate whole late minutes from the scheduled shift start. |
| 2 | `compute_work_hours` | Round calculated work hours using the required half-up rounding rule. | Test a duration that produces a half-up rounding boundary. | Use `Decimal` with `ROUND_HALF_UP` to round work hours to two decimal places. |
| 3 | `compute_overtime` | Handle overnight shifts correctly and apply the 30-minute overtime threshold. | Test overtime below, exactly at, and above 30 minutes, including an overnight shift. | Adjust the shift-end date when necessary and apply the required threshold. |
| 4 | `attendance_date_for` | Associate early-morning punches with the correct attendance date for overnight shifts. | Test punches immediately before and at the overnight shift-end time. | Apply the overnight-shift attendance-date rule. |

## Validation

- Verified attendance filtering, pagination, and the maximum page size.
- Tested punch-in, punch-out, and manual attendance correction history.
- Reviewed aggregation-based analytics and MongoDB explain plans.
- Ran the helper test suite successfully: 9 tests passed.

The entries above describe implementation behaviors and validation performed. They are not claims that the same issues existed in the original starter unless confirmed by comparison with that code.
