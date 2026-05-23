#!/usr/bin/env python
"""Generate a factor research report from CSV inputs."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import click
import pandas as pd
from loguru import logger

from cq.research import (
    analyze_factor_groups,
    calculate_forward_returns,
    calculate_ic,
    export_factor_report,
    summarize_ic,
)


@click.command()
@click.option("--factor-csv", required=True, type=click.Path(exists=True, dir_okay=False), help="因子长表 CSV，至少包含 date/symbol/factor")
@click.option("--price-csv", required=True, type=click.Path(exists=True, dir_okay=False), help="价格长表 CSV，至少包含 date/symbol/close")
@click.option("--output-dir", "-o", required=True, type=click.Path(file_okay=False), help="报告输出目录")
@click.option("--factor-name", default="factor", show_default=True, help="因子名称")
@click.option("--factor-col", default="factor", show_default=True, help="因子值列名")
@click.option("--date-col", default="date", show_default=True, help="日期列名")
@click.option("--symbol-col", default="symbol", show_default=True, help="股票代码列名")
@click.option("--price-col", default="close", show_default=True, help="价格列名")
@click.option("--period", "periods", multiple=True, type=int, default=(1, 5, 20), show_default=True, help="未来收益周期，可重复传入")
@click.option("--groups", type=int, default=5, show_default=True, help="分层组数")
@click.option("--universe", default=None, help="股票池名称")
@click.option("--start", "start_date", default=None, help="报告开始日期 YYYY-MM-DD")
@click.option("--end", "end_date", default=None, help="报告结束日期 YYYY-MM-DD")
@click.option("--metadata", multiple=True, help="报告元数据，格式 key=value，可重复传入")
def main(
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
    metadata: tuple[str, ...],
) -> None:
    """Generate factor grouping, IC, and report export files."""

    logger.remove()
    logger.add(sys.stderr, level="INFO")

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
        metadata=metadata_map,
        date_col=date_col,
    )
    logger.info(f"因子报告已输出到 {exported.output_dir}")
    click.echo(str(exported.output_dir))


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


if __name__ == "__main__":
    main()
