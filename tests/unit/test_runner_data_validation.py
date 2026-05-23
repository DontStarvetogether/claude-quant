"""单元测试：Web runner 交易标的数据质量硬校验。"""

from __future__ import annotations

import pytest

from web.runner import _build_universe_diagnostics, _validate_trade_symbol_data


def make_diag(
    status: str = "cache_hit",
    coverage_status: str = "ok",
    qfq_available: bool = True,
    used_cache: bool = True,
    warnings: list[str] | None = None,
) -> dict:
    quality_warnings = [] if qfq_available else ["qfq_missing"]
    if warnings is not None:
        quality_warnings = warnings
    return {
        "symbol": "600519.SH",
        "status": status,
        "used_cache": used_cache,
        "data_quality": {
            "coverage_status": coverage_status,
            "qfq_available": qfq_available,
            "warnings": quality_warnings,
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
        make_diag(warnings=["qfq_adjust_factor_missing"]),
        make_diag(warnings=["qfq_price_scale_mismatch"]),
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


def test_universe_diagnostics_marks_trend_rank_static_pool_high_risk():
    diagnostics = _build_universe_diagnostics(
        "trend_rank",
        [f"{i:06d}.SZ" for i in range(30)],
    )

    assert diagnostics["construction"] == "static"
    assert diagnostics["point_in_time"] is False
    assert diagnostics["history_membership_available"] is False
    assert diagnostics["survivorship_bias_risk"] == "high"
    assert "static_universe_survivorship_bias" in diagnostics["warnings"]


def test_universe_diagnostics_marks_small_custom_pool_low_risk():
    diagnostics = _build_universe_diagnostics(
        "double_ma",
        ["600519.SH", "000858.SZ"],
    )

    assert diagnostics["symbol_count"] == 2
    assert diagnostics["survivorship_bias_risk"] == "low"
    assert diagnostics["warnings"] == []


def test_universe_diagnostics_preserves_request_universe_metadata():
    diagnostics = _build_universe_diagnostics(
        "double_ma",
        ["600519.SH", "000858.SZ"],
        request={
            "universe": {
                "universe_id": "preset_bluechip",
                "universe_name": "蓝筹稳健",
                "source": "builtin_preset",
                "construction": "static",
            }
        },
    )

    assert diagnostics["universe_id"] == "preset_bluechip"
    assert diagnostics["universe_name"] == "蓝筹稳健"
    assert diagnostics["source"] == "builtin_preset"
    assert diagnostics["construction"] == "static"
