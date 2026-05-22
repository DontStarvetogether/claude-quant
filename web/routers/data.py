"""数据管理 API"""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from cq.data.calendar import TradingCalendar
from cq.data.pipeline import DataPipeline
from cq.data.source import create_source
from cq.data.store.parquet_store import ParquetStore
from cq.utils.config import Config
from web.routers.symbols import _name_cache, _load_name_cache, _local_symbols

router = APIRouter(prefix="/api/data", tags=["data"])

# ── 请求/响应数据结构 ─────────────────────────────────────────────────────────


@dataclass
class SymbolDataInfo:
    symbol: str
    name: str
    first_date: Optional[str]
    last_date: Optional[str]
    records: int


@dataclass
class DataStatsResponse:
    total_symbols: int
    total_records: int
    disk_usage_mb: float
    latest_date: Optional[str]


class DownloadRequest(BaseModel):
    symbols: list[str]
    start_date: str = "2020-01-01"


# ── 下载任务进度存储 ──────────────────────────────────────────────────────────
# { task_id: { status, total, done, current, errors: [] } }
_download_tasks: dict[str, dict] = {}

# 后台线程池（最多 4 个并发下载任务）
_executor = ThreadPoolExecutor(max_workers=4)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _get_store() -> ParquetStore:
    config = Config.from_yaml("config/default.yaml")
    return ParquetStore(config.data.root_path)


def _build_pipeline(store: ParquetStore) -> DataPipeline:
    """构建数据下载管道（含交易日历）。"""
    config = Config.from_yaml("config/default.yaml")
    source = create_source(config.data.source)

    calendar_days = store.read_calendar("SSE")
    if not calendar_days:
        logger.warning("本地无日历，先同步日历")
        pipeline = DataPipeline(source, store, None)
        pipeline.sync_calendar("SSE")
        calendar_days = store.read_calendar("SSE")

    calendar = TradingCalendar(calendar_days) if calendar_days else None
    return DataPipeline(source, store, calendar)


def _get_symbol_info(store: ParquetStore, symbol: str) -> SymbolDataInfo:
    """读取单只股票的本地数据信息（只读日期列，速度快）。"""
    code, exchange = symbol.split(".")
    path = store._root / "bars" / exchange / code / "qfq.parquet"

    if not path.exists():
        return SymbolDataInfo(
            symbol=symbol,
            name=_name_cache.get(symbol, ""),
            first_date=None,
            last_date=None,
            records=0,
        )

    try:
        df = pd.read_parquet(path, columns=["trade_date"])
        if df.empty:
            return SymbolDataInfo(
                symbol=symbol,
                name=_name_cache.get(symbol, ""),
                first_date=None,
                last_date=None,
                records=0,
            )
        dates = pd.to_datetime(df["trade_date"]).dt.date
        return SymbolDataInfo(
            symbol=symbol,
            name=_name_cache.get(symbol, ""),
            first_date=str(dates.min()),
            last_date=str(dates.max()),
            records=len(df),
        )
    except Exception as e:
        logger.warning(f"读取 {symbol} 数据信息失败: {e}")
        return SymbolDataInfo(
            symbol=symbol,
            name=_name_cache.get(symbol, ""),
            first_date=None,
            last_date=None,
            records=0,
        )


def _calc_disk_usage_mb(store: ParquetStore) -> float:
    """计算 bars/ 目录的总磁盘占用（MB）。"""
    bars_root = store._root / "bars"
    if not bars_root.exists():
        return 0.0
    total_bytes = sum(
        f.stat().st_size
        for f in bars_root.rglob("*.parquet")
        if f.is_file()
    )
    return round(total_bytes / (1024 * 1024), 2)


# ── 接口 1：GET /api/data/stats ───────────────────────────────────────────────


