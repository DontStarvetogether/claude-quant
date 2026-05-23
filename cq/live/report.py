"""Daily trading report helpers for paper/live sessions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "daily_trading_report.v1"


@dataclass(frozen=True)
class DailyTradingReport:
    """Rendered daily report and machine-readable summary."""

    session_id: str
    trade_date: str
    summary: dict[str, Any]
    markdown: str
    trades: pd.DataFrame
    positions: pd.DataFrame


@dataclass(frozen=True)
class DailyTradingReportExport:
    """Paths written by daily trading report export."""

    output_dir: Path
    files: dict[str, Path]
    summary: dict[str, Any]


def generate_daily_trading_report(
    *,
    session_id: str,
    trade_date: str,
    trades: pd.DataFrame | Sequence[Mapping[str, Any]],
    equity_curve: pd.DataFrame | Sequence[Mapping[str, Any]],
    positions: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    alerts: Sequence[str] | None = None,
) -> DailyTradingReport:
    """Generate a daily paper/live trading report."""

    trades_df = _normalize_trades(_to_frame(trades))
    equity_df = _normalize_equity(_to_frame(equity_curve))
    positions_df = _normalize_positions(_to_frame([] if positions is None else positions))

    summary = _build_summary(
        session_id=session_id,
        trade_date=trade_date,
        trades=trades_df,
        equity=equity_df,
        positions=positions_df,
        alerts=list(alerts or []),
    )
    markdown = _render_report(summary, trades_df, positions_df)
    return DailyTradingReport(
        session_id=session_id,
        trade_date=trade_date,
        summary=summary,
        markdown=markdown,
        trades=trades_df,
        positions=positions_df,
    )


def export_daily_trading_report(
    report: DailyTradingReport,
    output_dir: str | Path,
) -> DailyTradingReportExport:
    """Export report markdown, summary JSON, and source tables."""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    report_path = out / "daily_report.md"
    report_path.write_text(report.markdown, encoding="utf-8")
    files["report"] = report_path

    summary_path = out / "daily_summary.json"
    summary_path.write_text(
        json.dumps(_json_safe(report.summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files["summary"] = summary_path

    trades_path = out / "trades.csv"
    report.trades.to_csv(trades_path, index=False)
    files["trades"] = trades_path

    positions_path = out / "positions.csv"
    report.positions.to_csv(positions_path, index=False)
    files["positions"] = positions_path

    return DailyTradingReportExport(output_dir=out, files=files, summary=report.summary)


def _build_summary(
    *,
    session_id: str,
    trade_date: str,
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    positions: pd.DataFrame,
    alerts: list[str],
) -> dict[str, Any]:
    buy_mask = trades["side"] == "BUY" if "side" in trades.columns else pd.Series(dtype=bool)
    sell_mask = trades["side"] == "SELL" if "side" in trades.columns else pd.Series(dtype=bool)
    start_assets = float(equity["total_assets"].iloc[0]) if not equity.empty else 0.0
    final_assets = float(equity["total_assets"].iloc[-1]) if not equity.empty else 0.0
    final_cash = float(equity["cash"].iloc[-1]) if not equity.empty and "cash" in equity.columns else 0.0
    final_position_value = (
        float(equity["position_value"].iloc[-1])
        if not equity.empty and "position_value" in equity.columns
        else 0.0
    )
    total_commission = _sum_column(trades, "commission")
    total_stamp_tax = _sum_column(trades, "stamp_tax")
    total_amount = _sum_column(trades, "amount")
    pnl = final_assets - start_assets if start_assets else 0.0
    pnl_pct = pnl / start_assets if start_assets else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "trade_date": trade_date,
        "trade_count": int(len(trades)),
        "buy_count": int(buy_mask.sum()) if not buy_mask.empty else 0,
        "sell_count": int(sell_mask.sum()) if not sell_mask.empty else 0,
        "symbol_count": int(trades["symbol"].nunique()) if "symbol" in trades.columns else 0,
        "position_count": int(positions["symbol"].nunique()) if "symbol" in positions.columns else 0,
        "total_trade_amount": round(total_amount, 2),
        "total_commission": round(total_commission, 2),
        "total_stamp_tax": round(total_stamp_tax, 2),
        "total_fees": round(total_commission + total_stamp_tax, 2),
        "start_assets": round(start_assets, 2),
        "final_assets": round(final_assets, 2),
        "final_cash": round(final_cash, 2),
        "final_position_value": round(final_position_value, 2),
        "daily_pnl": round(pnl, 2),
        "daily_pnl_pct": round(pnl_pct, 6),
        "alerts": alerts,
    }


def _render_report(summary: Mapping[str, Any], trades: pd.DataFrame, positions: pd.DataFrame) -> str:
    lines = [
        f"# 每日交易日报：{summary['trade_date']}",
        "",
        "## 概览",
        "",
        "| 项目 | 值 |",
        "|---|---:|",
        f"| Session | {summary['session_id']} |",
        f"| 成交笔数 | {summary['trade_count']} |",
        f"| 买入 / 卖出 | {summary['buy_count']} / {summary['sell_count']} |",
        f"| 成交股票数 | {summary['symbol_count']} |",
        f"| 期末持仓数 | {summary['position_count']} |",
        f"| 成交额 | {summary['total_trade_amount']:.2f} |",
        f"| 总费用 | {summary['total_fees']:.2f} |",
        f"| 期末总资产 | {summary['final_assets']:.2f} |",
        f"| 日盈亏 | {summary['daily_pnl']:.2f} |",
        f"| 日收益率 | {summary['daily_pnl_pct']:.2%} |",
    ]
    alerts = summary.get("alerts", [])
    lines.extend(["", "## 风险提示", ""])
    if alerts:
        lines.extend([f"- {alert}" for alert in alerts])
    else:
        lines.append("无。")
    lines.extend(_sample_table_section("成交样例", trades))
    lines.extend(_sample_table_section("期末持仓", positions))
    return "\n".join(lines).rstrip() + "\n"


def _sample_table_section(title: str, frame: pd.DataFrame) -> list[str]:
    lines = ["", f"## {title}", ""]
    if frame.empty:
        lines.append("无。")
        return lines
    lines.append(_frame_to_markdown(frame.head(10)))
    return lines


def _normalize_trades(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["trade_date", "symbol", "side", "price", "quantity", "amount", "commission", "stamp_tax"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    data = df.copy()
    if "trade_date" not in data.columns and "date" in data.columns:
        data = data.rename(columns={"date": "trade_date"})
    for col in columns:
        if col not in data.columns:
            data[col] = 0.0 if col in {"price", "quantity", "amount", "commission", "stamp_tax"} else ""
    data["trade_date"] = data["trade_date"].astype(str)
    data["symbol"] = data["symbol"].astype(str).str.upper()
    data["side"] = data["side"].astype(str).str.upper()
    for col in ["price", "quantity", "amount", "commission", "stamp_tax"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
    return data[columns].sort_values(["trade_date", "symbol", "side"]).reset_index(drop=True)


def _normalize_equity(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["trade_date", "total_assets", "cash", "position_value"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    data = df.copy()
    if "trade_date" not in data.columns and "date" in data.columns:
        data = data.rename(columns={"date": "trade_date"})
    for col in columns:
        if col not in data.columns:
            data[col] = 0.0 if col != "trade_date" else ""
    for col in ["total_assets", "cash", "position_value"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
    data["trade_date"] = data["trade_date"].astype(str)
    return data[columns].sort_values("trade_date").reset_index(drop=True)


def _normalize_positions(df: pd.DataFrame) -> pd.DataFrame:
    columns = ["symbol", "quantity", "last_price", "market_value", "unrealized_pnl"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    data = df.copy()
    for col in columns:
        if col not in data.columns:
            data[col] = 0.0 if col != "symbol" else ""
    data["symbol"] = data["symbol"].astype(str).str.upper()
    for col in ["quantity", "last_price", "market_value", "unrealized_pnl"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
    return data[columns].sort_values("symbol").reset_index(drop=True)


def _sum_column(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0.0).sum())


def _to_frame(value: pd.DataFrame | Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    return pd.DataFrame(list(value))


def _frame_to_markdown(frame: pd.DataFrame) -> str:
    columns = [str(col) for col in frame.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.astype(object).where(pd.notna(frame), "").to_dict("records"):
        lines.append("| " + " | ".join(str(row[col]) for col in frame.columns) + " |")
    return "\n".join(lines)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    return value
