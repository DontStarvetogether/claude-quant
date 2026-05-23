"""单元测试：前复权计算"""

import pytest
import pandas as pd
from datetime import date

from cq.data.adjust.adjuster import PriceAdjuster


def make_raw_df():
    return pd.DataFrame({
        "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        "open":  [10.0, 10.0,  5.0],  # 1/3 除权，价格减半
        "high":  [11.0, 11.0,  5.5],
        "low":   [ 9.0,  9.0,  4.5],
        "close": [10.0, 10.0,  5.0],
        "volume": [1000, 1000, 2000],
        "amount": [10000.0, 10000.0, 10000.0],
        "pre_close": [9.5, 10.0, 10.0],  # 原始昨收
        "is_st": [False, False, False],
        "is_suspended": [False, False, False],
        "limit_up": [11.0, 11.0, 5.5],
        "limit_down": [9.0, 9.0, 4.5],
    })


def make_adj_df():
    # 1/1 和 1/2 因子为 2.0（除权前），1/3 因子变为 1.0（除权后）
    return pd.DataFrame({
        "trade_date": [date(2024, 1, 1), date(2024, 1, 3)],
        "adj_factor": [2.0, 1.0],
    })


class TestPriceAdjuster:
    def test_qfq_latest_price_unchanged(self):
        """前复权后，最新日期的收盘价应等于原始收盘价。"""
        adjuster = PriceAdjuster()
        raw = make_raw_df()
        adj = make_adj_df()

        qfq = adjuster.apply_qfq(raw, adj)

        # 最新日期（1/3）收盘价应与原始相同
        latest_close = qfq.loc[qfq["trade_date"] == date(2024, 1, 3), "close"].iloc[0]
        raw_latest_close = raw.loc[raw["trade_date"] == date(2024, 1, 3), "close"].iloc[0]
        assert latest_close == pytest.approx(raw_latest_close)

    def test_qfq_historical_price_adjusted(self):
        """前复权后，除权前的历史价格应被向下调整（消除除权跳跃）。"""
        adjuster = PriceAdjuster()
        raw = make_raw_df()
        adj = make_adj_df()

        qfq = adjuster.apply_qfq(raw, adj)

        # 1/2 的收盘价（adj_factor=2, latest_factor=1）→ 10 * 1/2 = 5
        hist_close = qfq.loc[qfq["trade_date"] == date(2024, 1, 2), "close"].iloc[0]
        assert hist_close == pytest.approx(5.0, abs=0.01)

    def test_qfq_adjusts_pre_close_to_same_scale(self):
        """前复权后 pre_close 应与 OHLC 保持同一价格尺度。"""
        adjuster = PriceAdjuster()
        qfq = adjuster.apply_qfq(make_raw_df(), make_adj_df())
        hist_pre_close = qfq.loc[qfq["trade_date"] == date(2024, 1, 2), "pre_close"].iloc[0]
        assert hist_pre_close == pytest.approx(5.0, abs=0.01)

    def test_qfq_adjusts_limit_prices_to_same_scale(self):
        """涨跌停价也必须随 qfq 同尺度调整，否则撮合会错把复权价和原始价比较。"""
        adjuster = PriceAdjuster()
        qfq = adjuster.apply_qfq(make_raw_df(), make_adj_df())

        hist = qfq[qfq["trade_date"] == date(2024, 1, 2)].iloc[0]
        assert hist["limit_up"] == pytest.approx(5.5, abs=0.01)
        assert hist["limit_down"] == pytest.approx(4.5, abs=0.01)

    def test_qfq_preserves_volume(self):
        """成交量不受复权影响。"""
        adjuster = PriceAdjuster()
        raw = make_raw_df()
        qfq = adjuster.apply_qfq(raw, make_adj_df())
        assert qfq["volume"].tolist() == raw["volume"].tolist()

    def test_detect_split_dates(self):
        adjuster = PriceAdjuster()
        adj = make_adj_df()
        split_dates = adjuster.detect_split_dates(adj)
        # adj_factor 从 2.0 变为 1.0 发生在 1/3
        assert date(2024, 1, 3) in split_dates

    def test_no_adj_factor_returns_unchanged(self):
        """无复权因子时，价格不变。"""
        adjuster = PriceAdjuster()
        raw = make_raw_df()
        qfq = adjuster.apply_qfq(raw, pd.DataFrame())
        assert qfq["close"].tolist() == pytest.approx(raw["close"].tolist())
