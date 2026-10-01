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
