import calendar
from datetime import datetime, timezone
from typing import Optional
from app.models.enums import BillingInterval


def ensure_utc(dt: datetime) -> datetime:
    """
    Ensures a datetime is timezone-aware and normalized to UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def add_calendar_months(
    dt: datetime,
    months: int,
    anchor_day: Optional[int] = None,
) -> datetime:
    """
    Advances a datetime by an exact number of calendar months in UTC,
    handling month-end clamping (e.g., Jan 31 -> Feb 28/29) and leap years safely.
    When `anchor_day` is provided, restores the original billing day when subsequent
    months are long enough (e.g., Feb 28 -> Mar 31 for anchor_day=31).
    """
    utc_dt = ensure_utc(dt)
    month_index = utc_dt.month - 1 + months
    year = utc_dt.year + month_index // 12
    month = month_index % 12 + 1

    desired_day = anchor_day if anchor_day is not None else utc_dt.day
    max_day_in_month = calendar.monthrange(year, month)[1]
    day = min(desired_day, max_day_in_month)

    return utc_dt.replace(year=year, month=month, day=day)


def calculate_period_end(
    start: datetime,
    interval: BillingInterval,
    anchor_day: Optional[int] = None,
) -> datetime:
    """
    Computes the exact UTC period end timestamp for monthly, quarterly, or annual intervals.
    """
    if interval == BillingInterval.MONTHLY:
        return add_calendar_months(start, 1, anchor_day=anchor_day)
    if interval == BillingInterval.QUARTERLY:
        return add_calendar_months(start, 3, anchor_day=anchor_day)
    if interval == BillingInterval.YEARLY:
        return add_calendar_months(start, 12, anchor_day=anchor_day)
    raise ValueError(f"Unsupported billing interval: {interval}")

