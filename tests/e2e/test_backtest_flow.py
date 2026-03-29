"""
测试：回测提交 → SSE 进度 → 结果页完整流程。

使用本地已缓存的股票（600519.SH 茅台），不依赖网络下载。
"""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    BASE_URL,
    CACHED_NAME,
    CACHED_SYMBOL,
    click_quick_symbol,
    wait_for_backtest,
)


@pytest.fixture
def page_with_symbol(page: Page):
    """打开主页并选好茅台，ready to submit。"""
    page.goto(BASE_URL, wait_until="networkidle")
    click_quick_symbol(page, CACHED_NAME)
    page.wait_for_timeout(200)
    return page


# ── 提交与进度 ───────────────────────────────────────────────────────────────

class TestBacktestSubmission:
    def test_submit_shows_status_card(self, page_with_symbol: Page):
        """提交后状态卡片应立即出现。"""
        page_with_symbol.locator("#run-btn").click()
        expect(page_with_symbol.locator("#run-status-card")).to_be_visible(timeout=3000)

    def test_submit_disables_run_btn(self, page_with_symbol: Page):
        """提交后运行按钮应禁用。"""
        page_with_symbol.locator("#run-btn").click()
        page_with_symbol.wait_for_timeout(300)
        expect(page_with_symbol.locator("#run-btn")).to_be_disabled()

    def test_status_card_shows_strategy_name(self, page_with_symbol: Page):
        """状态卡片应显示策略 ID。"""
        page_with_symbol.locator("#run-btn").click()
        page_with_symbol.wait_for_timeout(500)
        expect(page_with_symbol.locator("#status-strategy")).not_to_be_empty()

    def test_progress_bar_advances(self, page_with_symbol: Page):
        """回测运行中进度条宽度应超过 0%。"""
        page_with_symbol.locator("#run-btn").click()
        # 等待进度 > 0
        page_with_symbol.wait_for_function(
            "() => parseInt(document.getElementById('progress-text').textContent) > 0",
            timeout=60000,
        )
        progress_text = page_with_symbol.locator("#progress-text").text_content()
        pct = int(progress_text.replace("%", "").strip())
        assert pct > 0, f"进度条应前进，实际 {pct}%"

    def test_current_date_updates(self, page_with_symbol: Page):
        """current_date 文本应在回测中更新（显示日期或下载状态）。"""
        page_with_symbol.locator("#run-btn").click()
        page_with_symbol.wait_for_function(
            "() => document.getElementById('current-date-text').textContent.trim() !== ''",
            timeout=60000,
        )
        date_text = page_with_symbol.locator("#current-date-text").text_content().strip()
        assert date_text != "", "current_date 应有内容"

    def test_total_assets_updates(self, page_with_symbol: Page):
        """总资产显示应在回测运行时更新（不为 —）。"""
        page_with_symbol.locator("#run-btn").click()
        page_with_symbol.wait_for_function(
            "() => document.getElementById('status-assets').textContent !== '—'",
            timeout=60000,
        )
        assets_text = page_with_symbol.locator("#status-assets").text_content()
        assert assets_text != "—", "总资产应有数值"


# ── 结果页跳转 ───────────────────────────────────────────────────────────────

