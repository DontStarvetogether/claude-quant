"""单元测试：A股交易规则"""

import pytest
from cq.utils.trading_rules import AStockRules


class TestLimitPrices:
    def test_normal_stock_10pct(self):
        lp = AStockRules.calc_limit_prices(pre_close=10.0, is_st=False, symbol="600519.SH")
        assert lp.limit_up == pytest.approx(11.0, abs=0.01)
        assert lp.limit_down == pytest.approx(9.0, abs=0.01)

    def test_st_stock_5pct(self):
        lp = AStockRules.calc_limit_prices(pre_close=10.0, is_st=True, symbol="600519.SH")
        assert lp.limit_up == pytest.approx(10.5, abs=0.01)
        assert lp.limit_down == pytest.approx(9.5, abs=0.01)

    def test_star_market_20pct(self):
        lp = AStockRules.calc_limit_prices(pre_close=100.0, is_st=False, symbol="688001.SH")
        assert lp.limit_up == pytest.approx(120.0, abs=0.01)
        assert lp.limit_down == pytest.approx(80.0, abs=0.01)

    def test_floor_to_cent(self):
        """涨跌停价应向下取整到分（0.01元精度）。"""
        lp = AStockRules.calc_limit_prices(pre_close=3.33, is_st=False, symbol="000001.SZ")
        # 3.33 * 1.1 = 3.663 → floor to cent → 3.66
        assert lp.limit_up == pytest.approx(3.66, abs=0.001)


class TestLotCalculation:
    def test_round_to_lot(self):
        assert AStockRules.round_to_lot(250) == 200
        assert AStockRules.round_to_lot(100) == 100
        assert AStockRules.round_to_lot(99) == 0
        assert AStockRules.round_to_lot(1500.7) == 1500

    def test_is_valid_lot(self):
        assert AStockRules.is_valid_lot(100)
        assert AStockRules.is_valid_lot(1000)
        assert not AStockRules.is_valid_lot(0)
        assert not AStockRules.is_valid_lot(150)
        assert not AStockRules.is_valid_lot(-100)