@router.get("/stats")
async def get_stats() -> dict:
    """返回本地数据汇总统计（使用 parquet 元数据，避免逐文件读取）。"""
    import pyarrow.parquet as pq

    store = _get_store()

    if not _name_cache:
        _load_name_cache(store)

    symbols = _local_symbols(store)
    total_records = 0
    latest_date: Optional[str] = None
    total_bytes = 0

    bars_root = store._root / "bars"
    for sym in symbols:
        code, exchange = sym.split(".")
        path = bars_root / exchange / code / "qfq.parquet"
        if not path.exists():
            continue
        try:
            # 用 parquet 元数据获取行数，无需读取数据
            meta = pq.read_metadata(path)
            total_records += meta.num_rows
            total_bytes += path.stat().st_size

            # raw.parquet 也计入磁盘
            raw_path = bars_root / exchange / code / "raw.parquet"
            if raw_path.exists():
                total_bytes += raw_path.stat().st_size
        except Exception as e:
            logger.warning(f"统计 {sym} 时出错: {e}")

    # 最新日期：只采样最后 5 个文件（按目录名排序最大的）
    for sym in symbols[-5:]:
        code, exchange = sym.split(".")
        path = bars_root / exchange / code / "qfq.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["trade_date"])
            if not df.empty:
                dates = pd.to_datetime(df["trade_date"]).dt.date
                sym_latest = str(dates.max())
                if latest_date is None or sym_latest > latest_date:
                    latest_date = sym_latest
        except Exception:
            pass

    disk_usage_mb = round(total_bytes / (1024 * 1024), 2)

    return {
        "total_symbols": len(symbols),
        "total_records": total_records,
        "disk_usage_mb": disk_usage_mb,
        "latest_date": latest_date,
    }


# ── 接口 2：GET /api/data/symbols ─────────────────────────────────────────────


@router.get("/symbols")
async def list_symbol_data(
    page: int = Query(default=0, ge=0, description="页码，从 0 开始"),
    page_size: int = Query(default=50, ge=1, le=5000, description="每页条数"),
    q: str = Query(default="", description="按 symbol 或名称过滤"),
) -> dict:
    """分页返回每只股票的本地数据状态（名称、日期范围、记录数）。"""
    store = _get_store()

    if not _name_cache:
        _load_name_cache(store)

    symbols = _local_symbols(store)

    # 过滤
    if q:
        q_lower = q.lower()
        symbols = [
            sym for sym in symbols
            if q_lower in sym.lower() or q_lower in _name_cache.get(sym, "").lower()
        ]

    total = len(symbols)

    # 分页
    start = page * page_size
    end = start + page_size
    page_symbols = symbols[start:end]

    # 并行读取每只股票的数据信息
    infos: list[SymbolDataInfo] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_get_symbol_info, store, sym): sym for sym in page_symbols}
        # 保持顺序
        sym_to_info: dict[str, SymbolDataInfo] = {}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                sym_to_info[sym] = fut.result()
            except Exception as e:
                logger.warning(f"获取 {sym} 信息失败: {e}")
                sym_to_info[sym] = SymbolDataInfo(
                    symbol=sym,
                    name=_name_cache.get(sym, ""),
                    first_date=None,
                    last_date=None,
                    records=0,
                )
        infos = [sym_to_info[sym] for sym in page_symbols]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "symbol": info.symbol,
                "name": info.name,
                "first_date": info.first_date,
                "last_date": info.last_date,
                "records": info.records,
            }
            for info in infos
        ],
    }


# ── 接口 3：POST /api/data/download ──────────────────────────────────────────


@router.post("/download")
async def trigger_download(req: DownloadRequest) -> dict:
    """触发下载/更新指定股票数据，返回 task_id。"""
    if not req.symbols:
        raise HTTPException(status_code=400, detail="symbols 不能为空")

    task_id = str(uuid4())
    _download_tasks[task_id] = {
        "status": "pending",
        "total": len(req.symbols),
        "done": 0,
        "current": "",
        "errors": [],
        "results": [],
        "summary": {"total": len(req.symbols), "updated": 0, "cache_hit": 0, "failed": 0, "missing": 0},
    }

    # 在后台线程执行下载
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        _executor,
        _run_download_task,
        task_id,
        req.symbols,
        req.start_date,
    )

    return {"task_id": task_id}


