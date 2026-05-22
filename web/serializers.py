"""BacktestResult → JSON-safe dict 转换"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from cq.engine.backtest_engine import BacktestResult
from web.schemas import (
    BacktestResultResponse,
    EquityCurveData,
    MetricsDict,
    TradeRecord,
    BenchmarkDiagnostics,
    DataDiagnostics,
)
from web.store import RunRecord


BENCHMARK_NAMES = {
    "000300.SH": "沪深300",
    "000001.SH": "上证指数",
    "399006.SZ": "创业板指",
}


def _safe_float(v: Any) -> Any:
    """处理 inf / nan → None（JSON 不支持这些值）。"""
    if v is None:
        return None
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    return v


def serialize_result(record: RunRecord) -> BacktestResultResponse:
    """将 RunRecord（含 BacktestResult）序列化为 API 响应模型。"""
    result = record.result
    if result is None:
        raise ValueError(f"run {record.run_id} 尚未完成")

    m = result.metrics

    # 计算最终持有现金和持仓市值
    final_cash = _safe_float(m.final_value) or 0.0
    final_position_value = 0.0
    
    # 从交易记录计算最终现金
    if result.trades:
        current_cash = result.initial_capital
        for t in result.trades:
            if t.side.value == 'BUY':
                current_cash -= t.net_amount
            else:
                current_cash += t.net_amount
        
        final_cash = current_cash
        final_value = _safe_float(m.final_value) or 0.0
        final_position_value = max(0.0, final_value - final_cash)
    
    # 计算总佣金和总印花税
    total_commission = 0.0
    total_stamp_tax = 0.0
    if result.trades:
        for t in result.trades:
            total_commission += t.commission
            total_stamp_tax += t.stamp_tax
    
    metrics = MetricsDict(
        total_return=_safe_float(m.total_return) or 0.0,
        annual_return=_safe_float(m.annual_return) or 0.0,
        max_drawdown=_safe_float(m.max_drawdown) or 0.0,
        volatility=_safe_float(m.volatility) or 0.0,
        sharpe_ratio=_safe_float(m.sharpe_ratio) or 0.0,
        sortino_ratio=_safe_float(m.sortino_ratio) or 0.0,
        calmar_ratio=_safe_float(m.calmar_ratio) or 0.0,
        total_trades=m.total_trades,
        win_rate=_safe_float(m.win_rate) or 0.0,
        avg_profit=_safe_float(m.avg_profit) or 0.0,
        avg_loss=_safe_float(m.avg_loss) or 0.0,
        profit_factor=_safe_float(m.profit_factor),
        avg_hold_days=_safe_float(m.avg_hold_days) or 0.0,
        total_fees=_safe_float(m.total_fees) or 0.0,
        total_commission=round(total_commission, 2),
        total_stamp_tax=round(total_stamp_tax, 2),
        final_value=_safe_float(m.final_value) or 0.0,
        final_cash=round(final_cash, 2),
        final_position_value=round(final_position_value, 2),
        initial_value=_safe_float(m.initial_value) or 0.0,
        max_drawdown_start=str(m.max_drawdown_start) if m.max_drawdown_start else None,
        max_drawdown_end=str(m.max_drawdown_end) if m.max_drawdown_end else None,
        excess_return=_safe_float(m.excess_return) or 0.0,
        alpha=_safe_float(m.alpha) or 0.0,
        beta=_safe_float(m.beta) or 0.0,
        information_ratio=_safe_float(m.information_ratio) or 0.0,
        tracking_error=_safe_float(m.tracking_error) or 0.0,
        benchmark_return=_safe_float(m.benchmark_return) or 0.0,
        benchmark_annual_return=_safe_float(m.benchmark_annual_return) or 0.0,
    )

    equity_curve = _serialize_equity_curve(result.equity_curve)

    trades = []
    current_cash = result.initial_capital  # 初始现金
    
    for t in result.trades:
        # 计算交易后的现金余额
        if t.side.value == 'BUY':
            current_cash -= t.net_amount  # 买入减少现金
        else:
            current_cash += t.net_amount  # 卖出增加现金
        
        trades.append(
            TradeRecord(
                trade_id=t.trade_id,
                symbol=t.symbol,
                side=t.side.value,
                trade_date=str(t.trade_date),
                price=round(t.price, 4),
                quantity=t.quantity,
                amount=round(t.amount, 2),
                commission=round(t.commission, 2),
                stamp_tax=round(t.stamp_tax, 2),
                net_amount=round(t.net_amount, 2),
                cash_after=round(current_cash, 2),  # 交易后持有现金
            )
        )

    # 基准曲线
    benchmark_curve = None
    if result.benchmark_curve is not None:
        benchmark_curve = _serialize_equity_curve(result.benchmark_curve)
    benchmark_status = getattr(result, "benchmark_status", None)
    if benchmark_status is None:
        if result.benchmark is None:
            benchmark_status = "not_requested"
        elif benchmark_curve is not None:
            benchmark_status = "available"
        else:
            benchmark_status = "unavailable"

    return BacktestResultResponse(
        run_id=record.run_id,
        strategy_name=result.strategy_name,
        symbols=result.symbols,
        start_date=str(result.start_date),
        end_date=str(result.end_date),
        initial_capital=result.initial_capital,
        benchmark=result.benchmark if hasattr(result, 'benchmark') else None,
        benchmark_symbol=result.benchmark if hasattr(result, 'benchmark') else None,
        benchmark_name=BENCHMARK_NAMES.get(result.benchmark) if getattr(result, "benchmark", None) else None,
        benchmark_status=benchmark_status,
        benchmark_error=getattr(result, "benchmark_error", None),
        alpha_beta_available=bool(getattr(result, "alpha_beta_available", False)),
        benchmark_curve_available=benchmark_curve is not None,
        benchmark_diagnostics=(
            BenchmarkDiagnostics(**result.benchmark_diagnostics)
            if getattr(result, "benchmark_diagnostics", None)
            else None
        ),
        data_diagnostics=(
            DataDiagnostics(**result.data_diagnostics)
            if getattr(result, "data_diagnostics", None)
            else None
        ),
        metrics=metrics,
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        trades=trades,
        rejected_count=len(result.rejected_orders),
        created_at=record.created_at.isoformat(),
    )


def _serialize_equity_curve(equity: pd.Series) -> EquityCurveData:
    """计算权益曲线和回撤数组，用于前端图表。"""
    if equity.empty:
        return EquityCurveData(dates=[], values=[], drawdown=[])

    rolling_max = equity.cummax()
    drawdown = ((equity - rolling_max) / rolling_max).fillna(0.0)

    dates = [str(d) for d in equity.index]
    values = [round(float(v), 2) for v in equity.values]
    dd_values = [round(float(d) * 100, 4) for d in drawdown.values]  # 百分比

    return EquityCurveData(dates=dates, values=values, drawdown=dd_values)
