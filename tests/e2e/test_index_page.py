"""
测试：主页（配置页面）的所有 UI 功能。
"""

import re
from datetime import date, timedelta

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import (
    BASE_URL,
    CACHED_NAME,
    CACHED_SYMBOL,
    QUICK_SYMBOLS_EXPECTED,
    click_quick_symbol,
    get_selected_symbols,
)


@pytest.fixture(autouse=True)
def goto_index(page: Page):
    page.goto(BASE_URL, wait_until="networkidle")


# ── 页面基础元素 ─────────────────────────────────────────────────────────────

class TestPageStructure:
    def test_title(self, page: Page):
        expect(page).to_have_title("Claude Quant — 量化回测平台")

    def test_header_brand(self, page: Page):
        expect(page.locator("header")).to_contain_text("CLAUDE QUANT")

    def test_strategy_loaded(self, page: Page):
        select = page.locator("#strategy-select")
        expect(select).to_be_visible()
        options = page.locator("#strategy-select option").all_text_contents()
        assert "双均线策略" in options, f"策略未加载: {options}"

    def test_strategy_desc_shown(self, page: Page):
        desc = page.locator("#strategy-desc")
        expect(desc).not_to_be_empty()

    def test_run_btn_enabled(self, page: Page):
        expect(page.locator("#run-btn")).to_be_enabled()


# ── 快速选择股票 ─────────────────────────────────────────────────────────────

class TestQuickSymbols:
    def test_quick_symbol_count(self, page: Page):
        """快选按钮数量应为 18 只。"""
        count = page.locator(".quick-symbol").count()
        assert count == len(QUICK_SYMBOLS_EXPECTED), f"期望 {len(QUICK_SYMBOLS_EXPECTED)} 个，实际 {count} 个"

    def test_all_expected_stocks_present(self, page: Page):
        """每只预定义股票都应有对应按钮。"""
        all_text = page.locator("#quick-symbol-container").text_content()
        for symbol, name in QUICK_SYMBOLS_EXPECTED:
            assert name in all_text, f"缺少快选按钮: {name} ({symbol})"

    def test_click_adds_tag(self, page: Page):
        """点击快选按钮后，已选区域出现对应 tag。"""
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        tags_text = page.locator("#symbol-tags").text_content()
        assert CACHED_SYMBOL in tags_text
        assert CACHED_NAME in tags_text

    def test_click_highlights_button(self, page: Page):
        """选中后按钮应有蓝色背景高亮 class（bg-blue-900/20）。"""
        btn = page.locator(f'.quick-symbol:has-text("{CACHED_NAME}")')
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        # 选中时 syncQuickButtonStates 添加 bg-blue-900/20
        expect(btn).to_have_class(re.compile(r"bg-blue-900"))

    def test_click_again_deselects(self, page: Page):
        """再次点击已选按钮，取消选中。"""
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        symbols = get_selected_symbols(page)
        assert CACHED_SYMBOL not in symbols

    def test_deselect_unhighlights_button(self, page: Page):
        """取消选中后按钮不再有蓝色背景高亮（bg-blue-900/20 被移除）。"""
        btn = page.locator(f'.quick-symbol:has-text("{CACHED_NAME}")')
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        expect(btn).not_to_have_class(re.compile(r"bg-blue-900"))

    def test_remove_tag_syncs_button_state(self, page: Page):
        """点击 tag 上的 × 移除后，对应快选按钮也应取消高亮（bg-blue-900/20 被移除）。"""
        click_quick_symbol(page, CACHED_NAME)
        page.wait_for_timeout(200)
        # 点击 tag 上的 × 按钮
        page.locator("#symbol-tags button").first.click()
        page.wait_for_timeout(200)
        btn = page.locator(f'.quick-symbol:has-text("{CACHED_NAME}")')
        expect(btn).not_to_have_class(re.compile(r"bg-blue-900"))

    def test_select_all(self, page: Page):
        """全选后 tag 数量应等于快选股票总数。"""
        page.locator("#select-all-btn").click()
        page.wait_for_timeout(300)
        symbols = get_selected_symbols(page)
        assert len(symbols) == len(QUICK_SYMBOLS_EXPECTED), f"全选后期望 {len(QUICK_SYMBOLS_EXPECTED)} 只，实际 {len(symbols)} 只"

    def test_select_all_highlights_all_buttons(self, page: Page):
        """全选后所有快选按钮都应有蓝色背景高亮。"""
        page.locator("#select-all-btn").click()
        page.wait_for_timeout(300)
        btns = page.locator(".quick-symbol").all()
        for btn in btns:
            expect(btn).to_have_class(re.compile(r"bg-blue-900"))

    def test_clear_all(self, page: Page):
        """全选后点击清空，tag 应全部消失。"""
        page.locator("#select-all-btn").click()
        page.wait_for_timeout(200)
        page.locator("#clear-all-btn").click()
        page.wait_for_timeout(200)
        symbols = get_selected_symbols(page)
        assert len(symbols) == 0

    def test_clear_all_unhighlights_buttons(self, page: Page):
        """清空后所有快选按钮不应有蓝色背景高亮。"""
        page.locator("#select-all-btn").click()
        page.wait_for_timeout(200)
        page.locator("#clear-all-btn").click()
        page.wait_for_timeout(200)
        btns = page.locator(".quick-symbol").all()
        for btn in btns:
            expect(btn).not_to_have_class(re.compile(r"bg-blue-900"))


