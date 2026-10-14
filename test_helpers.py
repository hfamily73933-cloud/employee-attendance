
from datetime import datetime, timezone

from app.main import (
    IST,
    attendance_date_for,
    compute_late_minutes,
    compute_overtime,
    compute_work_hours,
)


def test_late_grace_period():
    assert compute_late_minutes(
        datetime(2026, 1, 5, 4, 10, tzinfo=timezone.utc),
        "09:30",
        "2026-01-05",
    ) == 0


def test_late_after_grace():
    assert compute_late_minutes(
        datetime(2026, 1, 5, 4, 10, 1, tzinfo=timezone.utc),
        "09:30",
        "2026-01-05",
    ) == 10


def test_late_minutes_are_counted_from_shift_start():
    assert compute_late_minutes(
        datetime(2026, 1, 5, 4, 45, 59, tzinfo=timezone.utc),
        "09:30",
        "2026-01-05",
    ) == 45


def test_early_punch_for_day_shift_is_not_previous_day():
    # 06:00 IST is early, but it belongs to the same day's shift.
    assert compute_late_minutes(
        datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc),
        "09:30",
        "2026-01-05",
    ) == 0


def test_half_up_work_hours():
    assert compute_work_hours(
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 4, 29, 6, tzinfo=timezone.utc),
    ) == 4.49


def test_overnight_overtime_below_threshold():
    # 06:15 IST is only 15 minutes after shift end.
    assert compute_overtime(
        datetime(2026, 1, 6, 0, 45, tzinfo=timezone.utc),
        "06:00",
        "2026-01-05",
        "22:00",
    ) == 0


def test_overnight_overtime_at_threshold():
    # 06:30 IST is exactly 30 minutes after shift end.
    assert compute_overtime(
        datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc),
        "06:00",
        "2026-01-05",
        "22:00",
    ) == 30


def test_overnight_attendance_date():
    employee = {
        "shift_start": "22:00",
        "shift_end": "06:00",
    }

    # 05:30 IST on January 6 belongs to the January 5 shift.
    punch_in = datetime(2026, 1, 6, 0, 0, tzinfo=timezone.utc)
    assert attendance_date_for(punch_in, employee) == "2026-01-05"


def test_overnight_attendance_date_after_shift_end():
    employee = {
        "shift_start": "22:00",
        "shift_end": "06:00",
    }

    # Exactly 06:00 IST is not earlier than shift_end.
    punch_in = datetime(2026, 1, 6, 0, 30, tzinfo=timezone.utc)
    assert attendance_date_for(punch_in, employee) == "2026-01-06"
