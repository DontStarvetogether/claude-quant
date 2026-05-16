"""
前复权计算。

前复权原理：以今日为基准，历史价格 × (今日adj_factor / 当日adj_factor)。
保证今日价格不变，历史除权日之前的价格向上调整，消除价格跳跃。
"""

from __future__ import annotations

from datetime import date

import pandas as pd


class PriceAdjuster:

    def apply_qfq(
        self,
        raw_df: pd.DataFrame,
        adj_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        计算前复权价格。

        raw_df：原始日线数据（含 trade_date, open, high, low, close, pre_close 等列）
        adj_df：复权因子数据（含 trade_date, adj_factor 列）

        返回前复权 DataFrame：
        - 价格列（open/high/low/close）已乘以复权系数
        - 新增 adj_factor 列（相对今日的复权系数）
        - 移除 pre_close 列（前复权后 pre_close 无实际意义）
        - 保留 limit_up / limit_down（使用原始价，不复权）
        """
        if adj_df.empty:
            # 无复权因子时，按 1.0 处理（不复权）
            df = raw_df.copy()
            df["adj_factor"] = 1.0
            return df.drop(columns=["pre_close"], errors="ignore")

        merged = raw_df.merge(adj_df[["trade_date", "adj_factor"]], on="trade_date", how="left")

        # 前向填充（adj_factor 只在除权日变化，中间日期应沿用上次的值）
        # 再回填剩余 NaN（上市初期还没有除权记录，视为 1.0）
        merged["adj_factor"] = (
            merged["adj_factor"]
            .ffill()
            .bfill()
            .fillna(1.0)
        )

        # 今日（最新日期）的复权因子作为基准
        latest_factor = adj_df["adj_factor"].iloc[-1]

        price_cols = ["open", "high", "low", "close"]
        for col in price_cols:
            merged[col] = (
                merged[col] * latest_factor / merged["adj_factor"]
            ).round(3)

        # adj_factor 列存储相对今日的复权系数（供后续重算使用）
        merged["adj_factor"] = (latest_factor / merged["adj_factor"]).round(6)

        return merged.drop(columns=["pre_close"], errors="ignore")

    def detect_split_dates(self, adj_df: pd.DataFrame) -> list[date]:
        """
        返回复权因子发生变化的日期（即除权日）。
        变化阈值 > 1e-6（过滤浮点精度噪声）。
        """
        if adj_df.empty or len(adj_df) < 2:
            return []

        changed = adj_df["adj_factor"].diff().abs() > 1e-6
        dates = adj_df.loc[changed, "trade_date"]
        return [d if isinstance(d, date) else d.date() for d in dates.tolist()]
