"""单元测试：Web runner 交易标的数据质量硬校验。"""

from __future__ import annotations

import pytest

from web.runner import _validate_trade_symbol_data


def make_diag(
    status: str = "cache_hit",
    coverage_status: str = "ok",
    qfq_available: bool = True,
    used_cache: bool = True,
) -> dict:
    return {
        "symbol": "600519.SH",
        "status": status,
        "used_cache": used_cache,
        "data_quality": {
            "coverage_status": coverage_status,
            "qfq_available": qfq_available,
            "warnings": [] if qfq_available else ["qfq_missing"],
        },
    }


def test_validate_trade_symbol_data_passes_ok_data():
    _validate_trade_symbol_data(
        [make_diag()],
        start_date="2024-01-02",
        end_date="2024-01-05",
    )


@pytest.mark.parametrize(
    "diagnostic",
    [
        make_diag(status="download_failed_no_cache", used_cache=False),
        make_diag(status="empty_source", used_cache=False),
        make_diag(coverage_status="start_missing"),
        make_diag(coverage_status="end_missing"),
        make_diag(qfq_available=False),
    ],
)
def test_validate_trade_symbol_data_blocks_bad_trade_data(diagnostic):
    with pytest.raises(RuntimeError, match="交易标的数据质量校验失败"):
        _validate_trade_symbol_data(
            [diagnostic],
            start_date="2024-01-02",
            end_date="2024-01-05",
        )


def test_validate_trade_symbol_data_allows_pre_listing_gap():
    _validate_trade_symbol_data(
        [make_diag(coverage_status="pre_listing_gap")],
        start_date="2024-01-02",
        end_date="2024-01-05",
    )