class TestBacktestResult:
    @pytest.fixture(autouse=True)
    def run_backtest(self, page_with_symbol: Page):
        """在每个测试前运行回测并等待跳转结果页。"""
        self.page = page_with_symbol
        page_with_symbol.locator("#run-btn").click()
        wait_for_backtest(page_with_symbol, timeout_ms=120_000)

    def test_redirected_to_result_page(self):
        assert "result.html" in self.page.url

    def test_result_page_shows_main_content(self):
        expect(self.page.locator("#main-content")).to_be_visible()
        expect(self.page.locator("#loading")).to_be_hidden()

    def test_metrics_total_return_displayed(self):
        val = self.page.locator("#m-total-return").text_content()
        assert val not in ("—", ""), f"总收益率应有值，实际: {val}"
        assert "%" in val

    def test_metrics_total_trades_positive(self):
        val = self.page.locator("#m-total-trades").text_content()
        num = int(val.replace("笔", "").strip())
        assert num > 0, f"应有成交记录，实际: {num}"

    def test_equity_chart_rendered(self):
        """图表容器应有实际高度（ECharts 已渲染）。"""
        box = self.page.locator("#equity-chart").bounding_box()
        assert box and box["height"] > 100, "权益曲线图未渲染"

    def test_trades_table_has_rows(self):
        rows = self.page.locator("#trades-tbody tr").count()
        assert rows > 0, "成交记录表格应有行"

    def test_trade_symbol_shown(self):
        """成交记录第一行应显示股票代码。"""
        first_row = self.page.locator("#trades-tbody tr").first
        expect(first_row).to_contain_text(CACHED_SYMBOL)

    def test_trade_company_name_shown(self):
        """成交记录应显示公司名称（茅台）。"""
        first_row = self.page.locator("#trades-tbody tr").first
        expect(first_row).to_contain_text(CACHED_NAME)

    def test_trades_summary_shown(self):
        summary = self.page.locator("#trades-summary").text_content()
        assert "笔成交" in summary

    def test_header_shows_symbol_and_name(self):
        """Header 股票信息应同时显示代码和公司名。"""
        header_symbols = self.page.locator("#header-symbols").text_content()
        assert CACHED_SYMBOL in header_symbols
        assert CACHED_NAME in header_symbols

    def test_detail_metrics_rendered(self):
        """详细指标区域应有多行内容。"""
        rows = self.page.locator("#detail-metrics .metric-row").count()
        assert rows >= 10, f"详细指标应 ≥ 10 行，实际 {rows}"

    def test_back_link_works(self):
        """← 返回 链接应回到主页。"""
        self.page.locator("a:has-text('← 返回')").click()
        self.page.wait_for_url("**/", timeout=5000)
        assert self.page.url.rstrip("/") == BASE_URL.rstrip("/")


# ── 历史记录 ─────────────────────────────────────────────────────────────────

class TestHistory:
    def test_history_entry_after_backtest(self, page_with_symbol: Page):
        """完成回测后，历史记录应出现对应条目。"""
        page_with_symbol.locator("#run-btn").click()
        wait_for_backtest(page_with_symbol, timeout_ms=120_000)

        # 返回主页
        page_with_symbol.goto(BASE_URL, wait_until="networkidle")
        # 历史可能有多条，用 first 避免 strict mode 错误
        expect(page_with_symbol.locator("#history-list .history-card").first).to_be_visible()

    def test_history_shows_return(self, page_with_symbol: Page):
        """历史记录卡片应显示总收益率。"""
        page_with_symbol.locator("#run-btn").click()
        run_id = None
        page_with_symbol.wait_for_url("**/result.html**", timeout=120_000)
        run_id = page_with_symbol.url.split("run_id=")[-1]
        page_with_symbol.goto(BASE_URL, wait_until="networkidle")

        # 找到本次回测对应的卡片（包含 run_id）
        card = page_with_symbol.locator(f"#history-list .history-card[onclick*='{run_id}']")
        text = card.text_content()
        assert "%" in text, f"历史记录应显示收益率，实际: {text}"

    def test_history_card_click_opens_result(self, page_with_symbol: Page):
        """点击历史记录卡片应跳转到结果页。"""
        page_with_symbol.locator("#run-btn").click()
        page_with_symbol.wait_for_url("**/result.html**", timeout=120_000)
        run_id = page_with_symbol.url.split("run_id=")[-1]
        page_with_symbol.goto(BASE_URL, wait_until="networkidle")

        card = page_with_symbol.locator(f"#history-list .history-card[onclick*='{run_id}']")
        card.click()
        page_with_symbol.wait_for_url("**/result.html**", timeout=5000)
        assert "result.html" in page_with_symbol.url
