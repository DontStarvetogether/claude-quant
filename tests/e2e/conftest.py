"""
E2E 测试配置。

前提：Web 服务已在 http://127.0.0.1:8888 运行。
  python run_web.py
"""

import pytest
from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8888"

# 有本地缓存的股票（快速，不需网络）
CACHED_SYMBOL = "600519.SH"
CACHED_NAME = "茅台"

# 快速选择列表中的股票
QUICK_SYMBOLS_EXPECTED = [
    ("600519.SH", "茅台"),
    ("000858.SZ", "五粮液"),
    ("000001.SZ", "平安银行"),
    ("600036.SH", "招商银行"),
    ("300750.SZ", "宁德时代"),
    ("601318.SH", "中国平安"),
    ("000333.SZ", "美的集团"),
    ("600276.SH", "恒瑞医药"),
    ("002415.SZ", "海康威视"),
    ("601888.SH", "中国中免"),
    ("600887.SH", "伊利股份"),
    ("000651.SZ", "格力电器"),
    ("601398.SH", "工商银行"),
    ("000002.SZ", "万科A"),
    ("300015.SZ", "爱尔眼科"),
    ("002594.SZ", "比亚迪"),
    ("600900.SH", "长江电力"),
    ("601166.SH", "兴业银行"),
]


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "base_url": BASE_URL}


def click_quick_symbol(page: Page, name: str) -> None:
    """点击名称匹配的快选按钮。"""
    page.locator(f'.quick-symbol:has-text("{name}")').click()


def get_selected_symbols(page: Page) -> list[str]:
    """返回已选 tag 中的代码列表。"""
    tags = page.locator("#symbol-tags span").all_text_contents()
    return [t.strip().split("·")[0].strip().split(" ")[0].strip() for t in tags if t.strip()]


def wait_for_backtest(page: Page, timeout_ms: int = 120_000) -> str:
    """等待回测完成并返回 run_id，或在出错时抛出异常。"""
    page.wait_for_url("**/result.html**", timeout=timeout_ms)
    url = page.url
    return url.split("run_id=")[-1]
