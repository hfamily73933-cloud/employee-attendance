# REVIEW.md

## Defects and fixes

| # | Where | What is wrong | How to notice | How fixed |
|---|---|---|---|---|
| 1 | Time helper: `compute_late_minutes` | Late minutes must be measured from shift start, with a strict 10-minute grace period. | Test exactly 10 minutes late and one second beyond the grace period. | Apply the strict threshold and calculate whole minutes from shift start. |
| 2 | Time helper: `compute_work_hours` | Standard rounding can produce incorrect results for half-up rounding requirements. | Test a duration that rounds to a half-up boundary. | Use `Decimal` with `ROUND_HALF_UP` to two decimal places. |
| 3 | Time helper: `compute_overtime` | Overnight shifts require the shift end to fall on the following calendar day. | Test an overnight shift at and below the 30-minute threshold. | Adjust the shift-end date for overnight shifts and apply the minimum threshold. |
| 4 | Attendance date helper: `attendance_date_for` | Early-morning punches for overnight shifts must belong to the previous shift date when they occur before shift end. | Test punches immediately before and at shift end. | Apply the overnight-shift date rule. |

## Other checks

- Verified pagination, filters, and the maximum page size.
- Verified attendance punch-in, punch-out, and manual correction history.
- Verified aggregation-based analytics and explain plans.
- Ran the helper tests successfully: 9 passed.

These entries describe the behaviors addressed and tested in the implementation. Confirm each against the original starter before treating it as a defect that existed in the starter.
