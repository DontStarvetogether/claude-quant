"""FastAPI 应用入口"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.routers import backtest, data, live, strategy, symbols
from web.data_update_service import DataUpdateService
from cq.utils.config import Config

app = FastAPI(
    title="Claude Quant",
    description="A股量化回测平台",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(strategy.router)
app.include_router(backtest.router)
app.include_router(symbols.router)
app.include_router(data.router)
app.include_router(live.router)

# 静态文件（前端）
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}

# 数据更新服务实例
_update_service: DataUpdateService = None


@app.on_event("startup")
async def startup_event():
    """服务启动时启动数据更新服务"""
    global _update_service
    if os.getenv("CQ_DISABLE_DATA_UPDATE") == "1":
        return

    try:
        config = Config.from_yaml("config/default.yaml")
        _update_service = DataUpdateService(config)
        _update_service.start()
        
        # 将更新服务实例传递给 symbols router
        import web.routers.symbols as symbols_router
        symbols_router._update_service = _update_service
        
    except Exception as e:
        from loguru import logger
        logger.error(f"启动数据更新服务失败: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """服务关闭时停止数据更新服务"""
    global _update_service
    if _update_service:
        _update_service.stop()


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE_HEADERS)


@app.get("/result.html", include_in_schema=False)
async def result_page():
    return FileResponse(STATIC_DIR / "result.html", headers=_NO_CACHE_HEADERS)


@app.get("/strategies.html", include_in_schema=False)
async def strategies_page():
    return FileResponse(STATIC_DIR / "strategies.html", headers=_NO_CACHE_HEADERS)


@app.get("/data.html", include_in_schema=False)
async def data_page():
    return FileResponse(STATIC_DIR / "data.html", headers=_NO_CACHE_HEADERS)


@app.get("/compare.html", include_in_schema=False)
async def compare_page():
    return FileResponse(STATIC_DIR / "compare.html", headers=_NO_CACHE_HEADERS)


@app.get("/live.html", include_in_schema=False)
async def live_page():
    return FileResponse(STATIC_DIR / "live.html", headers=_NO_CACHE_HEADERS)