# ── 手动输入股票代码 ─────────────────────────────────────────────────────────

class TestSymbolInput:
    def test_valid_symbol_added(self, page: Page):
        page.fill("#symbol-input", "600519.SH")
        page.locator("#add-symbol-btn").click()
        page.wait_for_timeout(200)
        assert "600519.SH" in get_selected_symbols(page)

    def test_enter_key_adds_symbol(self, page: Page):
        page.fill("#symbol-input", "000858.SZ")
        page.press("#symbol-input", "Enter")
        page.wait_for_timeout(200)
        assert "000858.SZ" in get_selected_symbols(page)

    def test_invalid_format_shows_error(self, page: Page):
        page.fill("#symbol-input", "INVALID")
        page.locator("#add-symbol-btn").click()
        page.wait_for_timeout(200)
        expect(page.locator("#form-error")).to_be_visible()
        expect(page.locator("#form-error")).to_contain_text("格式不正确")

    def test_input_cleared_after_add(self, page: Page):
        page.fill("#symbol-input", "600519.SH")
        page.locator("#add-symbol-btn").click()
        page.wait_for_timeout(200)
        assert page.input_value("#symbol-input") == ""

    def test_duplicate_not_added_twice(self, page: Page):
        page.fill("#symbol-input", "600519.SH")
        page.locator("#add-symbol-btn").click()
        page.wait_for_timeout(200)
        page.fill("#symbol-input", "600519.SH")
        page.locator("#add-symbol-btn").click()
        page.wait_for_timeout(200)
        symbols = get_selected_symbols(page)
        assert symbols.count("600519.SH") == 1


# ── 日期默认值 ───────────────────────────────────────────────────────────────

class TestDateDefaults:
    def test_end_date_is_today(self, page: Page):
        end_date = page.input_value("#end-date")
        today = date.today().isoformat()
        assert end_date == today, f"结束日期应为今天 {today}，实际 {end_date}"

    def test_start_date_is_3_years_ago(self, page: Page):
        start_date = page.input_value("#start-date")
        today = date.today()
        three_years_ago = date(today.year - 3, today.month, today.day).isoformat()
        assert start_date == three_years_ago, f"开始日期应为 {three_years_ago}，实际 {start_date}"


# ── 表单验证 ─────────────────────────────────────────────────────────────────

class TestFormValidation:
    def test_submit_without_symbol_shows_error(self, page: Page):
        page.locator("#run-btn").click()
        page.wait_for_timeout(300)
        expect(page.locator("#form-error")).to_be_visible()
        expect(page.locator("#form-error")).to_contain_text("至少添加一只")

    def test_risk_slider_max_pos_label_updates(self, page: Page):
        """风控滑块在折叠面板内，需先展开 <details>。"""
        page.locator("details summary").click()
        page.wait_for_timeout(200)
        page.locator("#max-pos-pct").fill("50")
        page.dispatch_event("#max-pos-pct", "input")
        page.wait_for_timeout(100)
        expect(page.locator("#max-pos-label")).to_have_text("50%")

    def test_risk_slider_min_cash_label_updates(self, page: Page):
        """风控滑块在折叠面板内，需先展开 <details>。"""
        page.locator("details summary").click()
        page.wait_for_timeout(200)
        page.locator("#min-cash-reserve").fill("10")
        page.dispatch_event("#min-cash-reserve", "input")
        page.wait_for_timeout(100)
        expect(page.locator("#min-cash-label")).to_have_text("10%")

    def test_strategy_params_rendered(self, page: Page):
        """选中双均线策略后应渲染快线/慢线周期参数。"""
        param_container = page.locator("#strategy-params")
        expect(param_container).to_contain_text("快线周期")
        expect(param_container).to_contain_text("慢线周期")
