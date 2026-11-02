from datetime import date

from app.services.calendar import is_trading_day, previous_trading_day


def test_b3_calendar_excludes_weekends_and_movable_closures():
    assert not is_trading_day(date(2026, 2, 16))
    assert not is_trading_day(date(2026, 2, 17))
    assert not is_trading_day(date(2026, 6, 4))
    assert not is_trading_day(date(2026, 11, 2))
    assert is_trading_day(date(2026, 11, 3))


def test_previous_trading_day_skips_weekend_and_holiday():
    assert previous_trading_day(date(2026, 11, 3)) == date(2026, 10, 30)
