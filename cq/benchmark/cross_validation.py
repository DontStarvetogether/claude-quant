"""Cross-platform benchmark validation helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from cq.benchmark.momentum_topn import BenchmarkResult

SCHEMA_VERSION = "cross_validation.v1"


@dataclass(frozen=True)
class CrossValidationTolerance:
    """Absolute tolerances for comparing exported platform results."""

    equity_abs: float = 1.0
    quantity_abs: float = 1e-6
    price_abs: float = 0.01
    amount_abs: float = 1.0
    fee_abs: float = 0.01


@dataclass(frozen=True)
class CrossValidationResult:
    """Comparison tables, summary, and rendered Markdown report."""

    summary: dict[str, Any]
    equity: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    markdown: str


@dataclass(frozen=True)
class CrossValidationExport:
    """Paths written by cross-validation export."""

    output_dir: Path
    files: dict[str, Path]
    summary: dict[str, Any]


def compare_benchmark_with_external(
    local: BenchmarkResult | Mapping[str, pd.DataFrame],
    external: Mapping[str, pd.DataFrame],
    tolerance: CrossValidationTolerance | None = None,
    *,
    platform_name: str = "external",
) -> CrossValidationResult:
    """Compare local benchmark outputs with external platform exports."""

    tol = tolerance or CrossValidationTolerance()
    local_frames = _frames_from_input(local)
    equity = _compare_equity(
        local_frames.get("equity_curve", pd.DataFrame()),
        external.get("equity_curve", pd.DataFrame()),
        tol,
    )
    holdings = _compare_holdings(
        local_frames.get("holdings", pd.DataFrame()),
        external.get("holdings", pd.DataFrame()),
        tol,
    )
    trades = _compare_trades(
        local_frames.get("trades", pd.DataFrame()),
        external.get("trades", pd.DataFrame()),
        tol,
    )
    summary = _build_summary(equity, holdings, trades, tol, platform_name)
    markdown = generate_cross_validation_report(summary, equity, holdings, trades)
    return CrossValidationResult(summary=summary, equity=equity, holdings=holdings, trades=trades, markdown=markdown)


def export_cross_validation_result(
    result: CrossValidationResult,
    output_dir: str | Path,
) -> CrossValidationExport:
    """Export cross-validation report, summary, and mismatch tables."""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    files: dict[str, Path] = {}
    for name, frame in {
        "equity_comparison": result.equity,
        "holdings_comparison": result.holdings,
        "trades_comparison": result.trades,
    }.items():
        path = out / f"{name}.csv"
        frame.to_csv(path, index=False)
        files[name] = path

    summary_path = out / "cross_validation_summary.json"
    summary_path.write_text(
        json.dumps(_json_safe(result.summary), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    files["summary"] = summary_path

    report_path = out / "cross_validation_report.md"
    report_path.write_text(result.markdown, encoding="utf-8")
    files["report"] = report_path
    return CrossValidationExport(output_dir=out, files=files, summary=result.summary)


def generate_cross_validation_report(
    summary: Mapping[str, Any],
    equity: pd.DataFrame,
    holdings: pd.DataFrame,
    trades: pd.DataFrame,
) -> str:
    """Render a Markdown cross-validation report."""

    result_text = "PASS" if summary.get("passed") else "FAIL"
    lines = [
        "# 平台交叉验证报告",
        "",
        "## 总览",
        "",
        "| 项目 | 值 |",
        "|---|---:|",
        f"| 结果 | {result_text} |",
        f"| 对照平台 | {summary.get('platform_name', '')} |",
        f"| 净值差异行数 | {summary.get('equity_mismatches', 0)} |",
        f"| 持仓差异行数 | {summary.get('holding_mismatches', 0)} |",
        f"| 成交差异行数 | {summary.get('trade_mismatches', 0)} |",
        f"| 最大净值差异 | {summary.get('max_equity_abs_diff', 0.0)} |",
        "",
        "## 差异排查顺序",
        "",
        "1. 复权方式",
        "2. 股票池是否 point-in-time",
        "3. 调仓日、信号日、成交日是否一致",
        "4. 成交价格、手续费、印花税、滑点是否一致",
        "5. 涨跌停、停牌、新股、退市、ST 处理是否一致",
    ]
    lines.extend(_mismatch_section("净值差异样例", equity))
    lines.extend(_mismatch_section("持仓差异样例", holdings))
    lines.extend(_mismatch_section("成交差异样例", trades))
    return "\n".join(lines).rstrip() + "\n"


def _compare_equity(local: pd.DataFrame, external: pd.DataFrame, tol: CrossValidationTolerance) -> pd.DataFrame:
    numeric_cols = ["total_assets", "cash", "position_value"]
    local_norm = _normalize_by_date(local, "date", numeric_cols)
    external_norm = _normalize_by_date(external, "date", numeric_cols)
    return _compare_frames(local_norm, external_norm, ["date"], numeric_cols, {col: tol.equity_abs for col in numeric_cols})


def _compare_holdings(local: pd.DataFrame, external: pd.DataFrame, tol: CrossValidationTolerance) -> pd.DataFrame:
    numeric_cols = ["quantity", "market_value"]
    local_norm = _normalize_grouped(local, ["date", "symbol"], numeric_cols)
    external_norm = _normalize_grouped(external, ["date", "symbol"], numeric_cols)
    tolerances = {"quantity": tol.quantity_abs, "market_value": tol.equity_abs}
    return _compare_frames(local_norm, external_norm, ["date", "symbol"], numeric_cols, tolerances)


def _compare_trades(local: pd.DataFrame, external: pd.DataFrame, tol: CrossValidationTolerance) -> pd.DataFrame:
    numeric_cols = ["quantity", "price", "amount", "commission", "stamp_tax", "net_amount"]
    local_norm = _normalize_trades(local, numeric_cols)
    external_norm = _normalize_trades(external, numeric_cols)
    tolerances = {
        "quantity": tol.quantity_abs,
        "price": tol.price_abs,
        "amount": tol.amount_abs,
        "commission": tol.fee_abs,
        "stamp_tax": tol.fee_abs,
        "net_amount": tol.amount_abs,
    }
    return _compare_frames(local_norm, external_norm, ["trade_date", "symbol", "side"], numeric_cols, tolerances)


def _frames_from_input(source: BenchmarkResult | Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    if isinstance(source, BenchmarkResult):
        return {
            "equity_curve": source.equity_curve,
            "holdings": source.holdings,
            "trades": source.trades,
            "signals": source.signals,
        }
    return {key: frame for key, frame in source.items()}


def _normalize_by_date(df: pd.DataFrame, date_col: str, numeric_cols: list[str]) -> pd.DataFrame:
    columns = [date_col, *numeric_cols]
    if df.empty or date_col not in df.columns:
        return pd.DataFrame(columns=columns)
    data = df.copy()
    data[date_col] = _date_str_series(data[date_col])
    for col in numeric_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce") if col in data.columns else 0.0
    return data[columns].groupby(date_col, as_index=False).sum(numeric_only=True)


def _normalize_grouped(df: pd.DataFrame, key_cols: list[str], numeric_cols: list[str]) -> pd.DataFrame:
    columns = [*key_cols, *numeric_cols]
    if df.empty or any(col not in df.columns for col in key_cols):
        return pd.DataFrame(columns=columns)
    data = df.copy()
    data[key_cols[0]] = _date_str_series(data[key_cols[0]])
    data[key_cols[1]] = data[key_cols[1]].astype(str).str.upper()
    for col in numeric_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce") if col in data.columns else 0.0
    return data[columns].groupby(key_cols, as_index=False).sum(numeric_only=True)


def _normalize_trades(df: pd.DataFrame, numeric_cols: list[str]) -> pd.DataFrame:
    key_cols = ["trade_date", "symbol", "side"]
    columns = [*key_cols, *numeric_cols]
    if df.empty:
        return pd.DataFrame(columns=columns)
    data = df.copy()
    if "trade_date" not in data.columns and "date" in data.columns:
        data = data.rename(columns={"date": "trade_date"})
    if any(col not in data.columns for col in key_cols):
        return pd.DataFrame(columns=columns)
    data["trade_date"] = _date_str_series(data["trade_date"])
    data["symbol"] = data["symbol"].astype(str).str.upper()
    data["side"] = data["side"].astype(str).str.upper()
    amount_missing = "amount" not in data.columns
    for col in numeric_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce") if col in data.columns else 0.0
    if amount_missing and {"price", "quantity"}.issubset(data.columns):
        data["amount"] = data["price"] * data["quantity"]
    grouped = data[columns].groupby(key_cols, as_index=False).sum(numeric_only=True)
    quantity = grouped["quantity"].where(grouped["quantity"] != 0)
    grouped["price"] = (grouped["amount"] / quantity).fillna(0.0)
    return grouped[columns]


def _compare_frames(
    local: pd.DataFrame,
    external: pd.DataFrame,
    key_cols: list[str],
    numeric_cols: list[str],
    tolerances: Mapping[str, float],
) -> pd.DataFrame:
    columns = [
        *key_cols,
        *[f"local_{col}" for col in numeric_cols],
        *[f"external_{col}" for col in numeric_cols],
        *[f"{col}_diff" for col in numeric_cols],
        *[f"{col}_abs_diff" for col in numeric_cols],
        "status",
    ]
    if local.empty and external.empty:
        return pd.DataFrame(columns=columns)

    merged = local.merge(external, on=key_cols, how="outer", suffixes=("_local", "_external"), indicator=True)
    for col in numeric_cols:
        local_col = f"{col}_local"
        external_col = f"{col}_external"
        if local_col not in merged.columns:
            merged[local_col] = pd.NA
        if external_col not in merged.columns:
            merged[external_col] = pd.NA
        merged[f"{col}_diff"] = merged[local_col].fillna(0.0) - merged[external_col].fillna(0.0)
        merged[f"{col}_abs_diff"] = merged[f"{col}_diff"].abs()

    merged["status"] = merged.apply(
        lambda row: _row_status(row, numeric_cols, tolerances),
        axis=1,
    )
    merged = merged.rename(
        columns={
            **{f"{col}_local": f"local_{col}" for col in numeric_cols},
            **{f"{col}_external": f"external_{col}" for col in numeric_cols},
        }
    )
    return merged[columns].sort_values(key_cols).reset_index(drop=True)


def _row_status(row: pd.Series, numeric_cols: list[str], tolerances: Mapping[str, float]) -> str:
    if row["_merge"] == "left_only":
        return "missing_external"
    if row["_merge"] == "right_only":
        return "missing_local"
    for col in numeric_cols:
        if float(row[f"{col}_abs_diff"]) > tolerances[col]:
            return "different"
    return "matched"


def _build_summary(
    equity: pd.DataFrame,
    holdings: pd.DataFrame,
    trades: pd.DataFrame,
    tolerance: CrossValidationTolerance,
    platform_name: str,
) -> dict[str, Any]:
    equity_mismatches = _mismatch_count(equity)
    holding_mismatches = _mismatch_count(holdings)
    trade_mismatches = _mismatch_count(trades)
    total_mismatches = equity_mismatches + holding_mismatches + trade_mismatches
    max_equity_abs_diff = (
        float(equity["total_assets_abs_diff"].max())
        if "total_assets_abs_diff" in equity.columns and not equity.empty
        else 0.0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "platform_name": platform_name,
        "passed": total_mismatches == 0,
        "equity_rows": int(len(equity)),
        "holding_rows": int(len(holdings)),
        "trade_rows": int(len(trades)),
        "equity_mismatches": equity_mismatches,
        "holding_mismatches": holding_mismatches,
        "trade_mismatches": trade_mismatches,
        "total_mismatches": total_mismatches,
        "max_equity_abs_diff": round(max_equity_abs_diff, 6),
        "tolerance": asdict(tolerance),
    }


def _mismatch_count(frame: pd.DataFrame) -> int:
    if frame.empty or "status" not in frame.columns:
        return 0
    return int((frame["status"] != "matched").sum())


def _mismatch_section(title: str, frame: pd.DataFrame) -> list[str]:
    mismatches = frame[frame["status"] != "matched"].head(10) if "status" in frame.columns else pd.DataFrame()
    lines = ["", f"## {title}", ""]
    if mismatches.empty:
        lines.append("无差异。")
        return lines
    lines.append(_frame_to_markdown(mismatches))
    return lines


def _date_str_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.strftime("%Y-%m-%d")


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
