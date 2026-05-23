"""Reproducible momentum Top-N benchmark.

The benchmark is intentionally independent from the event-driven engine. It
uses a compact pandas input/output contract so results can be compared with
external platforms before wiring the logic into richer execution flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from cq.utils.config import EngineConfig
from cq.utils.trading_rules import AStockRules


@dataclass(frozen=True)
class MomentumTopNConfig:
    """Configuration for the close-to-next-open momentum Top-N benchmark."""

    lookback: int = 20
    top_n: int = 20
    rebalance: Literal["D", "W"] = "W"
    initial_capital: float = EngineConfig.initial_capital
    commission_rate: float = EngineConfig.commission_rate
    stamp_tax_rate: float = EngineConfig.stamp_tax_rate
    min_commission: float = EngineConfig.min_commission
    lot_size: int = 100
    max_position_weight: float = 1.0


@dataclass(frozen=True)
class BenchmarkResult:
    """Standard benchmark outputs for cross-platform comparison."""

    equity_curve: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    signals: pd.DataFrame


def run_momentum_topn_benchmark(
    prices: pd.DataFrame,
    config: MomentumTopNConfig | None = None,
    *,
    date_col: str = "date",
    symbol_col: str = "symbol",
    open_col: str = "open",
    close_col: str = "close",
) -> BenchmarkResult:
    """
    Run a momentum Top-N benchmark on long-form OHLC data.

    Signals are selected at the signal date close and executed at the next
    trading date open. The returned frames are sorted and deterministic.
    """
    cfg = config or MomentumTopNConfig()
    _validate_config(cfg)
    data = _prepare_prices(prices, [date_col, symbol_col, open_col, close_col])

    date_values = list(data[date_col].drop_duplicates().sort_values())
    next_trade_date = {date_values[i]: date_values[i + 1] for i in range(len(date_values) - 1)}

    data["prev_close"] = data.groupby(symbol_col, sort=False)[close_col].shift(cfg.lookback)
    data["momentum"] = data[close_col] / data["prev_close"] - 1.0

    signal_rows, targets_by_execute_date = _build_signals(
        data=data,
        date_values=date_values,
        next_trade_date=next_trade_date,
        cfg=cfg,
        date_col=date_col,
        symbol_col=symbol_col,
    )

    open_prices = _to_price_map(data, date_col, symbol_col, open_col)
    close_prices = _to_price_map(data, date_col, symbol_col, close_col)

    cash = float(cfg.initial_capital)
    positions: dict[str, int] = {}
    last_close: dict[str, float] = {}
    trade_rows: list[dict[str, object]] = []
    holding_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []

    for trade_date in date_values:
        targets = targets_by_execute_date.get(trade_date)
        if targets is not None:
            cash = _execute_rebalance(
                trade_date=trade_date,
                targets=targets,
                positions=positions,
                cash=cash,
                open_prices=open_prices,
                last_close=last_close,
                cfg=cfg,
                trade_rows=trade_rows,
            )

        _mark_to_market(
            trade_date=trade_date,
            positions=positions,
            cash=cash,
            close_prices=close_prices,
            last_close=last_close,
            holding_rows=holding_rows,
            equity_rows=equity_rows,
        )

    return BenchmarkResult(
        equity_curve=_make_equity_frame(equity_rows),
        holdings=_make_holdings_frame(holding_rows),
        trades=_make_trades_frame(trade_rows),
        signals=_make_signals_frame(signal_rows),
    )


def _validate_config(cfg: MomentumTopNConfig) -> None:
    if cfg.lookback < 1:
        raise ValueError("lookback must be >= 1")
    if cfg.top_n < 1:
        raise ValueError("top_n must be >= 1")
    if cfg.rebalance not in {"D", "W"}:
        raise ValueError("rebalance must be 'D' or 'W'")
    if cfg.initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")
    if cfg.commission_rate < 0 or cfg.stamp_tax_rate < 0:
        raise ValueError("fee rates must be >= 0")
    if cfg.min_commission < 0:
        raise ValueError("min_commission must be >= 0")
    if cfg.lot_size < 1:
        raise ValueError("lot_size must be >= 1")
    if not 0 < cfg.max_position_weight <= 1:
        raise ValueError("max_position_weight must be in (0, 1]")


def _prepare_prices(prices: pd.DataFrame, required_columns: list[str]) -> pd.DataFrame:
    missing = [col for col in required_columns if col not in prices.columns]
    if missing:
        raise ValueError(f"prices missing required columns: {missing}")

    data = prices[required_columns].copy()
    data[required_columns[0]] = pd.to_datetime(data[required_columns[0]])
    data[required_columns[2]] = pd.to_numeric(data[required_columns[2]], errors="coerce")
    data[required_columns[3]] = pd.to_numeric(data[required_columns[3]], errors="coerce")
    data = data.sort_values([required_columns[0], required_columns[1]]).reset_index(drop=True)

    duplicate_mask = data.duplicated([required_columns[0], required_columns[1]])
    if duplicate_mask.any():
        raise ValueError("prices contains duplicate date/symbol rows")

    invalid_price = (data[required_columns[2]] <= 0) | (data[required_columns[3]] <= 0)
    if data[required_columns[2]].isna().any() or data[required_columns[3]].isna().any() or invalid_price.any():
        raise ValueError("open and close prices must be positive numbers")

    return data


def _build_signals(
    *,
    data: pd.DataFrame,
    date_values: list[pd.Timestamp],
    next_trade_date: dict[pd.Timestamp, pd.Timestamp],
    cfg: MomentumTopNConfig,
    date_col: str,
    symbol_col: str,
) -> tuple[list[dict[str, object]], dict[pd.Timestamp, list[dict[str, object]]]]:
    signal_dates = _select_signal_dates(date_values, cfg.rebalance)
    signal_rows: list[dict[str, object]] = []
    targets_by_execute_date: dict[pd.Timestamp, list[dict[str, object]]] = {}

    for signal_date in signal_dates:
        execute_date = next_trade_date.get(signal_date)
        if execute_date is None:
            continue

        daily = data.loc[
            (data[date_col] == signal_date) & data["momentum"].notna(),
            [symbol_col, "momentum"],
        ].copy()
        if daily.empty:
            continue

        daily = daily.sort_values(["momentum", symbol_col], ascending=[False, True]).head(cfg.top_n)
        target_weight = cfg.max_position_weight / len(daily)
        targets: list[dict[str, object]] = []

        for rank, row in enumerate(daily.to_dict("records"), start=1):
            target = {
                "symbol": row[symbol_col],
                "momentum": float(row["momentum"]),
                "rank": rank,
                "target_weight": target_weight,
            }
            targets.append(target)
            signal_rows.append(
                {
                    "signal_date": signal_date,
                    "execute_date": execute_date,
                    "symbol": target["symbol"],
                    "momentum": target["momentum"],
                    "rank": rank,
                    "target_weight": target_weight,
                }
            )

        targets_by_execute_date[execute_date] = targets

    return signal_rows, targets_by_execute_date


def _select_signal_dates(date_values: list[pd.Timestamp], rebalance: str) -> list[pd.Timestamp]:
    executable_signal_dates = date_values[:-1]
    if rebalance == "D":
        return executable_signal_dates

    weekly_dates = pd.DataFrame({"date": executable_signal_dates})
    if weekly_dates.empty:
        return []
    return list(weekly_dates.groupby(weekly_dates["date"].dt.to_period("W-FRI"))["date"].max())


def _to_price_map(
    data: pd.DataFrame,
    date_col: str,
    symbol_col: str,
    price_col: str,
) -> dict[tuple[pd.Timestamp, str], float]:
    return {
        (row[date_col], row[symbol_col]): float(row[price_col])
        for row in data[[date_col, symbol_col, price_col]].to_dict("records")
    }


def _execute_rebalance(
    *,
    trade_date: pd.Timestamp,
    targets: list[dict[str, object]],
    positions: dict[str, int],
    cash: float,
    open_prices: dict[tuple[pd.Timestamp, str], float],
    last_close: dict[str, float],
    cfg: MomentumTopNConfig,
    trade_rows: list[dict[str, object]],
) -> float:
    target_symbols = {str(target["symbol"]) for target in targets}
    total_assets_open = cash + sum(
        quantity * _execution_price(trade_date, symbol, open_prices, last_close)
        for symbol, quantity in positions.items()
    )
    target_value = total_assets_open * cfg.max_position_weight / len(targets)

    for symbol in sorted(list(positions)):
        if positions.get(symbol, 0) <= 0:
            continue

        price = open_prices.get((trade_date, symbol))
        if price is None:
            continue

        current_value = positions[symbol] * price
        if symbol not in target_symbols:
            sell_quantity = positions[symbol]
        else:
            excess_value = current_value - target_value
            sell_quantity = _round_to_lot(excess_value / price, cfg.lot_size)

        if sell_quantity > 0:
            cash += _sell(
                trade_date=trade_date,
                symbol=symbol,
                quantity=min(sell_quantity, positions[symbol]),
                price=price,
                positions=positions,
                cfg=cfg,
                trade_rows=trade_rows,
            )

    cash = _buy_targets(
        trade_date=trade_date,
        targets=targets,
        positions=positions,
        cash=cash,
        open_prices=open_prices,
        last_close=last_close,
        cfg=cfg,
        trade_rows=trade_rows,
    )
    return cash


def _buy_targets(
    *,
    trade_date: pd.Timestamp,
    targets: list[dict[str, object]],
    positions: dict[str, int],
    cash: float,
    open_prices: dict[tuple[pd.Timestamp, str], float],
    last_close: dict[str, float],
    cfg: MomentumTopNConfig,
    trade_rows: list[dict[str, object]],
) -> float:
    total_assets_open = cash + sum(
        quantity * _execution_price(trade_date, symbol, open_prices, last_close)
        for symbol, quantity in positions.items()
    )
    target_value = total_assets_open * cfg.max_position_weight / len(targets)

    for target in sorted(targets, key=lambda item: int(item["rank"])):
        symbol = str(target["symbol"])
        price = open_prices.get((trade_date, symbol))
        if price is None:
            continue

        current_quantity = positions.get(symbol, 0)
        desired_quantity = _round_to_lot(target_value / price, cfg.lot_size)
        buy_quantity = desired_quantity - current_quantity
        if buy_quantity <= 0:
            continue

        buy_quantity = _max_affordable_quantity(price, buy_quantity, cash, cfg)
        if buy_quantity <= 0:
            continue

        amount = price * buy_quantity
        commission = _commission(amount, cfg)
        cash -= amount + commission
        positions[symbol] = current_quantity + buy_quantity
        trade_rows.append(
            _trade_row(
                trade_date=trade_date,
                symbol=symbol,
                side="BUY",
                quantity=buy_quantity,
                price=price,
                commission=commission,
                stamp_tax=0.0,
            )
        )

    return cash


def _execution_price(
    trade_date: pd.Timestamp,
    symbol: str,
    open_prices: dict[tuple[pd.Timestamp, str], float],
    last_close: dict[str, float],
) -> float:
    return open_prices.get((trade_date, symbol), last_close.get(symbol, 0.0))


def _sell(
    *,
    trade_date: pd.Timestamp,
    symbol: str,
    quantity: int,
    price: float,
    positions: dict[str, int],
    cfg: MomentumTopNConfig,
    trade_rows: list[dict[str, object]],
) -> float:
    amount = price * quantity
    commission = _commission(amount, cfg)
    stamp_tax = round(amount * cfg.stamp_tax_rate, 2)
    positions[symbol] -= quantity
    if positions[symbol] <= 0:
        positions.pop(symbol, None)

    trade_rows.append(
        _trade_row(
            trade_date=trade_date,
            symbol=symbol,
            side="SELL",
            quantity=quantity,
            price=price,
            commission=commission,
            stamp_tax=stamp_tax,
        )
    )
    return amount - commission - stamp_tax


def _trade_row(
    *,
    trade_date: pd.Timestamp,
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    commission: float,
    stamp_tax: float,
) -> dict[str, object]:
    amount = round(price * quantity, 2)
    net_amount = amount + commission if side == "BUY" else amount - commission - stamp_tax
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": round(price, 4),
        "amount": amount,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "net_amount": round(net_amount, 2),
    }


def _mark_to_market(
    *,
    trade_date: pd.Timestamp,
    positions: dict[str, int],
    cash: float,
    close_prices: dict[tuple[pd.Timestamp, str], float],
    last_close: dict[str, float],
    holding_rows: list[dict[str, object]],
    equity_rows: list[dict[str, object]],
) -> None:
    position_values: dict[str, float] = {}
    for symbol, quantity in sorted(positions.items()):
        close = close_prices.get((trade_date, symbol), last_close.get(symbol))
        if close is None:
            continue
        last_close[symbol] = close
        position_values[symbol] = quantity * close

    position_value = sum(position_values.values())
    total_assets = cash + position_value

    for symbol, market_value in position_values.items():
        quantity = positions[symbol]
        close = last_close[symbol]
        holding_rows.append(
            {
                "date": trade_date,
                "symbol": symbol,
                "quantity": quantity,
                "close": round(close, 4),
                "market_value": round(market_value, 2),
                "weight": market_value / total_assets if total_assets > 0 else 0.0,
            }
        )

    equity_rows.append(
        {
            "date": trade_date,
            "total_assets": round(total_assets, 2),
            "cash": round(cash, 2),
            "position_value": round(position_value, 2),
        }
    )


def _commission(amount: float, cfg: MomentumTopNConfig) -> float:
    if amount <= 0:
        return 0.0
    return round(max(amount * cfg.commission_rate, cfg.min_commission), 2)


def _max_affordable_quantity(
    price: float,
    requested_quantity: int,
    cash: float,
    cfg: MomentumTopNConfig,
) -> int:
    quantity = _round_to_lot(requested_quantity, cfg.lot_size)
    while quantity > 0:
        amount = price * quantity
        if amount + _commission(amount, cfg) <= cash + 1e-6:
            return quantity
        quantity -= cfg.lot_size
    return 0


def _round_to_lot(quantity: float, lot_size: int) -> int:
    if lot_size == 100:
        return AStockRules.round_to_lot(quantity)
    return int(quantity // lot_size) * lot_size


def _make_equity_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    columns = ["date", "total_assets", "cash", "position_value"]
    return pd.DataFrame(rows, columns=columns)


def _make_holdings_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    columns = ["date", "symbol", "quantity", "close", "market_value", "weight"]
    return pd.DataFrame(rows, columns=columns)


def _make_trades_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    columns = [
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
    return pd.DataFrame(rows, columns=columns)


def _make_signals_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    columns = ["signal_date", "execute_date", "symbol", "momentum", "rank", "target_weight"]
    return pd.DataFrame(rows, columns=columns)
