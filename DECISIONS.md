# DECISIONS.md

1. **Indexes.** I created a unique index on `employees.emp_code` to prevent duplicate employee codes, and a unique compound index on `attendance_logs.emp_code` and `date` to enforce one attendance record per employee per day. The date-and-employee index supports attendance date filtering and sorting. Employee department and joining-date indexes support department analytics. I also created an attendance status-and-date index for status-based queries. I would avoid adding overlapping indexes without query-plan evidence because each index adds storage and write overhead.

2. **Punch-in race.** Both requests can initially find the employee. They then attempt to insert the same employee/date record. The unique compound index allows only one insert; the other raises `DuplicateKeyError` and receives HTTP 409. This protects the one-record-per-day guarantee at the database level.

3. **Ties.** The leaderboard sorts by total late minutes and then employee code for deterministic ordering, assigns ranks using `$rank`, and limits the final output to the requested number of rows. A tie at the cutoff may therefore be split by the limit.

4. **Headcount.** Department summary starts from eligible employee documents and uses `$lookup` for attendance. Employees without logs remain in the employee set and contribute to headcount.

5. **100x scale.** I would review explain plans and workload metrics, then consider partitioning or archival strategies and further query optimization based on measured bottlenecks.
