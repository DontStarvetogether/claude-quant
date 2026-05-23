"""股票池 API"""
from __future__ import annotations

from fastapi import APIRouter, Query

from cq.data.store.parquet_store import ParquetStore
from cq.utils.config import Config
from web.data_update_service import DataUpdateService
from web.schemas import SymbolInfo, SymbolsResponse

router = APIRouter(prefix="/api/symbols", tags=["symbols"])

# 进程级缓存：{ symbol: name }
_name_cache: dict[str, str] = {}

# 全局数据更新服务实例
_update_service: DataUpdateService = None


def _get_store() -> ParquetStore:
    config = Config.from_yaml("config/default.yaml")
    return ParquetStore(config.data.root_path)


def _load_name_cache(store: ParquetStore) -> None:
    """从 stock_info parquet 加载名称到内存缓存。"""
    global _name_cache
    df = store.read_stock_info()
    if df.empty:
        return
    # stock_info 列：symbol, name, ...
    if "symbol" in df.columns and "name" in df.columns:
        _name_cache.update(dict(zip(df["symbol"], df["name"], strict=False)))


def _local_symbols(store: ParquetStore) -> list[str]:
    """扫描 bars/ 目录，返回本地已有行情数据的 symbol 列表。"""
    bars_root = store._root / "bars"
    if not bars_root.exists():
        return []
    symbols = []
    for exchange_dir in sorted(bars_root.iterdir()):
        if not exchange_dir.is_dir():
            continue
        exchange = exchange_dir.name.upper()
        for code_dir in sorted(exchange_dir.iterdir()):
            if not code_dir.is_dir():
                continue
            if (code_dir / "qfq.parquet").exists():
                symbols.append(f"{code_dir.name}.{exchange}")
    return symbols


@router.get("", response_model=SymbolsResponse)
async def list_symbols(
    sync_names: bool = Query(default=False, description="从 AKShare 同步全量股票名称"),
) -> SymbolsResponse:
    """
    返回本地已缓存行情数据的股票列表。

    - `sync_names=true`：先从 AKShare 拉取全量股票名称并缓存，再返回。
    """
    store = _get_store()

    if sync_names or not _name_cache:
        _load_name_cache(store)

    if sync_names and not _name_cache:
        # stock_info 为空，从 AKShare 拉取并持久化
        _sync_names_from_akshare(store)

    symbols = _local_symbols(store)
    result = [
        SymbolInfo(symbol=sym, name=_name_cache.get(sym, ""))
        for sym in symbols
    ]
    return SymbolsResponse(symbols=result, total=len(result))


def _sync_names_from_akshare(store: ParquetStore) -> None:
    """从 AKShare 拉取全量 A 股代码+名称，写入 stock_info 并更新内存缓存。"""
    global _name_cache
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        # 列名：code, name（部分版本为 股票代码, 股票名称）
        col_code = df.columns[0]
        col_name = df.columns[1]
        df = df.rename(columns={col_code: "code", col_name: "name"})

        # 构造 symbol（判断交易所）
        def _to_symbol(code: str) -> str:
            if code.startswith("6") or code.startswith("5"):
                return f"{code}.SH"
            if code.startswith("4") or code.startswith("8"):
                return f"{code}.BJ"
            return f"{code}.SZ"

        df["symbol"] = df["code"].astype(str).apply(_to_symbol)
        out = df[["symbol", "name"]].copy()
        store.write_stock_info(out)
        _name_cache = dict(zip(out["symbol"], out["name"], strict=False))
    except Exception as e:
        from loguru import logger
        logger.warning(f"同步股票名称失败: {e}")


@router.post("/update")
async def trigger_update(
    update_type: str = Query(default="kline", description="更新类型: names/kline/all"),
    limit: int = Query(default=100, description="下载新股票的数量，仅当 update_type=all 时有效")
) -> dict:
    """
    手动触发数据更新。

    - `update_type=names`：立即更新股票名称
    - `update_type=kline`：立即更新K线数据
    - `update_type=all`：下载新股票（可指定 limit）
    """
    if _update_service is None:
        return {"status": "error", "message": "数据更新服务未启动"}

    try:
        if update_type == "names":
            await _update_service.trigger_update_now("names")
            return {"status": "success", "message": "股票名称更新已启动"}
        elif update_type == "kline":
            await _update_service.trigger_update_now("kline")
            return {"status": "success", "message": "K线数据更新已启动"}
        elif update_type == "all":
            await _update_service.trigger_update_now("all", limit)
            return {"status": "success", "message": f"新股票下载已启动（限制{limit}只）"}
        else:
            return {"status": "error", "message": f"不支持的更新类型: {update_type}"}
    except Exception as e:
        from loguru import logger
        logger.error(f"触发更新失败: {e}")
        return {"status": "error", "message": str(e)}
