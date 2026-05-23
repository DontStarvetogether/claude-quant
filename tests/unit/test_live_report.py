from __future__ import annotations

import json

import pandas as pd

from cq.live import export_daily_trading_report, generate_daily_trading_report


def test_generate_daily_trading_report_summarizes_trades_equity_and_positions():
    report = generate_daily_trading_report(
        session_id="session-1",
        trade_date="2024-06-03",
        trades=[
            {
                "trade_date": "2024-06-03",
                "symbol": "600519.SH",
                "side": "BUY",
                "price": 100.0,
                "quantity": 1000,
                "amount": 100_000.0,
                "commission": 30.0,
                "stamp_tax": 0.0,
            },
            {
                "trade_date": "2024-06-03",
                "symbol": "000001.SZ",
                "side": "SELL",
                "price": 12.0,
                "quantity": 2000,
                "amount": 24_000.0,
                "commission": 7.2,
                "stamp_tax": 24.0,
            },
        ],
        equity_curve=[
            {"trade_date": "2024-06-03 09:30", "total_assets": 1_000_000.0, "cash": 1_000_000.0, "position_value": 0.0},
            {"trade_date": "2024-06-03 15:00", "total_assets": 1_012_000.0, "cash": 888_000.0, "position_value": 124_000.0},
        ],
        positions=[
            {
                "symbol": "600519.SH",
                "quantity": 1000,
                "last_price": 101.0,
                "market_value": 101_000.0,
                "unrealized_pnl": 1000.0,
            }
        ],
        alerts=["cash below target"],
    )

    assert report.summary["schema_version"] == "daily_trading_report.v1"
    assert report.summary["trade_count"] == 2
    assert report.summary["buy_count"] == 1
    assert report.summary["sell_count"] == 1
    assert report.summary["total_fees"] == 61.2
    assert report.summary["daily_pnl"] == 12_000.0
    assert "cash below target" in report.markdown
    assert "| 成交笔数 | 2 |" in report.markdown


def test_export_daily_trading_report_writes_standard_files(tmp_path):
    report = generate_daily_trading_report(
        session_id="session-1",
        trade_date="2024-06-03",
        trades=pd.DataFrame(
            [
                {
                    "trade_date": "2024-06-03",
                    "symbol": "600519.SH",
                    "side": "BUY",
                    "price": 100.0,
                    "quantity": 1000,
                    "amount": 100_000.0,
                    "commission": 30.0,
                    "stamp_tax": 0.0,
                }
            ]
        ),
        equity_curve=pd.DataFrame(
            [
                {"trade_date": "2024-06-03", "total_assets": 1_000_000.0, "cash": 900_000.0, "position_value": 100_000.0}
            ]
        ),
    )

    exported = export_daily_trading_report(report, tmp_path)

    assert set(exported.files) == {"report", "summary", "trades", "positions"}
    for path in exported.files.values():
        assert path.exists()
    payload = json.loads(exported.files["summary"].read_text(encoding="utf-8"))
    assert payload["session_id"] == "session-1"
    assert payload["trade_count"] == 1
    assert pd.read_csv(exported.files["trades"])["symbol"].iloc[0] == "600519.SH"
    assert exported.files["report"].read_text(encoding="utf-8").startswith("# 每日交易日报")
