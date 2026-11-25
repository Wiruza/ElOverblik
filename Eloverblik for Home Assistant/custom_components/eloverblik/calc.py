"""Turning hourly readings into the numbers the sensors show (Danish calendar days).

Written for Power Monitor; the same rules as its desktop app, so both give the same totals.
Pure Python with no Home Assistant imports, so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Copenhagen")

# hour start (Unix seconds, UTC) -> (kWh, quality)
Hours = dict[int, tuple[float, int]]


def local_dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ)


def local_date(ts: int) -> date:
    return local_dt(ts).date()


def today_dk(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(TZ).date()


def day_start(d: date) -> int:
    """First instant of a Danish calendar day, as Unix seconds."""
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())


def prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def days_in_month(year: int, month: int) -> int:
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    return (date(ny, nm, 1) - date(year, month, 1)).days


@dataclass
class DayAgg:
    total: float = 0.0
    hours: list[float] = field(default_factory=lambda: [0.0] * 24)
    count: int = 0
    estimated: int = 0

    @property
    def complete(self) -> bool:
        """A whole day of readings (23 hours on the spring DST day)."""
        return self.count >= 23


@dataclass
class Summary:
    latest_day: date | None = None
    latest_day_total: float | None = None
    latest_day_hours: list[float] = field(default_factory=lambda: [0.0] * 24)
    last7: tuple[date, date, float] | None = None
    month: tuple[int, int, float] | None = None
    last_month: tuple[int, int, float] | None = None
    year: tuple[int, float] | None = None
    latest_hour: tuple[int, float] | None = None
    data_until: int | None = None
    hours_stored: int = 0


def build_daily(hours: Hours) -> dict[date, DayAgg]:
    daily: dict[date, DayAgg] = {}
    for ts in sorted(hours):
        kwh, quality = hours[ts]
        lt = local_dt(ts)
        agg = daily.setdefault(lt.date(), DayAgg())
        agg.total += kwh
        agg.hours[lt.hour] += kwh
        agg.count += 1
        if quality:
            agg.estimated += 1
    return daily


def _sum_days(daily: dict[date, DayAgg], start: date, end: date, need_all: bool) -> float | None:
    total, any_day, d = 0.0, False, start
    while d <= end:
        agg = daily.get(d)
        if agg is not None:
            total += agg.total
            any_day = True
        elif need_all:
            return None
        d += timedelta(days=1)
    return total if any_day else None


def summarize(hours: Hours) -> Summary:
    s = Summary(hours_stored=len(hours))
    if not hours:
        return s
    last = max(hours)
    s.data_until = last + 3600
    s.latest_hour = (last, hours[last][0])
    daily = build_daily(hours)
    complete = [d for d, agg in daily.items() if agg.complete]
    if not complete:
        return s
    latest = max(complete)
    s.latest_day = latest
    s.latest_day_total = daily[latest].total
    s.latest_day_hours = list(daily[latest].hours)

    start7 = latest - timedelta(days=6)
    t7 = _sum_days(daily, start7, latest, False)
    if t7 is not None:
        s.last7 = (start7, latest, t7)

    y, m = latest.year, latest.month
    tm = _sum_days(daily, date(y, m, 1), latest, False)
    if tm is not None:
        s.month = (y, m, tm)
    py, pm = prev_month(y, m)
    if date(py, pm, 1) in daily:
        tl = _sum_days(daily, date(py, pm, 1), date(py, pm, days_in_month(py, pm)), False)
        if tl is not None:
            s.last_month = (py, pm, tl)
    ty = _sum_days(daily, date(y, 1, 1), latest, False)
    if ty is not None:
        s.year = (y, ty)
    return s


def statistics_rows(hours: Hours, after: int | None, start_sum: float) -> list[tuple[int, float]]:
    """Hourly long-term statistics rows (hour start, running total) for hours after `after`,
    continuing the running total from `start_sum`."""
    rows: list[tuple[int, float]] = []
    total = start_sum
    for ts in sorted(hours):
        if after is not None and ts <= after:
            continue
        total += hours[ts][0]
        rows.append((ts, round(total, 4)))
    return rows


def history_start(today: date, newest: int | None, history_days: int, overlap_days: int) -> date:
    """First day to download: a few days before the newest reading, or the chosen history."""
    if newest is not None:
        start = local_date(newest) - timedelta(days=overlap_days)
    else:
        start = today - timedelta(days=max(7, min(history_days, 1800)))
    return max(start, date(today.year - 5, 1, 1))


def chunks(start: date, end: date, days: int = 365) -> list[tuple[date, date]]:
    """Split [start, end) into pieces Eloverblik accepts (it allows at most 730 days per call)."""
    out = []
    while start < end:
        stop = min(start + timedelta(days=days), end)
        out.append((start, stop))
        start = stop
    return out
