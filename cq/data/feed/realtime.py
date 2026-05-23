"""
实时行情 Feed。

RealtimeFeed：抽象基类，定义订阅/回调接口。
QMTRealtimeFeed：基于迅投 QMT (xtquant) 的实现。

收到 Bar 时调用 _bar_callback，由 LiveEngine 将其放入线程安全队列再推入事件总线，
保证策略 on_bar() 始终在主线程中执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime

from loguru import logger

from cq.core.models import Bar


class RealtimeFeed(ABC):
    """实时行情 Feed 基类。"""

    def __init__(self) -> None:
        self._bar_callback: Callable[[Bar], None] | None = None

    def set_bar_callback(self, callback: Callable[[Bar], None]) -> None:
        """设置 Bar 到达时的回调函数（在行情线程中调用，注意线程安全）。"""
        self._bar_callback = callback

    @abstractmethod
    def subscribe(self, symbols: list[str]) -> None:
        """订阅标的实时行情。"""

    @abstractmethod
    def unsubscribe(self, symbols: list[str]) -> None:
        """取消订阅。"""

    @abstractmethod
    def start(self) -> None:
        """启动行情接收（阻塞，直到 stop() 被调用）。"""

    @abstractmethod
    def stop(self) -> None:
        """停止行情接收并取消所有订阅。"""

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Bar | None:
        """返回最新一根 Bar，无数据时返回 None。"""


class QMTRealtimeFeed(RealtimeFeed):
    """
    基于迅投 QMT (xtquant) 的实时行情 Feed。

    依赖：pip install xtquant（仅在 QMT 客户端环境内可用）

    使用示例：
        feed = QMTRealtimeFeed(data_dir="C:/qmt/userdata_mini", period="1d")
        feed.set_bar_callback(lambda bar: bar_queue.put(bar))
        feed.subscribe(["600519.SH", "000858.SZ"])
        feed.start()   # 阻塞，行情线程持续推送

    参数说明：
        data_dir：QMT 数据目录（同 XtQuantTrader 的 path 参数）
        period：行情周期，"1d" 为日线（盘中实时合成），"1m" 为分钟线
    """

    def __init__(
        self,
        data_dir: str = ".",
        period: str = "1d",
    ) -> None:
        super().__init__()
        self._st_cache: set[str] = set()
        self._st_loaded = False
        try:
            from xtquant import xtdata as _xtdata  # type: ignore[import-not-found]
            self._xtdata = _xtdata
        except ImportError as exc:
            raise ImportError(
                "请在 QMT 客户端环境中安装 xtquant：pip install xtquant"
            ) from exc

        self._data_dir = data_dir
        self._period = period
        self._latest_bars: dict[str, Bar] = {}
        self._subscribed: set[str] = set()

    # ── 公共接口 ─────────────────────────────────────────────────────────────────

    def subscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            self._xtdata.subscribe_quote(
                stock_code=symbol,
                period=self._period,
                count=0,          # 0 = 只推送实时，不下载历史
                callback=self._on_quote,
            )
            self._subscribed.add(symbol)
        logger.info(f"订阅实时行情: {symbols}  period={self._period}")

    def unsubscribe(self, symbols: list[str]) -> None:
        for symbol in symbols:
            self._xtdata.unsubscribe_quote(symbol, self._period, self._on_quote)
            self._subscribed.discard(symbol)

    def _is_st(self, symbol: str) -> bool:
        """判断股票是否为 ST。优先用 QMT 详情，回退到 akshare ST 列表。"""
        if not self._st_loaded:
            self._load_st_cache()
        code = symbol.split(".")[0] if "." in symbol else symbol
        return code in self._st_cache

    def _load_st_cache(self) -> None:
        """加载 ST 股列表（纯数字代码，不含交易所后缀）。"""
        self._st_loaded = True
        try:
            from cq.data.source.akshare import AkshareSource
            if AkshareSource._st_codes_cache is not None:
                self._st_cache = AkshareSource._st_codes_cache
            else:
                checker = AkshareSource()
                checker._load_st_codes()
                self._st_cache = checker._st_codes_cache or set()
        except Exception:
            logger.warning("无法加载 ST 股列表，涨跌停将统一按 ±10% 计算")

    def start(self) -> None:
        """阻塞，持续接收行情推送（在独立线程中调用）。"""
        logger.info("实时行情 Feed 启动，等待推送…")
        self._xtdata.run()

    def stop(self) -> None:
        self.unsubscribe(list(self._subscribed))
        logger.info("实时行情 Feed 已停止")

    def get_latest_bar(self, symbol: str) -> Bar | None:
        return self._latest_bars.get(symbol)

    # ── 内部方法 ─────────────────────────────────────────────────────────────────

    def _on_quote(self, datas: dict) -> None:
        """
        QMT 行情回调（在 QMT 网络线程中调用）。

        datas 格式：{symbol: {field_name: [values]}}
        """
        for symbol, fields in datas.items():
            bar = self._parse_bar(symbol, fields)
            if bar is None:
                continue
            self._latest_bars[symbol] = bar
            if self._bar_callback:
                self._bar_callback(bar)

    def _parse_bar(self, symbol: str, fields: dict) -> Bar | None:
        """将 QMT 推送的字段字典转为 Bar。"""
        try:
            def last(key: str, default=0):
                v = fields.get(key, default)
                if isinstance(v, (list, tuple)):
                    return v[-1] if v else default
                return v if v is not None else default

            ts = last("time")
            if not ts:
                return None
            trade_date = datetime.fromtimestamp(ts / 1000).date()

            close = float(last("close"))
            pre_close = float(last("lastClose", close))
            is_st = self._is_st(symbol)
            pct = 0.05 if is_st else 0.10
            limit_up = round(pre_close * (1 + pct), 2)
            limit_down = round(pre_close * (1 - pct), 2)

            return Bar(
                symbol=symbol,
                trade_date=trade_date,
                open=float(last("open", close)),
                high=float(last("high", close)),
                low=float(last("low", close)),
                close=close,
                volume=int(last("volume")),
                amount=float(last("amount")),
                limit_up=limit_up,
                limit_down=limit_down,
                pre_close=pre_close,
                is_st=is_st,
            )
        except (KeyError, TypeError, ValueError, IndexError) as e:
            logger.warning(f"解析行情数据失败 {symbol}: {e}")
            return None
