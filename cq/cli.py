"""Command line entry point for claude-quant."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import click
import pandas as pd
from loguru import logger

from cq.benchmark import (
    CrossValidationInputFiles,
    CrossValidationTolerance,
    MomentumTopNConfig,
    compare_benchmark_with_external,
    export_benchmark_result,
    export_cross_validation_result,
    export_cross_validation_template,
    load_cross_validation_frames,
    run_momentum_topn_benchmark,
)
from cq.data.calendar import TradingCalendar
from cq.data.pipeline import DataPipeline
from cq.data.source import create_source
from cq.data.store.parquet_store import ParquetStore
from cq.engine.backtest_engine import BacktestEngine
from cq.research import (
    analyze_factor_groups,
    calculate_forward_returns,
    calculate_ic,
    export_factor_report,
    summarize_ic,
)
from cq.strategy.registry import BUILTIN_STRATEGIES, load_strategy
from cq.universe import (
    AksharePitSourceError,
    PointInTimeUniverseProvider,
    TusharePitSourceError,
    export_pit_validation_result,
    fetch_akshare_pit_universe,
    fetch_tushare_pit_universe,
    filter_prices_by_pit_universe,
    import_pit_memberships,
    parse_akshare_index_specs,
    parse_tushare_index_specs,
    validate_pit_memberships,
)
from cq.utils.config import Config


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """A 股量化研究、回测、benchmark 和模拟/实盘工具。"""


@main.command("backtest")
@click.option("--strategy", "-st", required=True, help="策略名称（如 double_ma）")
@click.option("--symbols", "-s", required=True, multiple=True, help="股票代码，可重复传入")
@click.option("--start", required=True, help="开始日期 YYYY-MM-DD")
@click.option("--end", required=True, help="结束日期 YYYY-MM-DD")
@click.option("--capital", type=float, default=None, help="初始资金（覆盖配置）")
@click.option("--config", "config_path", default="config/local.yaml", show_default=True, help="配置文件路径")
@click.option("--output", "-o", default=None, help="结果输出 JSON 文件路径")
def backtest_cmd(
    strategy: str,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    capital: float | None,
    config_path: str,
    output: str | None,
) -> None:
    """运行事件驱动回测。"""

    config = Config.from_yaml(config_path)
    if capital is not None:
        config.engine.initial_capital = capital
    _setup_logging(config.logging.level)

    if strategy not in BUILTIN_STRATEGIES:
        raise click.BadParameter(f"未知策略: {strategy}。内置策略: {list(BUILTIN_STRATEGIES)}")

    engine = BacktestEngine(config)
    engine.add_strategy(load_strategy(strategy), symbols=list(symbols))
    result = engine.run(start, end)
    click.echo(result.summary())

    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info(f"结果已保存到 {path}")


@main.command("factor-report")
@click.option("--factor-csv", required=True, type=click.Path(exists=True, dir_okay=False), help="因子长表 CSV")
@click.option("--price-csv", required=True, type=click.Path(exists=True, dir_okay=False), help="价格长表 CSV")
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False), help="报告输出目录")
@click.option("--factor-name", default="factor", show_default=True, help="因子名称")
@click.option("--factor-col", default="factor", show_default=True, help="因子值列名")
@click.option("--date-col", default="date", show_default=True, help="日期列名")
@click.option("--symbol-col", default="symbol", show_default=True, help="股票代码列名")
@click.option("--price-col", default="close", show_default=True, help="价格列名")
@click.option("--period", "periods", multiple=True, type=int, default=(1, 5, 20), show_default=True, help="未来收益周期")
@click.option("--groups", type=int, default=5, show_default=True, help="分层组数")
@click.option("--universe", default=None, help="股票池名称")
@click.option("--start", "start_date", default=None, help="报告开始日期 YYYY-MM-DD")
@click.option("--end", "end_date", default=None, help="报告结束日期 YYYY-MM-DD")
@click.option("--sample-split-date", default=None, help="样本内/样本外切分日期 YYYY-MM-DD")
@click.option("--metadata", multiple=True, help="报告元数据，格式 key=value，可重复传入")
def factor_report_cmd(
    factor_csv: str,
    price_csv: str,
    output_dir: str,
    factor_name: str,
    factor_col: str,
    date_col: str,
    symbol_col: str,
    price_col: str,
    periods: tuple[int, ...],
    groups: int,
    universe: str | None,
    start_date: str | None,
    end_date: str | None,
    sample_split_date: str | None,
    metadata: tuple[str, ...],
) -> None:
    """从 CSV 生成因子分层、IC 和 Markdown 报告。"""

    _setup_logging("INFO")
    factor_df = pd.read_csv(factor_csv)
    price_df = pd.read_csv(price_csv)
    metadata_map = _parse_metadata(metadata)
    forward_returns = calculate_forward_returns(
        price_df,
        periods=periods,
        date_col=date_col,
        symbol_col=symbol_col,
        price_col=price_col,
    )
    analysis = analyze_factor_groups(
        factor_df,
        forward_returns,
        group_count=groups,
        periods=periods,
        date_col=date_col,
        symbol_col=symbol_col,
        factor_col=factor_col,
    )
    factor_return = factor_df[[date_col, symbol_col, factor_col]].merge(
        forward_returns,
        on=[date_col, symbol_col],
        how="left",
    )
    ic_summary = summarize_ic(
        calculate_ic(
            factor_return,
            periods=periods,
            date_col=date_col,
            factor_col=factor_col,
        )
    )
    exported = export_factor_report(
        factor_name=factor_name,
        analysis=analysis,
        ic_summary=ic_summary,
        output_dir=output_dir,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        sample_split_date=sample_split_date,
        metadata=metadata_map,
        date_col=date_col,
    )
    logger.info(f"因子报告已输出到 {exported.output_dir}")
    click.echo(str(exported.output_dir))


@main.command("benchmark")
@click.option("--price-csv", required=True, type=click.Path(exists=True, dir_okay=False), help="长表 OHLC CSV")
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False), help="实验包输出目录")
@click.option("--lookback", default=20, show_default=True, type=int, help="动量回看交易日数")
@click.option("--top-n", default=20, show_default=True, type=int, help="持仓股票数量")
@click.option("--rebalance", default="W", show_default=True, type=click.Choice(["D", "W"]), help="调仓频率")
@click.option("--capital", default=1_000_000.0, show_default=True, type=float, help="初始资金")
@click.option("--max-position-weight", default=1.0, show_default=True, type=float, help="组合最大股票仓位")
@click.option("--commission-rate", default=0.00015, show_default=True, type=float, help="佣金率")
@click.option("--stamp-tax-rate", default=0.0005, show_default=True, type=float, help="印花税率")
@click.option("--min-commission", default=5.0, show_default=True, type=float, help="最低佣金")
@click.option("--date-col", default="date", show_default=True, help="日期列名")
@click.option("--symbol-col", default="symbol", show_default=True, help="股票代码列名")
@click.option("--open-col", default="open", show_default=True, help="开盘价列名")
@click.option("--close-col", default="close", show_default=True, help="收盘价列名")
@click.option("--start", "start_date", default=None, help="过滤开始日期 YYYY-MM-DD")
@click.option("--end", "end_date", default=None, help="过滤结束日期 YYYY-MM-DD")
@click.option("--pit-csv", type=click.Path(exists=True, dir_okay=False), default=None, help="PIT 成分股 CSV")
@click.option("--universe-id", default=None, help="PIT 股票池 ID，如 HS300_PIT")
@click.option("--pit-fetch-summary", type=click.Path(exists=True, dir_okay=False), default=None, help="PIT fetch summary JSON；默认自动读取 pit-csv 同名 .summary.json")
def benchmark_cmd(
    price_csv: str,
    output_dir: str,
    lookback: int,
    top_n: int,
    rebalance: str,
    capital: float,
    max_position_weight: float,
    commission_rate: float,
    stamp_tax_rate: float,
    min_commission: float,
    date_col: str,
    symbol_col: str,
    open_col: str,
    close_col: str,
    start_date: str | None,
    end_date: str | None,
    pit_csv: str | None,
    universe_id: str | None,
    pit_fetch_summary: str | None,
) -> None:
    """运行标准 20 日动量 TopN benchmark 并导出可复现实验包。"""

    _setup_logging("INFO")
    prices = pd.read_csv(price_csv)
    prices[date_col] = pd.to_datetime(prices[date_col])
    if start_date:
        prices = prices[prices[date_col] >= pd.Timestamp(start_date)]
    if end_date:
        prices = prices[prices[date_col] <= pd.Timestamp(end_date)]

    metadata: dict[str, Any] = {}
    if pit_fetch_summary and not pit_csv:
        raise click.BadParameter("--pit-fetch-summary requires --pit-csv")
    if pit_csv or universe_id:
        if not pit_csv or not universe_id:
            raise click.BadParameter("--pit-csv and --universe-id must be provided together")
        provider = PointInTimeUniverseProvider.from_csv(pit_csv)
        prices, diagnostics = filter_prices_by_pit_universe(
            prices,
            provider,
            universe_id,
            date_col=date_col,
            symbol_col=symbol_col,
        )
        metadata["universe_diagnostics"] = diagnostics
        source_summary = _load_pit_fetch_summary(pit_csv, pit_fetch_summary)
        if source_summary is not None:
            metadata["universe_source"] = source_summary
            if source_summary.get("strict_historical_pit") is False:
                metadata["universe_quality_warning"] = (
                    "PIT universe source is best-effort/latest snapshot, not strict historical PIT."
                )

    cfg = MomentumTopNConfig(
        lookback=lookback,
        top_n=top_n,
        rebalance=rebalance,  # type: ignore[arg-type]
        initial_capital=capital,
        commission_rate=commission_rate,
        stamp_tax_rate=stamp_tax_rate,
        min_commission=min_commission,
        max_position_weight=max_position_weight,
    )
    result = run_momentum_topn_benchmark(
        prices,
        cfg,
        date_col=date_col,
        symbol_col=symbol_col,
        open_col=open_col,
        close_col=close_col,
    )
    config_payload = {
        "price_csv": str(price_csv),
        "lookback": lookback,
        "top_n": top_n,
        "rebalance": rebalance,
        "initial_capital": capital,
        "commission_rate": commission_rate,
        "stamp_tax_rate": stamp_tax_rate,
        "min_commission": min_commission,
        "max_position_weight": max_position_weight,
        "date_col": date_col,
        "symbol_col": symbol_col,
        "open_col": open_col,
        "close_col": close_col,
        "start_date": start_date,
        "end_date": end_date,
        "pit_csv": pit_csv,
        "universe_id": universe_id,
        "pit_fetch_summary": pit_fetch_summary,
    }
    exported = export_benchmark_result(
        result,
        output_dir,
        universe=universe_id,
        metadata=metadata,
        config=config_payload,
    )
    logger.info(f"Benchmark 实验包已输出到 {exported.output_dir}")
    click.echo(str(exported.output_dir))


@main.command("cross-validate")
@click.option("--local-dir", type=click.Path(exists=True, file_okay=False), help="本地 benchmark 标准导出目录")
@click.option("--external-dir", type=click.Path(exists=True, file_okay=False), help="外部平台导出目录")
@click.option("--local-equity-csv", type=click.Path(exists=True, dir_okay=False), help="本地每日净值 CSV")
@click.option("--local-holdings-csv", type=click.Path(exists=True, dir_okay=False), help="本地每日持仓 CSV")
@click.option("--local-trades-csv", type=click.Path(exists=True, dir_okay=False), help="本地成交记录 CSV")
@click.option("--external-equity-csv", type=click.Path(exists=True, dir_okay=False), help="外部平台每日净值 CSV")
@click.option("--external-holdings-csv", type=click.Path(exists=True, dir_okay=False), help="外部平台每日持仓 CSV")
@click.option("--external-trades-csv", type=click.Path(exists=True, dir_okay=False), help="外部平台成交记录 CSV")
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False), help="对账报告输出目录")
@click.option("--platform-name", default="external", show_default=True, help="外部平台名称")
@click.option("--encoding", default="utf-8", show_default=True, help="CSV 文件编码")
@click.option("--equity-abs", default=1.0, show_default=True, type=float, help="净值/资产绝对误差容忍度")
@click.option("--quantity-abs", default=1e-6, show_default=True, type=float, help="数量绝对误差容忍度")
@click.option("--price-abs", default=0.01, show_default=True, type=float, help="成交价绝对误差容忍度")
@click.option("--amount-abs", default=1.0, show_default=True, type=float, help="成交金额绝对误差容忍度")
@click.option("--fee-abs", default=0.01, show_default=True, type=float, help="手续费/印花税绝对误差容忍度")
def cross_validate_cmd(
    local_dir: str | None,
    external_dir: str | None,
    local_equity_csv: str | None,
    local_holdings_csv: str | None,
    local_trades_csv: str | None,
    external_equity_csv: str | None,
    external_holdings_csv: str | None,
    external_trades_csv: str | None,
    output_dir: str,
    platform_name: str,
    encoding: str,
    equity_abs: float,
    quantity_abs: float,
    price_abs: float,
    amount_abs: float,
    fee_abs: float,
) -> None:
    """对比本地 benchmark 输出和外部平台导出。"""

    _setup_logging("INFO")
    local_files = _resolve_input_files(local_dir, local_equity_csv, local_holdings_csv, local_trades_csv, "local")
    external_files = _resolve_input_files(
        external_dir,
        external_equity_csv,
        external_holdings_csv,
        external_trades_csv,
        platform_name,
    )
    _validate_inputs(local_files, source_name="local")
    _validate_inputs(external_files, source_name=platform_name)
    result = compare_benchmark_with_external(
        load_cross_validation_frames(local_files, encoding=encoding, source_name="local"),
        load_cross_validation_frames(external_files, encoding=encoding, source_name=platform_name),
        CrossValidationTolerance(
            equity_abs=equity_abs,
            quantity_abs=quantity_abs,
            price_abs=price_abs,
            amount_abs=amount_abs,
            fee_abs=fee_abs,
        ),
        platform_name=platform_name,
    )
    exported = export_cross_validation_result(result, output_dir)
    status = "PASS" if result.summary["passed"] else "FAIL"
    logger.info(f"平台交叉验证完成: {status}，输出目录 {exported.output_dir}")
    click.echo(str(exported.output_dir))
    raise SystemExit(0 if result.summary["passed"] else 1)


@main.command("cross-validation-template")
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False), help="模板输出目录")
@click.option("--platform-name", default="external", show_default=True, help="外部平台名称")
def cross_validation_template_cmd(output_dir: str, platform_name: str) -> None:
    """生成外部平台对账 CSV 模板和假设记录文件。"""

    exported = export_cross_validation_template(output_dir, platform_name=platform_name)
    click.echo(str(exported.output_dir))


@main.command("import-pit-universe")
@click.option("--input", "input_csv", required=True, type=click.Path(exists=True, dir_okay=False), help="外部 PIT 成分股 CSV")
@click.option("--output", "output_csv", required=True, type=click.Path(dir_okay=False), help="标准化 CSV 输出路径")
@click.option("--diagnostics", "diagnostics_path", type=click.Path(dir_okay=False), default=None, help="诊断 JSON 输出路径")
@click.option("--encoding", default="utf-8", show_default=True, help="输入 CSV 编码")
def import_pit_universe_cmd(
    input_csv: str,
    output_csv: str,
    diagnostics_path: str | None,
    encoding: str,
) -> None:
    """标准化 point-in-time 指数成分股 CSV。"""

    result = import_pit_memberships(
        input_csv,
        output_csv,
        diagnostics_path=diagnostics_path,
        encoding=encoding,
    )
    click.echo(str(result.output_csv))


@main.command("validate-pit-universe")
@click.option("--input", "input_csv", required=True, type=click.Path(exists=True, dir_okay=False), help="PIT 成分股 CSV")
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False), help="校验报告输出目录")
@click.option("--expected-universe", multiple=True, help="预期必须存在的股票池 ID，可重复传入")
@click.option("--min-symbols", type=int, default=None, help="每个股票池最少成分股数阈值")
@click.option("--coverage-start", default=None, help="覆盖检查开始日期 YYYY-MM-DD")
@click.option("--coverage-end", default=None, help="覆盖检查结束日期 YYYY-MM-DD")
@click.option("--encoding", default="utf-8", show_default=True, help="输入 CSV 编码")
def validate_pit_universe_cmd(
    input_csv: str,
    output_dir: str,
    expected_universe: tuple[str, ...],
    min_symbols: int | None,
    coverage_start: str | None,
    coverage_end: str | None,
    encoding: str,
) -> None:
    """校验 PIT 成分股文件并导出 JSON/CSV/Markdown 报告。"""

    data = pd.read_csv(input_csv, encoding=encoding)
    result = validate_pit_memberships(
        data,
        expected_universes=expected_universe,
        min_symbols=min_symbols,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    exported = export_pit_validation_result(result, output_dir)
    click.echo(str(exported.output_dir))
    raise SystemExit(0 if result.passed else 1)


@main.command("fetch-pit-universe")
@click.option(
    "--provider",
    default="akshare",
    show_default=True,
    type=click.Choice(["akshare", "tushare"]),
    help="PIT 数据源；akshare 为免费最新快照，tushare 为严格历史 PIT",
)
@click.option("--index", "index_specs", multiple=True, help="指数映射，格式 UNIVERSE_ID=INDEX_CODE，可重复传入")
@click.option("--start", required=True, help="开始日期 YYYY-MM-DD")
@click.option("--end", required=True, help="结束日期 YYYY-MM-DD")
@click.option("--output", "output_csv", default="data/universes/pit_memberships.csv", show_default=True, help="PIT 成分股 CSV 输出路径")
@click.option("--weights-output", default="data/universes/pit_weights.csv", show_default=True, help="PIT 权重快照 CSV 输出路径")
@click.option("--raw-dir", default=None, help="原始数据 CSV 目录；未指定时按 provider 使用默认目录")
@click.option("--validation-dir", default="output/universe_validation", show_default=True, help="PIT 校验报告输出目录")
@click.option("--config", "config_path", default="config/local.yaml", show_default=True, help="配置文件路径")
@click.option("--min-symbols", type=int, default=None, help="覆盖默认最小成分股阈值，测试或小样本调试时使用")
@click.option("--request-pause", type=float, default=0.0, show_default=True, help="每次 Tushare 请求后的等待秒数")
def fetch_pit_universe_cmd(
    provider: str,
    index_specs: tuple[str, ...],
    start: str,
    end: str,
    output_csv: str,
    weights_output: str,
    raw_dir: str | None,
    validation_dir: str,
    config_path: str,
    min_symbols: int | None,
    request_pause: float,
) -> None:
    """从外部数据源下载并标准化 point-in-time 指数成分股。"""

    _setup_logging("INFO")
    config = Config.from_yaml(config_path)
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if provider == "akshare":
            specs = parse_akshare_index_specs(index_specs)
            result = fetch_akshare_pit_universe(
                start=start_date,
                end=end_date,
                output_csv=output_csv,
                weights_output=weights_output,
                raw_dir=raw_dir or "data/raw/akshare/csindex",
                validation_dir=validation_dir,
                index_specs=specs,
                min_symbols=min_symbols,
            )
            if result.summary.get("strict_historical_pit") is False:
                logger.warning("AkShare 免费源只提供最新公开快照，不代表严格历史 PIT 股票池。")
        else:
            specs = parse_tushare_index_specs(index_specs)
            result = fetch_tushare_pit_universe(
                start=start_date,
                end=end_date,
                output_csv=output_csv,
                weights_output=weights_output,
                raw_dir=raw_dir or "data/raw/tushare/index_weight",
                validation_dir=validation_dir,
                index_specs=specs,
                token=config.data.tushare_token,
                min_symbols=min_symbols,
                request_pause=request_pause,
            )
    except (ValueError, AksharePitSourceError, TusharePitSourceError) as exc:
        raise click.ClickException(str(exc)) from exc

    logger.info(
        f"PIT 股票池下载完成: memberships={result.output_csv} weights={result.weights_output} "
        f"validation_passed={result.validation_result.passed}"
    )
    click.echo(str(result.output_csv))
    raise SystemExit(0 if result.validation_result.passed else 1)


@main.command("download-data")
@click.option("--symbols", "-s", multiple=True, help="股票代码，如 600519.SH 000001.SZ")
@click.option("--years", "-y", type=int, default=None, help="下载最近 N 年数据")
@click.option("--start", type=str, default=None, help="开始日期 YYYY-MM-DD")
@click.option("--end", type=str, default=None, help="结束日期 YYYY-MM-DD（默认今天）")
@click.option("--force", is_flag=True, default=False, help="强制重新全量下载")
@click.option("--sync-calendar", is_flag=True, default=False, help="同步交易日历")
@click.option("--workers", type=int, default=8, show_default=True, help="并行下载线程数")
@click.option("--config", "config_path", type=str, default="config/local.yaml", show_default=True, help="配置文件路径")
def download_data_cmd(
    symbols: tuple[str, ...],
    years: int | None,
    start: str | None,
    end: str | None,
    force: bool,
    sync_calendar: bool,
    workers: int,
    config_path: str,
) -> None:
    """下载并存储历史 K 线数据。"""

    config = Config.from_yaml(config_path)
    _setup_logging(config.logging.level)
    pipeline = _build_pipeline(config)
    if sync_calendar:
        current_year = date.today().year
        pipeline.sync_calendar("SSE", years=list(range(2000, current_year + 1)))
        pipeline.sync_calendar("SZSE", years=list(range(2000, current_year + 1)))
        if not symbols:
            return
    if not symbols:
        raise click.BadParameter("请指定股票代码，如 --symbols 600519.SH")
    end_date = date.fromisoformat(end) if end else date.today()
    if years is not None:
        start_date = date(end_date.year - years, end_date.month, end_date.day)
    elif start is not None:
        start_date = date.fromisoformat(start)
    else:
        start_date = None
    if len(symbols) == 1:
        result = pipeline.update_symbol(symbols[0], end_date, start_date=start_date, force=force)
        logger.info(f"完成，新增 {result} 条记录")
    else:
        results = pipeline.update_batch(list(symbols), end_date, start_date=start_date, max_workers=workers, force=force)
        logger.info(f"全部完成，共新增 {sum(results.values())} 条记录")


@main.command("live")
@click.option("--strategy", "-s", required=False, help="策略名称")
@click.option("--symbols", "-S", multiple=True, help="标的代码，如 600519.SH")
@click.option("--config", "-c", "config_path", default="config/local.yaml", show_default=True, help="配置文件路径")
@click.option("--paper", is_flag=True, default=False, help="纸上交易模式")
@click.option("--paper-start", default=None, help="纸上交易起始日期 YYYY-MM-DD")
@click.option("--paper-end", default=None, help="纸上交易结束日期 YYYY-MM-DD")
@click.option("--list-strategies", is_flag=True, default=False, help="列出所有可用策略")
def live_cmd(
    strategy: str | None,
    symbols: tuple[str, ...],
    config_path: str,
    paper: bool,
    paper_start: str | None,
    paper_end: str | None,
    list_strategies: bool,
) -> None:
    """启动纸上交易或实盘引擎。"""

    if list_strategies:
        click.echo("可用策略：")
        for name in BUILTIN_STRATEGIES:
            click.echo(f"  {name}")
        return
    if not strategy:
        raise click.BadParameter("--strategy is required unless --list-strategies is used")
    if not symbols:
        raise click.BadParameter("--symbols is required")

    config = Config.from_yaml(config_path)
    _setup_logging(config.logging.level)
    engine = _build_live_engine(config, strategy, list(symbols))
    store = ParquetStore(config.data.root_path)
    if paper:
        end = date.fromisoformat(paper_end) if paper_end else date.today() - timedelta(days=1)
        start = date.fromisoformat(paper_start) if paper_start else end - timedelta(days=90)
        engine.paper_trade(store=store, start_date=start, end_date=end)
    else:
        engine.run()


def _setup_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level)


def _parse_metadata(items: tuple[str, ...]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise click.BadParameter(f"metadata must be key=value: {item}")
        key, value = item.split("=", 1)
        if not key:
            raise click.BadParameter("metadata key must not be empty")
        parsed[key] = value
    return parsed


def _load_pit_fetch_summary(pit_csv: str, explicit_path: str | None) -> dict[str, Any] | None:
    path = Path(explicit_path) if explicit_path else Path(pit_csv).with_suffix(".summary.json")
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"PIT fetch summary is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise click.BadParameter(f"PIT fetch summary must be a JSON object: {path}")
    return payload


def _resolve_input_files(
    directory: str | None,
    equity_csv: str | None,
    holdings_csv: str | None,
    trades_csv: str | None,
    source_name: str,
) -> CrossValidationInputFiles:
    base_dir = Path(directory) if directory else None
    return CrossValidationInputFiles(
        equity_curve=_resolve_file(base_dir, equity_csv, "equity_curve.csv", source_name),
        holdings=_resolve_file(base_dir, holdings_csv, "holdings.csv", source_name),
        trades=_resolve_file(base_dir, trades_csv, "trades.csv", source_name),
    )


def _resolve_file(
    base_dir: Path | None,
    explicit_path: str | None,
    default_name: str,
    source_name: str,
) -> Path | None:
    if explicit_path:
        return Path(explicit_path)
    if base_dir is None:
        return None
    path = base_dir / default_name
    if not path.exists():
        raise click.BadParameter(f"{source_name} directory missing {default_name}: {path}")
    return path


def _validate_inputs(files: CrossValidationInputFiles, *, source_name: str) -> None:
    if files.equity_curve is None and files.holdings is None and files.trades is None:
        raise click.BadParameter(f"{source_name} must provide at least one CSV file")
    if files.equity_curve is None:
        raise click.BadParameter(f"{source_name} equity CSV is required")
    if files.holdings is None:
        raise click.BadParameter(f"{source_name} holdings CSV is required")
    if files.trades is None:
        raise click.BadParameter(f"{source_name} trades CSV is required")


def _build_pipeline(config: Config) -> DataPipeline:
    store = ParquetStore(config.data.root_path)
    source = create_source(config.data.source)
    calendar_days = store.read_calendar("SSE")
    if not calendar_days:
        pipeline = DataPipeline(source, store, None)  # type: ignore[arg-type]
        pipeline.sync_calendar("SSE")
        calendar_days = store.read_calendar("SSE")
    return DataPipeline(source, store, TradingCalendar(calendar_days))


def _build_live_engine(config: Config, strategy_id: str, symbols: list[str]):
    from cq.live.engine import LiveEngine

    engine = LiveEngine(config)
    engine.add_strategy(load_strategy(strategy_id), symbols=symbols)
    return engine


if __name__ == "__main__":
    main()
