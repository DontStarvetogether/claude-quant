"""
实盘启动脚本。

使用方法：
    python scripts/run_live.py --strategy double_ma --symbols 600519.SH 000858.SZ

启动前检查：
  1. 历史数据是否已下载到昨日（策略计算指标需要）
  2. 今天是否是交易日（周末/节假日自动提示）
  3. QMT 客户端是否已启动

日线策略说明（重要）：
  实盘日线策略应在 after_trading() 中下单，而非 on_bar()。
  这样信号基于当天收盘数据，订单在次日开盘集合竞价中执行，
  与回测的 D日信号→D+1开盘成交 保持一致。
  详见 docs/design/06-live-trading.md。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import click
from loguru import logger

# 确保项目根目录在 Python 路径中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cq.data.store.parquet_store import ParquetStore  # noqa: E402
from cq.strategy.base import Strategy  # noqa: E402
from cq.utils.config import Config  # noqa: E402


# ── 已注册的策略（在此处添加你自己的策略）────────────────────────────────────────
def _get_strategy_registry() -> dict[str, type[Strategy]]:
    from cq.strategy.examples.bollinger import BollingerStrategy
    from cq.strategy.examples.double_ma import DoubleMaStrategy
    from cq.strategy.examples.momentum import MomentumStrategy
    from cq.strategy.examples.rsi import RsiStrategy
    return {
        "double_ma": DoubleMaStrategy,
        "rsi":       RsiStrategy,
        "bollinger": BollingerStrategy,
        "momentum":  MomentumStrategy,
    }


@click.command()
@click.option("--strategy", "-s", required=True, help="策略名称（见 --list-strategies）")
@click.option("--symbols", "-S", required=True, multiple=True, help="标的代码，如 600519.SH")
@click.option("--config", "-c", "config_path",
              default=str(ROOT / "config" / "default.yaml"),
              show_default=True, help="配置文件路径")
@click.option("--paper", is_flag=True, default=False,
              help="纸上交易模式（用历史数据模拟，不下真实订单）")
@click.option("--paper-start", default=None, help="纸上交易起始日期，如 2024-01-02")
@click.option("--paper-end", default=None, help="纸上交易结束日期，如 2024-06-28")
@click.option("--list-strategies", is_flag=True, default=False, help="列出所有可用策略")
def main(
    strategy: str,
    symbols: tuple[str, ...],
    config_path: str,
    paper: bool,
    paper_start: str | None,
    paper_end: str | None,
    list_strategies: bool,
) -> None:
    registry = _get_strategy_registry()

    if list_strategies:
        click.echo("可用策略：")
        for name in registry:
            click.echo(f"  {name}")
        return

    if strategy not in registry:
        click.echo(f"错误：未知策略 '{strategy}'，使用 --list-strategies 查看可用策略", err=True)
        sys.exit(1)

    cfg = Config.from_yaml(config_path)
    _setup_logging(cfg)

    symbols_list = list(symbols)

    if paper:
        _run_paper(cfg, registry[strategy], symbols_list, paper_start, paper_end)
    else:
        _run_live(cfg, registry[strategy], symbols_list)


# ── 实盘模式 ──────────────────────────────────────────────────────────────────────

def _run_live(cfg: Config, strategy_cls: type[Strategy], symbols: list[str]) -> None:
    """启动实盘，运行前做安全检查。"""
    from cq.live.engine import LiveEngine

    # 1. 检查账号配置
    if not cfg.live.account_id:
        logger.error("config.live.account_id 未填写，请在 config/default.yaml 中配置账号")
        sys.exit(1)

    # 2. 检查是否交易日
    today = date.today()
    if today.weekday() >= 5:
        logger.warning(f"今天是{['周一','周二','周三','周四','周五','周六','周日'][today.weekday()]}，"
                       f"非交易日。如需继续请手动确认。")
        if not click.confirm("确认继续启动？"):
            sys.exit(0)

    # 3. 检查历史数据
    store = ParquetStore(cfg.data.root_path)
    _check_data_freshness(store, symbols, today)

    logger.info(f"实盘启动  账户={cfg.live.account_id}  策略={strategy_cls.__name__}  标的={symbols}")
    logger.info("提示：Ctrl-C 可安全停止，不会影响已成交订单")

    engine = LiveEngine(cfg)
    engine.add_strategy(strategy_cls(), symbols=symbols)
    engine.run()


# ── 纸上交易模式 ──────────────────────────────────────────────────────────────────

def _run_paper(
    cfg: Config,
    strategy_cls: type[Strategy],
    symbols: list[str],
    paper_start: str | None,
    paper_end: str | None,
) -> None:
    """纸上交易（历史数据回放，不下真实订单）。"""
    from cq.live.engine import LiveEngine

    if paper_start is None or paper_end is None:
        # 默认回放最近 3 个月
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=90)
    else:
        start = date.fromisoformat(paper_start)
        end = date.fromisoformat(paper_end)

    store = ParquetStore(cfg.data.root_path)
    _check_data_freshness(store, symbols, end)

    logger.info(f"纸上交易  策略={strategy_cls.__name__}  {start}→{end}  标的={symbols}")

    engine = LiveEngine(cfg)
    engine.add_strategy(strategy_cls(), symbols=symbols)
    engine.paper_trade(store=store, start_date=start, end_date=end)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────────

def _check_data_freshness(store: ParquetStore, symbols: list[str], required_end: date) -> None:
    """检查本地历史数据是否覆盖到 required_end，不足时给出下载提示。"""
    missing = []
    for symbol in symbols:
        try:
            df = store.read_bars_batch([symbol], date(2020, 1, 1), required_end, adjust="qfq")
            if df.empty:
                missing.append(symbol)
                continue
            latest = df["trade_date"].max()
            # 允许最多 3 个自然日的滞后（节假日）
            if (required_end - latest).days > 3:
                missing.append(symbol)
        except Exception:
            missing.append(symbol)

    if missing:
        logger.warning(f"以下标的历史数据不足或已过期：{missing}")
        logger.warning(
            "请先运行数据下载：\n"
            f"  python scripts/download_data.py --symbols {' '.join(missing)}"
        )
        if not click.confirm("数据可能不完整，是否继续？"):
            sys.exit(0)


def _setup_logging(cfg: Config) -> None:
    """配置 loguru 日志（控制台 + 可选文件）。"""
    logger.remove()
    logger.add(
        sys.stderr,
        level=cfg.logging.level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )
    if cfg.logging.file:
        logger.add(
            cfg.logging.file,
            level=cfg.logging.level,
            rotation=cfg.logging.rotation,
            encoding="utf-8",
        )
        logger.info(f"日志同时写入文件：{cfg.logging.file}")


if __name__ == "__main__":
    main()
