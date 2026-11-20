"""Time helpers. All timestamps are stored as naive local ISO strings."""

from __future__ import annotations

from datetime import date, datetime

DATE_FMT = "%Y-%m-%d"
TS_FMT = "%Y-%m-%d %H:%M"


def now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def fmt_date(d: date | datetime) -> str:
    return d.strftime(DATE_FMT)


def fmt_ts(dt: datetime) -> str:
    return dt.strftime(TS_FMT)


def parse_date(s: str) -> date:
    return datetime.strptime(s[:10], DATE_FMT).date()


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s[:16], TS_FMT)