def _run_download_task(task_id: str, symbols: list[str], start_date_str: str) -> None:
    """在后台线程中执行下载，更新 _download_tasks[task_id]。"""
    task = _download_tasks[task_id]
    task["status"] = "running"

    try:
        store = _get_store()
        pipeline = _build_pipeline(store)

        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            start_date = date(2020, 1, 1)

        end_date = date.today()

        for i, symbol in enumerate(symbols):
            task["current"] = symbol
            try:
                diagnostic = pipeline.update_symbol_diagnostic(
                    symbol,
                    end_date=end_date,
                    start_date=start_date,
                    force=False,
                ).to_dict()
                diagnostic["role"] = "trade_symbol"
                task["results"].append(diagnostic)
                if diagnostic["status"] in ("download_failed_no_cache", "empty_source"):
                    task["errors"].append(
                        f"{symbol}: {diagnostic.get('error') or diagnostic['status']}"
                    )
            except Exception as e:
                err_msg = f"{symbol}: {e}"
                logger.error(f"下载失败 {err_msg}")
                task["errors"].append(err_msg)
                local_min, local_max = store.get_available_dates(symbol, adjust="raw")
                diagnostic = {
                    "symbol": symbol,
                    "role": "trade_symbol",
                    "status": "download_failed_cache_available" if local_max else "download_failed_no_cache",
                    "new_records": 0,
                    "used_cache": local_max is not None,
                    "local_first_date": str(local_min) if local_min else None,
                    "local_last_date": str(local_max) if local_max else None,
                    "requested_start": str(start_date),
                    "requested_end": str(end_date),
                    "error": str(e),
                }
                task["results"].append(diagnostic)
            finally:
                task["done"] = i + 1
                task["summary"] = _summarize_download_results(task["results"], task["total"])

        task["status"] = "completed"
        task["current"] = ""
        logger.info(f"下载任务 {task_id} 完成，共 {len(symbols)} 只，错误 {len(task['errors'])} 只")

    except Exception as e:
        task["status"] = "failed"
        task["errors"].append(str(e))
        logger.error(f"下载任务 {task_id} 失败: {e}")


# ── 接口 4：GET /api/data/download/{task_id}/progress ────────────────────────


@router.get("/download/{task_id}/progress")
async def download_progress(task_id: str) -> StreamingResponse:
    """SSE 流式返回下载进度。"""
    if task_id not in _download_tasks:
        raise HTTPException(status_code=404, detail=f"task_id {task_id} 不存在")

    async def event_generator():
        while True:
            task = _download_tasks.get(task_id)
            if task is None:
                yield f"data: {json.dumps({'done': True, 'error': 'task not found'}, ensure_ascii=False)}\n\n"
                break

            payload = {
                "status": task["status"],
                "total": task["total"],
                "done": task["done"],
                "current": task["current"],
                "errors": task["errors"],
                "results": task.get("results", []),
                "summary": task.get("summary", {}),
            }

            if task["status"] in ("completed", "failed"):
                payload["done_flag"] = True
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                # 发送终止信号
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
                break

            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _summarize_download_results(results: list[dict], total: int) -> dict:
    updated = sum(1 for item in results if item.get("status") == "updated")
    cache_hit = sum(1 for item in results if item.get("status") == "cache_hit")
    failed = sum(1 for item in results if str(item.get("status", "")).startswith("download_failed"))
    missing = sum(1 for item in results if item.get("status") in ("download_failed_no_cache", "empty_source"))
    return {
        "total": total,
        "updated": updated,
        "cache_hit": cache_hit,
        "failed": failed,
        "missing": missing,
    }
