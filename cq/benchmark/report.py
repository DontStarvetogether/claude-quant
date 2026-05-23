"""Benchmark report and export helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from cq.benchmark.momentum_topn import BenchmarkResult

TRADING_DAYS = 252
SCHEMA_VERSION = "benchmark.v1"


@dataclass(frozen=True)
class BenchmarkSummary:
    """Compact benchmark metrics for reports and cross-platform checks."""

    start_date: str | None
    end_date: str | None
    trading_days: int
    initial_assets: float
    final_assets: float
    total_return: float
    annual_return: float
    max_drawdown: float
    trade_count: int
    buy_count: int
    sell_count: int
    total_trade_amount: float
    total_commission: float
    total_stamp_tax: float
    total_fees: float
    final_cash: float
    final_position_value: float
    avg_position_count: float
    avg_cash_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkReport:
    """Rendered benchmark Markdown report."""

    markdown: str
    summary: BenchmarkSummary


@dataclass(frozen=True)
class BenchmarkExport:
    """Paths written by benchmark export."""

    output_dir: Path
    files: dict[str, Path]
    summary: BenchmarkSummary


def summarize_benchmark_result(result: BenchmarkResult) -> BenchmarkSummary:
    """Summarize benchmark result frames into stable metrics."""
    equity = _sorted_frame(result.equity_curve, "date")
    trades = result.trades.copy()
    holdings = result.holdings.copy()

    if equity.empty:
        return BenchmarkSummary(
            start_date=None,
            end_date=None,
            trading_days=0,
            initial_assets=0.0,
            final_assets=0.0,
            total_return=0.0,
            annual_return=0.0,
            max_drawdown=0.0,
            trade_count=0,
            buy_count=0,
            sell_count=0,
            total_trade_amount=0.0,
            total_commission=0.0,
            total_stamp_tax=0.0,
            total_fees=0.0,
            final_cash=0.0,
            final_position_value=0.0,
            avg_position_count=0.0,
            avg_cash_ratio=0.0,
        )

    assets = pd.to_numeric(equity["total_assets"], errors="coerce").fillna(0.0)
    initial_assets = float(assets.iloc[0])
    final_assets = float(assets.iloc[-1])
    total_return = final_assets / initial_assets - 1.0 if initial_assets > 0 else 0.0
    periods = max(len(equity) - 1, 0)
    annual_return = (
        (final_assets / initial_assets) ** (TRADING_DAYS / periods) - 1.0
        if periods > 0 and initial_assets > 0 and final_assets > 0
        else 0.0
    )
    rolling_max = assets.cummax().replace(0, pd.NA)
    drawdown = (assets / rolling_max - 1.0).fillna(0.0)

    trade_amount = (
        pd.to_numeric(trades["amount"], errors="coerce").fillna(0.0)
        if "amount" in trades.columns
        else pd.Series(dtype="float64")
    )
    side = trades["side"].astype(str) if "side" in trades.columns else pd.Series(dtype="object")
    total_commission = _sum_column(trades, "commission")
    total_stamp_tax = _sum_column(trades, "stamp_tax")
    final_cash = float(pd.to_numeric(equity["cash"], errors="coerce").fillna(0.0).iloc[-1])
    final_position_value = float(
        pd.to_numeric(equity["position_value"], errors="coerce").fillna(0.0).iloc[-1]
    )
    avg_cash_ratio = float(
        (pd.to_numeric(equity["cash"], errors="coerce").fillna(0.0) / assets.replace(0, pd.NA))
        .fillna(0.0)
        .mean()
    )
    avg_position_count = (
        float(holdings.groupby("date")["symbol"].nunique().mean())
        if not holdings.empty and {"date", "symbol"}.issubset(holdings.columns)
        else 0.0
    )

    return BenchmarkSummary(
        start_date=_date_str(equity["date"].iloc[0]),
        end_date=_date_str(equity["date"].iloc[-1]),
        trading_days=len(equity),
        initial_assets=round(initial_assets, 2),
        final_assets=round(final_assets, 2),
        total_return=round(total_return, 6),
        annual_return=round(float(annual_return), 6),
        max_drawdown=round(float(drawdown.min()), 6),
        trade_count=int(len(trades)),
        buy_count=int((side == "BUY").sum()) if not side.empty else 0,
        sell_count=int((side == "SELL").sum()) if not side.empty else 0,
        total_trade_amount=round(float(trade_amount.sum()), 2),
        total_commission=round(total_commission, 2),
        total_stamp_tax=round(total_stamp_tax, 2),
        total_fees=round(total_commission + total_stamp_tax, 2),
        final_cash=round(final_cash, 2),
        final_position_value=round(final_position_value, 2),
        avg_position_count=round(avg_position_count, 4),
        avg_cash_ratio=round(avg_cash_ratio, 6),
    )


def generate_benchmark_report(
    result: BenchmarkResult,
    *,
    name: str = "20日动量 TopN Benchmark",
    universe: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    exported_files: Mapping[str, str | Path] | None = None,
) -> BenchmarkReport:
    """Generate a Markdown report for a benchmark result."""
    summary = summarize_benchmark_result(result)
    lines = [f"# Benchmark 报告：{name}", ""]
    lines.extend(_scope_section(summary, universe=universe, metadata=metadata))
    lines.extend(_metric_section(summary))
    lines.extend(_trade_section(summary))
    lines.extend(_schema_section())
    if exported_files:
        lines.extend(_export_section(exported_files))
    return BenchmarkReport(markdown="\n".join(lines).rstrip() + "\n", summary=summary)


def export_benchmark_result(
    result: BenchmarkResult,
    output_dir: str | Path,
    *,
    name: str = "20日动量 TopN Benchmark",
    universe: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    include_report: bool = True,
) -> BenchmarkExport:
    """Export benchmark frames, summary JSON, and an optional Markdown report."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: dict[str, Path] = {}
    for key, frame in {
        "equity_curve": result.equity_curve,
        "holdings": result.holdings,
        "trades": result.trades,
        "signals": result.signals,
    }.items():
        path = out / f"{key}.csv"
        _frame_for_export(frame).to_csv(path, index=False)
        files[key] = path

    summary = summarize_benchmark_result(result)
    summary_payload = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "universe": universe,
        "metadata": dict(metadata or {}),
        "summary": summary.to_dict(),
        "backtest_field_mapping": _backtest_field_mapping(),
        "files": {key: path.name for key, path in files.items()},
    }
    summary_path = out / "summary.json"
    summary_path.write_text(
        json.dumps(_json_safe(summary_payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files["summary"] = summary_path

    if include_report:
        report = generate_benchmark_report(
            result,
            name=name,
            universe=universe,
            metadata=metadata,
            exported_files={key: path.name for key, path in files.items()},
        )
        report_path = out / "report.md"
        report_path.write_text(report.markdown, encoding="utf-8")
        files["report"] = report_path

    return BenchmarkExport(output_dir=out, files=files, summary=summary)


def _scope_section(
    summary: BenchmarkSummary,
    *,
    universe: str | None,
    metadata: Mapping[str, Any] | None,
) -> list[str]:
    rows = [
        ["股票池", universe or "—"],
        ["测试区间", _date_range(summary.start_date, summary.end_date)],
        ["交易日数", str(summary.trading_days)],
    ]
    if metadata:
        rows.extend([[str(key), str(value)] for key, value in metadata.items()])
    return ["## 测试范围", "", _markdown_table(["项目", "值"], rows), ""]


def _metric_section(summary: BenchmarkSummary) -> list[str]:
    rows = [
        ["初始资产", _fmt_money(summary.initial_assets)],
        ["期末资产", _fmt_money(summary.final_assets)],
        ["总收益", _fmt_pct(summary.total_return)],
        ["年化收益", _fmt_pct(summary.annual_return)],
        ["最大回撤", _fmt_pct(summary.max_drawdown)],
        ["平均持仓数", f"{summary.avg_position_count:.2f}"],
        ["平均现金占比", _fmt_pct(summary.avg_cash_ratio)],
    ]
    return ["## 核心指标", "", _markdown_table(["指标", "值"], rows), ""]


def _trade_section(summary: BenchmarkSummary) -> list[str]:
    rows = [
        ["成交笔数", str(summary.trade_count)],
        ["买入笔数", str(summary.buy_count)],
        ["卖出笔数", str(summary.sell_count)],
        ["成交额", _fmt_money(summary.total_trade_amount)],
        ["佣金", _fmt_money(summary.total_commission)],
        ["印花税", _fmt_money(summary.total_stamp_tax)],
        ["总费用", _fmt_money(summary.total_fees)],
    ]
    return ["## 成交概览", "", _markdown_table(["项目", "值"], rows), ""]


def _schema_section() -> list[str]:
    mapping = _backtest_field_mapping()
    rows = [[source, target] for source, target in mapping.items()]
    return ["## 回测页面字段映射", "", _markdown_table(["Benchmark 字段", "回测字段"], rows), ""]


def _export_section(exported_files: Mapping[str, str | Path]) -> list[str]:
    rows = [[key, str(path)] for key, path in exported_files.items()]
    return ["## 导出文件", "", _markdown_table(["名称", "文件"], rows), ""]


def _backtest_field_mapping() -> dict[str, str]:
    return {
        "equity_curve.date": "equity_curve.dates",
        "equity_curve.total_assets": "equity_curve.values",
        "equity_curve.cash": "metrics.final_cash / trades.cash_after",
        "equity_curve.position_value": "metrics.final_position_value",
        "trades.trade_date": "trades.trade_date",
        "trades.symbol": "trades.symbol",
        "trades.side": "trades.side",
        "trades.quantity": "trades.quantity",
        "trades.amount": "trades.amount",
        "trades.commission": "trades.commission",
        "trades.stamp_tax": "trades.stamp_tax",
        "trades.net_amount": "trades.net_amount",
        "summary.total_return": "metrics.total_return",
        "summary.annual_return": "metrics.annual_return",
        "summary.max_drawdown": "metrics.max_drawdown",
        "summary.total_fees": "metrics.total_fees",
    }


def _sorted_frame(frame: pd.DataFrame, date_col: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result[date_col] = pd.to_datetime(result[date_col])
    return result.sort_values(date_col).reset_index(drop=True)


def _frame_for_export(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for col in result.columns:
        if col == "date" or col.endswith("_date"):
            result[col] = pd.to_datetime(result[col]).dt.strftime("%Y-%m-%d")
    return result


def _sum_column(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0.0).sum())


def _date_str(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


def _date_range(start_date: str | None, end_date: str | None) -> str:
    if start_date and end_date:
        return f"{start_date} → {end_date}"
    return start_date or end_date or "—"


def _fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        rows = [["—" for _ in headers]]
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(_escape_cell(str(cell)) for cell in row) + " |" for row in rows]
    return "\n".join([header, separator, *body])


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value
