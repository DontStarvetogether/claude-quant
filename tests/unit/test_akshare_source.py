"""
单元测试：AkshareSource 列映射 / adj_factor 计算 / 日历过滤。

所有测试使用 unittest.mock 拦截 akshare 网络调用，无需网络连接。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import date
from unittest.mock import MagicMock, patch


# ── 辅助函数：构造 akshare 返回的 DataFrame ──────────────────────────────────

def _make_ak_bars(
    dates: list[str],
    opens: list[float],
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],    # 手
    amounts: list[float],
    price_changes: list[float],  # 涨跌额
) -> pd.DataFrame:
    """构造 akshare stock_zh_a_hist 返回格式的 DataFrame。"""
    return pd.DataFrame({
        "日期": dates,
        "开盘": opens,
        "收盘": closes,
        "最高": highs,
        "最低": lows,
        "成交量": volumes,    # 手
        "成交额": amounts,
        "涨跌额": price_changes,
    })


def _make_ak_calendar(dates: list[str]) -> pd.DataFrame:
    """构造 akshare tool_trade_date_hist_sina 返回格式。"""
    return pd.DataFrame({"交易日期": dates})


# ── 列名映射测试 ─────────────────────────────────────────────────────────────

class TestAkshareSourceColumnMapping:
    """验证 akshare 中文列名正确映射到内部英文列名。"""

    def _get_source(self):
        """延迟导入，避免 ImportError（akshare 未安装时）。"""
        from cq.data.source.akshare import AkshareSource
        return AkshareSource()

    def test_output_columns_correct(self):
        """fetch_daily_bars 返回标准列名集合。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02", "2024-01-03"],
            opens=[10.0, 10.5],
            closes=[10.0, 11.0],
            highs=[10.5, 11.5],
            lows=[9.5, 10.0],
            volumes=[1000.0, 2000.0],
            amounts=[100000.0, 200000.0],
            price_changes=[0.5, 1.0],
        )

        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 3))

        expected_cols = {
            "trade_date", "open", "high", "low", "close",
            "volume", "amount", "pre_close", "is_st", "is_suspended",
        }
        assert set(result.columns) == expected_cols

    def test_volume_converted_from_lots_to_shares(self):
        """成交量从手（lot）转换为股（share）：×100。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02"],
            opens=[10.0], closes=[10.0], highs=[10.5], lows=[9.5],
            volumes=[1000.0],   # 1000 手
            amounts=[100000.0],
            price_changes=[0.5],
        )
        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 2))
        assert result["volume"].iloc[0] == 100_000  # 1000 × 100

    def test_pre_close_derived_from_price_change(self):
        """pre_close = close - 涨跌额。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02"],
            opens=[10.5], closes=[11.0], highs=[11.5], lows=[10.0],
            volumes=[1000.0], amounts=[110000.0],
            price_changes=[1.0],   # 涨跌额 = 1.0
        )
        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 2))
        # pre_close = 11.0 - 1.0 = 10.0
        assert result["pre_close"].iloc[0] == pytest.approx(10.0)

    def test_is_suspended_when_zero_volume_and_amount(self):
        """成交量和成交额均为 0 时判断为停牌。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02"],
            opens=[10.0], closes=[10.0], highs=[10.0], lows=[10.0],
            volumes=[0.0],       # 停牌：volume=0
            amounts=[0.0],       # 停牌：amount=0
            price_changes=[0.0],
        )
        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 2))
        assert result["is_suspended"].iloc[0] is True or result["is_suspended"].iloc[0] == True

    def test_is_suspended_false_when_trading(self):
        """正常交易日 is_suspended=False。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02"],
            opens=[10.0], closes=[10.5], highs=[10.8], lows=[9.9],
            volumes=[5000.0], amounts=[500000.0],
            price_changes=[0.5],
        )
        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 2))
        assert result["is_suspended"].iloc[0] is False or result["is_suspended"].iloc[0] == False

    def test_is_st_always_false(self):
        """is_st 当前版本固定为 False（历史 ST 数据不可用）。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02", "2024-01-03"],
            opens=[10.0, 10.0], closes=[10.0, 10.0], highs=[10.0, 10.0], lows=[10.0, 10.0],
            volumes=[1000.0, 1000.0], amounts=[100000.0, 100000.0],
            price_changes=[0.0, 0.0],
        )
        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 3))
        assert result["is_st"].all() == False

    def test_trade_date_is_python_date(self):
        """trade_date 列应为 Python date 对象，不是字符串。"""
        source = self._get_source()
        raw = _make_ak_bars(
            dates=["2024-01-02"],
            opens=[10.0], closes=[10.0], highs=[10.0], lows=[10.0],
            volumes=[1000.0], amounts=[100000.0], price_changes=[0.0],
        )
        with patch("akshare.stock_zh_a_hist", return_value=raw):
            result = source.fetch_daily_bars("600519.SH", date(2024, 1, 2), date(2024, 1, 2))
        assert isinstance(result["trade_date"].iloc[0], date)


# ── adj_factor 计算测试 ───────────────────────────────────────────────────────

class TestAkshareSourceAdjFactor:
    """验证 adj_factor = raw_close / qfq_close 的计算正确性。"""

    def _get_source(self):
        from cq.data.source.akshare import AkshareSource
        return AkshareSource()

    def _call_fetch_adj_factors(
        self,
        raw_closes: list[float],
        qfq_closes: list[float],
        dates: list[str] | None = None,
    ) -> pd.DataFrame:
        """辅助：构造 mock 数据并调用 fetch_adj_factors。"""
        source = self._get_source()
        n = len(raw_closes)
        if dates is None:
            dates = [f"2024-0{i+1}-01" if i < 9 else f"2024-{i+1}-01" for i in range(n)]

        raw = _make_ak_bars(
            dates=dates, opens=raw_closes, closes=raw_closes,
            highs=raw_closes, lows=raw_closes,
            volumes=[1000.0] * n, amounts=[c * 1000 for c in raw_closes],
            price_changes=[0.0] * n,
        )
        qfq = _make_ak_bars(
            dates=dates, opens=qfq_closes, closes=qfq_closes,
            highs=qfq_closes, lows=qfq_closes,
            volumes=[1000.0] * n, amounts=[c * 1000 for c in qfq_closes],
            price_changes=[0.0] * n,
        )

        with patch("akshare.stock_zh_a_hist", side_effect=[raw, qfq]):
            return source.fetch_adj_factors("600519.SH", date(2024, 1, 1), date(2024, 9, 1))

    def test_split_2for1_adj_factor_is_two(self):
        """2-for-1 股权后，除权前的 adj_factor 应为 2.0。"""
        # raw:  [10, 10, 5]  →  stock split on day 3 (price halved)
        # qfq:  [ 5,  5, 5]  →  continuous (all adjusted to post-split level)
        result = self._call_fetch_adj_factors(
            raw_closes=[10.0, 10.0, 5.0],
            qfq_closes=[5.0,  5.0, 5.0],
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        )
        assert not result.empty
        # 除权前日期的 adj_factor 应为 2.0
        pre_split = result[result["trade_date"] < date(2024, 1, 3)]
        assert not pre_split.empty
        assert pre_split["adj_factor"].iloc[-1] == pytest.approx(2.0, abs=1e-4)

    def test_adj_factor_latest_approx_one(self):
        """最新日期的 adj_factor 应约等于 1.0（qfq 以最新日为基准）。"""
        result = self._call_fetch_adj_factors(
            raw_closes=[10.0, 10.0, 5.0],
            qfq_closes=[5.0,  5.0, 5.0],
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        )
        assert not result.empty
        latest_date = result["trade_date"].max()
        latest_row = result[result["trade_date"] == latest_date]
        assert latest_row["adj_factor"].iloc[0] == pytest.approx(1.0, abs=1e-4)

    def test_no_split_returns_empty_dataframe(self):
        """未发生复权时（raw == qfq），返回空 DataFrame。"""
        result = self._call_fetch_adj_factors(
            raw_closes=[10.0, 10.5, 11.0],
            qfq_closes=[10.0, 10.5, 11.0],  # same as raw
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        )
        assert result.empty

    def test_adj_factor_pre_split_gt_one(self):
        """除权前的 adj_factor 大于 1（历史价格需向下调整）。"""
        result = self._call_fetch_adj_factors(
            raw_closes=[20.0, 20.0, 10.0],
            qfq_closes=[10.0, 10.0, 10.0],
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        )
        pre_split_rows = result[result["adj_factor"] > 1.0 + 1e-6]
        assert not pre_split_rows.empty
        assert all(pre_split_rows["adj_factor"] > 1.0)

    def test_adjuster_produces_correct_qfq(self):
        """与 PriceAdjuster 联合验证：adj_factor 代入后得到正确的前复权价格。"""
        from cq.data.adjust.adjuster import PriceAdjuster

        raw_closes = [10.0, 10.0, 5.0]
        qfq_closes = [5.0,  5.0, 5.0]

        result = self._call_fetch_adj_factors(
            raw_closes=raw_closes,
            qfq_closes=qfq_closes,
            dates=["2024-01-01", "2024-01-02", "2024-01-03"],
        )

        raw_df = pd.DataFrame({
            "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
            "open": raw_closes, "high": raw_closes, "low": raw_closes, "close": raw_closes,
            "volume": [1000, 1000, 1000], "amount": [10000.0, 10000.0, 5000.0],
            "pre_close": [10.0, 10.0, 10.0],
        })

        adjuster = PriceAdjuster()
        qfq_out = adjuster.apply_qfq(raw_df, result)

        # 最新日期收盘价不变
        latest_close = qfq_out.loc[qfq_out["trade_date"] == date(2024, 1, 3), "close"].iloc[0]
        assert latest_close == pytest.approx(5.0, abs=0.01)

        # 除权前收盘价被向下调整为 5.0
        hist_close = qfq_out.loc[qfq_out["trade_date"] == date(2024, 1, 2), "close"].iloc[0]
        assert hist_close == pytest.approx(5.0, abs=0.01)


# ── 交易日历测试 ─────────────────────────────────────────────────────────────

class TestAkshareSourceCalendar:
    """验证交易日历的年份过滤和缓存行为。"""

    def _get_fresh_source(self):
        """每次测试使用独立实例，清空类级缓存。"""
        from cq.data.source.akshare import AkshareSource
        AkshareSource._calendar_cache = None
        return AkshareSource()

    def test_calendar_filters_by_year(self):
        """只返回指定年份的交易日。"""
        source = self._get_fresh_source()
        mock_cal = _make_ak_calendar([
            "2021-01-04", "2021-12-31",
            "2022-01-04", "2022-06-15", "2022-12-30",
            "2023-01-03",
        ])
        with patch("akshare.tool_trade_date_hist_sina", return_value=mock_cal):
            result = source.fetch_trading_calendar("SSE", 2022)
        assert all(d.year == 2022 for d in result)
        assert len(result) == 3

    def test_calendar_returns_sorted_dates(self):
        """返回的日期列表升序排列。"""
        source = self._get_fresh_source()
        mock_cal = _make_ak_calendar(["2022-12-30", "2022-01-04", "2022-06-15"])
        with patch("akshare.tool_trade_date_hist_sina", return_value=mock_cal):
            result = source.fetch_trading_calendar("SSE", 2022)
        assert result == sorted(result)

    def test_calendar_caches_on_second_call(self):
        """第二次调用不重复请求 akshare API（使用类级缓存）。"""
        source = self._get_fresh_source()
        mock_cal = _make_ak_calendar(["2022-01-04", "2023-01-03"])
        with patch("akshare.tool_trade_date_hist_sina", return_value=mock_cal) as mock_fn:
            source.fetch_trading_calendar("SSE", 2022)
            source.fetch_trading_calendar("SSE", 2023)  # 第二次调用
            assert mock_fn.call_count == 1  # 只调用了一次

    def test_calendar_returns_date_objects(self):
        """返回 Python date 对象，不是字符串。"""
        source = self._get_fresh_source()
        mock_cal = _make_ak_calendar(["2022-01-04"])
        with patch("akshare.tool_trade_date_hist_sina", return_value=mock_cal):
            result = source.fetch_trading_calendar("SSE", 2022)
        assert all(isinstance(d, date) for d in result)


# ── 股票信息 / 板块识别测试 ──────────────────────────────────────────────────

class TestAkshareSourceStockInfo:
    """验证 board 识别和 exchange 推断。"""

    def _get_source(self):
        from cq.data.source.akshare import AkshareSource
        return AkshareSource()

    def _mock_info(self, name: str = "测试股") -> pd.DataFrame:
        return pd.DataFrame({
            "item": ["股票简称", "上市时间", "行业"],
            "value": [name, "2020-01-01", "制造业"],
        })

    def test_board_star_market(self):
        """688xxx → board='star'（科创板）。"""
        source = self._get_source()
        with patch("akshare.stock_individual_info_em", return_value=self._mock_info()):
            info = source.fetch_stock_info("688001.SH")
        assert info["board"] == "star"

    def test_board_gem(self):
        """300xxx → board='gem'（创业板）。"""
        source = self._get_source()
        with patch("akshare.stock_individual_info_em", return_value=self._mock_info()):
            info = source.fetch_stock_info("300750.SZ")
        assert info["board"] == "gem"

    def test_board_main(self):
        """600xxx → board='main'（主板）。"""
        source = self._get_source()
        with patch("akshare.stock_individual_info_em", return_value=self._mock_info()):
            info = source.fetch_stock_info("600519.SH")
        assert info["board"] == "main"

    def test_board_bj(self):
        """8xxxxx → board='bj'（北交所）。"""
        source = self._get_source()
        with patch("akshare.stock_individual_info_em", return_value=self._mock_info()):
            info = source.fetch_stock_info("835796.BJ")
        assert info["board"] == "bj"

    def test_exchange_from_suffix_sh(self):
        """.SH → exchange='SH'。"""
        source = self._get_source()
        with patch("akshare.stock_individual_info_em", return_value=self._mock_info()):
            info = source.fetch_stock_info("600519.SH")
        assert info["exchange"] == "SH"

    def test_exchange_from_suffix_sz(self):
        """.SZ → exchange='SZ'。"""
        source = self._get_source()
        with patch("akshare.stock_individual_info_em", return_value=self._mock_info()):
            info = source.fetch_stock_info("000001.SZ")
        assert info["exchange"] == "SZ"


# ── 代码格式转换测试 ─────────────────────────────────────────────────────────

class TestSymbolConversion:
    def test_strips_sh_suffix(self):
        from cq.data.source.akshare import AkshareSource
        assert AkshareSource._to_ak_code("600519.SH") == "600519"

    def test_strips_sz_suffix(self):
        from cq.data.source.akshare import AkshareSource
        assert AkshareSource._to_ak_code("000001.SZ") == "000001"

    def test_strips_bj_suffix(self):
        from cq.data.source.akshare import AkshareSource
        assert AkshareSource._to_ak_code("835796.BJ") == "835796"
