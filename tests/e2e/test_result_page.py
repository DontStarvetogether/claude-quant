"""
测试：结果页 — 图表、指标、导出 CSV。
直接使用已完成的 run_id，不需要重跑回测。
"""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import BASE_URL, CACHED_NAME, CACHED_SYMBOL, click_quick_symbol, wait_for_backtest


@pytest.fixture(scope="module")
def completed_run_id(browser):
    """模块级 fixture：运行一次回测，得到 run_id 供所有测试复用。"""
    context = browser.new_context(base_url=BASE_URL)
    page = context.new_page()
    page.goto(BASE_URL, wait_until="networkidle")
    click_quick_symbol(page, CACHED_NAME)
    page.wait_for_timeout(200)
    page.locator("#run-btn").click()
    wait_for_backtest(page, timeout_ms=120_000)
    run_id = page.url.split("run_id=")[-1]
    context.close()
    return run_id


@pytest.fixture
def result_page(page: Page, completed_run_id: str):
    page.goto(f"{BASE_URL}/result.html?run_id={completed_run_id}", wait_until="networkidle")
    page.wait_for_selector("#main-content:not(.hidden)", timeout=10000)
    return page


# ── 指标区域 ─────────────────────────────────────────────────────────────────

class TestMetrics:
    METRIC_IDS = [
        "m-total-return",
        "m-annual-return",
        "m-max-drawdown",
        "m-sharpe",
        "m-win-rate",
        "m-total-trades",
        "m-final-value",
        "m-total-fees",
    ]

    def test_all_metric_cards_have_values(self, result_page: Page):
        for mid in self.METRIC_IDS:
            val = result_page.locator(f"#{mid}").text_content().strip()
            assert val not in ("—", ""), f"指标 #{mid} 无值"

    def test_total_return_is_percentage(self, result_page: Page):
        val = result_page.locator("#m-total-return").text_content()
        assert "%" in val

    def test_max_drawdown_is_negative(self, result_page: Page):
        val = result_page.locator("#m-max-drawdown").text_content()
        assert val.startswith("-"), f"最大回撤应为负值: {val}"

    def test_total_trades_is_integer(self, result_page: Page):
        val = result_page.locator("#m-total-trades").text_content()
        n = int(val.replace("笔", "").strip())
        assert n >= 0

    def test_detail_metrics_sharpe(self, result_page: Page):
        rows = result_page.locator("#detail-metrics .metric-row").all()
        labels = [r.locator(".metric-row-label").text_content() for r in rows]
        assert "夏普比率" in labels

    def test_detail_metrics_win_rate(self, result_page: Page):
        rows = result_page.locator("#detail-metrics .metric-row").all()
        labels = [r.locator(".metric-row-label").text_content() for r in rows]
        assert "胜率" in labels


# ── 权益曲线 ─────────────────────────────────────────────────────────────────

class TestEquityChart:
    def test_chart_container_visible(self, result_page: Page):
        expect(result_page.locator("#equity-chart")).to_be_visible()

    def test_chart_has_canvas(self, result_page: Page):
        """ECharts 渲染后应产生 canvas 元素。"""
        canvas_count = result_page.locator("#equity-chart canvas").count()
        assert canvas_count > 0, "ECharts 未生成 canvas"

    def test_chart_height(self, result_page: Page):
        box = result_page.locator("#equity-chart").bounding_box()
        assert box and box["height"] >= 300, f"图表高度不足: {box}"


# ── 成交记录 ─────────────────────────────────────────────────────────────────

class TestTradesTable:
    def test_table_has_data(self, result_page: Page):
        rows = result_page.locator("#trades-tbody tr").count()
        assert rows > 0

    def test_symbol_and_name_in_each_row(self, result_page: Page):
        """每笔成交都应显示股票代码和公司名。"""
        rows = result_page.locator("#trades-tbody tr").all()
        for row in rows:
            text = row.text_content()
            assert CACHED_SYMBOL in text, f"行缺少代码: {text[:100]}"
            assert CACHED_NAME in text, f"行缺少公司名: {text[:100]}"

    def test_side_column_is_buy_or_sell(self, result_page: Page):
        rows = result_page.locator("#trades-tbody tr").all()
        for row in rows:
            text = row.text_content()
            assert "买入" in text or "卖出" in text, f"方向列异常: {text[:80]}"

    def test_summary_text(self, result_page: Page):
        summary = result_page.locator("#trades-summary").text_content()
        assert "买入" in summary and "卖出" in summary

    def test_buy_sell_counts_add_up(self, result_page: Page):
        summary = result_page.locator("#trades-summary").text_content()
        import re
        total = int(re.search(r"共\s*(\d+)\s*笔", summary).group(1))
        buy = int(re.search(r"买入\s*(\d+)\s*笔", summary).group(1))
        sell = int(re.search(r"卖出\s*(\d+)\s*笔", summary).group(1))
        assert buy + sell == total


# ── 导出 CSV ─────────────────────────────────────────────────────────────────

class TestExportCsv:
    def test_export_btn_visible(self, result_page: Page):
        expect(result_page.locator("#export-csv-btn")).to_be_visible()

    def test_export_triggers_download(self, result_page: Page):
        with result_page.expect_download(timeout=5000) as dl:
            result_page.locator("#export-csv-btn").click()
        download = dl.value
        assert download.suggested_filename.endswith(".csv")

    def test_csv_contains_company_name(self, result_page: Page):
        """导出的 CSV 应包含公司名称列。"""
        with result_page.expect_download(timeout=5000) as dl:
            result_page.locator("#export-csv-btn").click()
        download = dl.value
        path = download.path()
        content = open(path, encoding="utf-8-sig").read()
        assert "公司名称" in content
        assert CACHED_NAME in content
