import json
from datetime import date, timedelta
from pathlib import Path

import holidays
from dateutil.easter import easter

CALENDAR_FILE = Path(__file__).resolve().parents[2] / "config" / "b3_holidays.json"


def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    brazil_holidays = holidays.country_holidays("BR", subdiv="SP", years=[day.year])
    easter_sunday = easter(day.year)
    b3_movable_closures = {
        easter_sunday - timedelta(days=48),
        easter_sunday - timedelta(days=47),
        easter_sunday + timedelta(days=60),
    }
    configured = set()
    if CALENDAR_FILE.exists():
        configured = set(json.loads(CALENDAR_FILE.read_text(encoding="utf-8")))
    return (
        day not in brazil_holidays
        and day not in b3_movable_closures
        and day.isoformat() not in configured
    )


def previous_trading_day(day: date) -> date:
    candidate = day - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate
