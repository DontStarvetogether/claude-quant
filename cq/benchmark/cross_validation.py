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
CROSS_VALIDATION_TEMPLATE_SCHEMA_VERSION = "cross_validation_template.v1"


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


@dataclass(frozen=True)
class CrossValidationInputFiles:
    """CSV files exported by a local benchmark run or an external platform."""

    equity_curve: Path | None = None
    holdings: Path | None = None
    trades: Path | None = None


@dataclass(frozen=True)
class CrossValidationTemplateExport:
    """Template files for collecting external platform exports."""

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


def load_cross_validation_frames(
    files: CrossValidationInputFiles,
    *,
    encoding: str = "utf-8",
    source_name: str = "external",
) -> dict[str, pd.DataFrame]:
    """Load CSV exports and standardize common external-platform column names.

    The canonical output keys match ``BenchmarkResult`` exports:

    - ``equity_curve``: date,total_assets,cash,position_value
    - ``holdings``: date,symbol,quantity,market_value
    - ``trades``: trade_date,symbol,side,quantity,price,amount,commission,stamp_tax,net_amount
    """

    frames: dict[str, pd.DataFrame] = {}
    if files.equity_curve is not None:
        frames["equity_curve"] = _standardize_equity_frame(
            pd.read_csv(files.equity_curve, encoding=encoding),
            source_name=source_name,
        )
    if files.holdings is not None:
        frames["holdings"] = _standardize_holdings_frame(
            pd.read_csv(files.holdings, encoding=encoding),
            source_name=source_name,
        )
    if files.trades is not None:
        frames["trades"] = _standardize_trades_frame(
            pd.read_csv(files.trades, encoding=encoding),
            source_name=source_name,
        )
    return frames


