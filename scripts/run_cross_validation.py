#!/usr/bin/env python
"""Compare local benchmark CSV exports with external platform CSV exports."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import click
from loguru import logger

from cq.benchmark import (
    CrossValidationInputFiles,
    CrossValidationTolerance,
    compare_benchmark_with_external,
    export_cross_validation_result,
    load_cross_validation_frames,
)


@click.command()
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
def main(
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
    """Run cross-platform validation and export comparison files."""

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    local_files = _resolve_input_files(
        directory=local_dir,
        equity_csv=local_equity_csv,
        holdings_csv=local_holdings_csv,
        trades_csv=local_trades_csv,
        source_name="local",
    )
    external_files = _resolve_input_files(
        directory=external_dir,
        equity_csv=external_equity_csv,
        holdings_csv=external_holdings_csv,
        trades_csv=external_trades_csv,
        source_name=platform_name,
    )
    _validate_inputs(local_files, source_name="local")
    _validate_inputs(external_files, source_name=platform_name)

    local_frames = load_cross_validation_frames(
        local_files,
        encoding=encoding,
        source_name="local",
    )
    external_frames = load_cross_validation_frames(
        external_files,
        encoding=encoding,
        source_name=platform_name,
    )
    tolerance = CrossValidationTolerance(
        equity_abs=equity_abs,
        quantity_abs=quantity_abs,
        price_abs=price_abs,
        amount_abs=amount_abs,
        fee_abs=fee_abs,
    )
    result = compare_benchmark_with_external(
        local_frames,
        external_frames,
        tolerance,
        platform_name=platform_name,
    )
    exported = export_cross_validation_result(result, output_dir)

    status = "PASS" if result.summary["passed"] else "FAIL"
    logger.info(f"平台交叉验证完成: {status}，输出目录 {exported.output_dir}")
    click.echo(str(exported.output_dir))
    raise SystemExit(0 if result.summary["passed"] else 1)


def _resolve_input_files(
    *,
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


if __name__ == "__main__":
    main()
