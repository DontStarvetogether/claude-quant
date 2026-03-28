"""FastAPI 应用入口"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.routers import backtest, strategy

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

# 静态文件（前端）
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/result.html", include_in_schema=False)
async def result_page():
    return FileResponse(STATIC_DIR / "result.html")