def export_cross_validation_template(
    output_dir: str | Path,
    *,
    platform_name: str = "external",
) -> CrossValidationTemplateExport:
    """Write canonical CSV templates and an assumptions file for external validation."""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    templates = {
        "equity_curve": pd.DataFrame(
            columns=["date", "total_assets", "cash", "position_value"]
        ),
        "holdings": pd.DataFrame(columns=["date", "symbol", "quantity", "market_value"]),
        "trades": pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "side",
                "quantity",
                "price",
                "amount",
                "commission",
                "stamp_tax",
                "net_amount",
            ]
        ),
    }
    for name, frame in templates.items():
        path = out / f"{name}.csv"
        frame.to_csv(path, index=False)
        files[name] = path

    assumptions = {
        "schema_version": CROSS_VALIDATION_TEMPLATE_SCHEMA_VERSION,
        "platform_name": platform_name,
        "required_files": {
            "equity_curve": "date,total_assets,cash,position_value",
            "holdings": "date,symbol,quantity,market_value",
            "trades": "trade_date,symbol,side,quantity,price,amount,commission,stamp_tax,net_amount",
        },
        "must_record": [
            "adjustment_mode",
            "universe_id_or_membership_file",
            "rebalance_schedule",
            "signal_price",
            "execution_price",
            "commission_rate",
            "min_commission",
            "stamp_tax_rate",
            "slippage_model",
            "limit_up_down_policy",
            "suspension_policy",
            "t1_policy",
            "lot_size_policy",
        ],
        "notes": "把外部平台导出的字段改名为模板列名，或保留常见中文/平台字段名让 cq cross-validate 自动识别。",
    }
    assumptions_path = out / "external_platform_assumptions.json"
    assumptions_path.write_text(json.dumps(assumptions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    files["assumptions"] = assumptions_path

    readme_path = out / "README.md"
    readme_path.write_text(_cross_validation_template_readme(platform_name), encoding="utf-8")
    files["readme"] = readme_path
    return CrossValidationTemplateExport(output_dir=out, files=files, summary=assumptions)


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


def _cross_validation_template_readme(platform_name: str) -> str:
    return (
        "# 外部平台对账导出模板\n\n"
        f"目标平台：`{platform_name}`\n\n"
        "把 JoinQuant / RiceQuant / QMT 等平台导出的每日净值、每日持仓、成交记录整理到本目录三张 CSV：\n\n"
        "- `equity_curve.csv`: `date,total_assets,cash,position_value`\n"
        "- `holdings.csv`: `date,symbol,quantity,market_value`\n"
        "- `trades.csv`: `trade_date,symbol,side,quantity,price,amount,commission,stamp_tax,net_amount`\n\n"
        "`side` 使用 `BUY` / `SELL`，股票代码使用 `000001.SZ` / `600000.SH` / `430047.BJ`。\n\n"
        "同时填写 `external_platform_assumptions.json` 里的复权、股票池、调仓日、成交价、费用、"
        "涨跌停、停牌、T+1、最小交易单位等假设，便于解释差异。\n"
    )


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


def _standardize_equity_frame(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    data = _standardize_columns(
        df,
        frame_name="equity_curve",
        source_name=source_name,
        aliases={
            "date": ["trade_date", "datetime", "time", "day", "日期", "交易日期"],
            "total_assets": [
                "total_value",
                "portfolio_value",
                "net_value",
                "nav",
                "account_value",
                "assets",
                "asset",
                "总资产",
                "账户总资产",
                "净值",
            ],
            "cash": ["available_cash", "cash_balance", "available", "现金", "可用资金", "持有现金"],
            "position_value": [
                "positions_value",
                "market_value",
                "stock_value",
                "securities_value",
                "持仓市值",
                "证券市值",
            ],
        },
        required=["date", "total_assets"],
    )
    if "cash" not in data.columns:
        data["cash"] = 0.0
    if "position_value" not in data.columns:
        data["position_value"] = pd.to_numeric(data["total_assets"], errors="coerce").fillna(0.0) - pd.to_numeric(
            data["cash"],
            errors="coerce",
        ).fillna(0.0)
    return data[["date", "total_assets", "cash", "position_value"]]


def _standardize_holdings_frame(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    data = _standardize_columns(
        df,
        frame_name="holdings",
        source_name=source_name,
        aliases={
            "date": ["trade_date", "datetime", "time", "day", "日期", "交易日期"],
            "symbol": ["code", "security", "order_book_id", "instrument", "stock", "股票代码", "证券代码"],
            "quantity": ["shares", "volume", "position", "qty", "持仓数量", "股份数量", "数量"],
            "market_value": ["value", "position_value", "stock_value", "市值", "持仓市值", "证券市值"],
        },
        required=["date", "symbol", "quantity"],
    )
    if "market_value" not in data.columns:
        data["market_value"] = 0.0
    return data[["date", "symbol", "quantity", "market_value"]]


def _standardize_trades_frame(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    data = _standardize_columns(
        df,
        frame_name="trades",
        source_name=source_name,
        aliases={
            "trade_date": ["date", "datetime", "time", "day", "日期", "交易日期", "成交日期"],
            "symbol": ["code", "security", "order_book_id", "instrument", "stock", "股票代码", "证券代码"],
            "side": ["action", "direction", "operation", "买卖方向", "交易方向", "操作"],
            "quantity": ["shares", "volume", "qty", "成交数量", "股份数量", "数量"],
            "price": ["成交价格", "成交价", "价格"],
            "amount": ["turnover", "trade_amount", "成交金额", "交易金额", "金额"],
            "commission": ["fee", "手续费", "佣金"],
            "stamp_tax": ["tax", "印花税"],
            "net_amount": ["net_turnover", "net_value", "净成交金额", "净额"],
        },
        required=["trade_date", "symbol", "side", "quantity", "price"],
    )
    data["side"] = _standardize_side(data["side"], source_name=source_name)
    quantity = pd.to_numeric(data["quantity"], errors="coerce").fillna(0.0)
    price = pd.to_numeric(data["price"], errors="coerce").fillna(0.0)
    if "amount" not in data.columns:
        data["amount"] = quantity * price
    if "commission" not in data.columns:
        data["commission"] = 0.0
    if "stamp_tax" not in data.columns:
        data["stamp_tax"] = 0.0
    if "net_amount" not in data.columns:
        amount = pd.to_numeric(data["amount"], errors="coerce").fillna(0.0)
        commission = pd.to_numeric(data["commission"], errors="coerce").fillna(0.0)
        stamp_tax = pd.to_numeric(data["stamp_tax"], errors="coerce").fillna(0.0)
        data["net_amount"] = amount.where(data["side"] == "SELL", amount + commission)
        data.loc[data["side"] == "SELL", "net_amount"] = amount - commission - stamp_tax
    return data[
        [
            "trade_date",
            "symbol",
            "side",
            "quantity",
            "price",
            "amount",
            "commission",
            "stamp_tax",
            "net_amount",
        ]
    ]


def _standardize_columns(
    df: pd.DataFrame,
    *,
    frame_name: str,
    source_name: str,
    aliases: Mapping[str, list[str]],
    required: list[str],
) -> pd.DataFrame:
    data = df.copy()
    rename_map: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        source_col = _find_column(data.columns, [canonical, *candidates])
        if source_col is None:
            if canonical in required:
                raise ValueError(f"{source_name} {frame_name} missing required column: {canonical}")
            continue
        if source_col != canonical:
            rename_map[source_col] = canonical
    if rename_map:
        data = data.rename(columns=rename_map)
    return data


def _find_column(columns: pd.Index, candidates: list[str]) -> str | None:
    by_normalized = {_normalize_col_name(col): str(col) for col in columns}
    for candidate in candidates:
        found = by_normalized.get(_normalize_col_name(candidate))
        if found is not None:
            return found
    return None


def _normalize_col_name(value: object) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _standardize_side(series: pd.Series, *, source_name: str) -> pd.Series:
    mapping = {
        "BUY": "BUY",
        "B": "BUY",
        "买": "BUY",
        "买入": "BUY",
        "限价买入": "BUY",
        "市价买入": "BUY",
        "SELL": "SELL",
        "S": "SELL",
        "卖": "SELL",
        "卖出": "SELL",
        "限价卖出": "SELL",
        "市价卖出": "SELL",
    }
    normalized = series.astype(str).str.strip()
    standardized = normalized.map(lambda value: mapping.get(value.upper(), mapping.get(value, "")))
    bad_values = sorted(set(normalized[standardized == ""]))
    if bad_values:
        raise ValueError(f"{source_name} trades contains unsupported side values: {bad_values}")
    return standardized


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
