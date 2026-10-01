"""Generate the Friday-Sunday pairs to search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

FRIDAY = 4


@dataclass(frozen=True)
class Weekend:
    friday: date

    @property
    def sunday(self) -> date:
        return self.friday + timedelta(days=2)


def upcoming_weekends(today: date, count: int) -> list[Weekend]:
    """The next `count` weekends. If today is Friday, this weekend is included
    (an evening flight may still be bookable); on Saturday/Sunday it is not."""
    first = today + timedelta(days=(FRIDAY - today.weekday()) % 7)
    return [Weekend(first + timedelta(weeks=i)) for i in range(count)]


def weekends_in_range(today: date, first_friday: date, last_friday: date) -> list[Weekend]:
    """Every weekend whose Friday falls in [first_friday, last_friday], minus
    those already past by the same rule as upcoming_weekends()."""
    start = max(first_friday, today)
    friday = start + timedelta(days=(FRIDAY - start.weekday()) % 7)
    out = []
    while friday <= last_friday:
        out.append(Weekend(friday))
        friday += timedelta(weeks=1)
    return out
