"""单元测试：交易日历"""

import pytest
from datetime import date

from cq.data.calendar import TradingCalendar


@pytest.fixture
def calendar():
    # 构造一个简单的测试日历（跳过周末）
    days = [
        date(2024, 1, 2),   # 周二
        date(2024, 1, 3),   # 周三
        date(2024, 1, 4),   # 周四
        date(2024, 1, 5),   # 周五
        # 跳过 1/6 (周六), 1/7 (周日)
        date(2024, 1, 8),   # 周一
        date(2024, 1, 9),   # 周二
        date(2024, 1, 10),  # 周三
    ]
    return TradingCalendar(days)


class TestTradingCalendar:
    def test_is_trading_day(self, calendar):
        assert calendar.is_trading_day(date(2024, 1, 2))
        assert not calendar.is_trading_day(date(2024, 1, 6))  # 周末
        assert not calendar.is_trading_day(date(2024, 1, 7))

    def test_next_trading_day(self, calendar):
        assert calendar.next_trading_day(date(2024, 1, 5)) == date(2024, 1, 8)  # 跳过周末
        assert calendar.next_trading_day(date(2024, 1, 2)) == date(2024, 1, 3)

    def test_prev_trading_day(self, calendar):
        assert calendar.prev_trading_day(date(2024, 1, 8)) == date(2024, 1, 5)  # 跳过周末
        assert calendar.prev_trading_day(date(2024, 1, 3)) == date(2024, 1, 2)

    def test_trading_days_between(self, calendar):
        days = calendar.trading_days_between(date(2024, 1, 3), date(2024, 1, 9))
        assert days == [
            date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
            date(2024, 1, 8), date(2024, 1, 9),
        ]

    def test_contains_operator(self, calendar):
        assert date(2024, 1, 2) in calendar
        assert date(2024, 1, 6) not in calendar

    def test_next_day_out_of_range_raises(self, calendar):
        with pytest.raises(ValueError):
            calendar.next_trading_day(date(2024, 1, 10))  # 已是最后一个交易日

    def test_empty_calendar_raises(self):
        with pytest.raises(ValueError):
            TradingCalendar([])
