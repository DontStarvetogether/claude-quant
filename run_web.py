#!/usr/bin/env python
"""
启动 Claude Quant Web 服务。

用法：
    python run_web.py              # 开发模式（自动重载）
    python run_web.py --port 8080  # 指定端口
    python run_web.py --no-data-update  # 启动时跳过自动数据更新
    python run_web.py --prod       # 生产模式（多进程，不自动重载）
"""

import os
import sys
from pathlib import Path

# 项目根目录加入 path
sys.path.insert(0, str(Path(__file__).parent))

import click
import uvicorn


@click.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8888, type=int, help="监听端口")
@click.option("--prod", is_flag=True, default=False, help="生产模式（禁用自动重载）")
@click.option("--no-data-update", is_flag=True, default=False, help="启动时跳过自动数据更新")
def main(host: str, port: int, prod: bool, no_data_update: bool) -> None:
    if no_data_update:
        os.environ["CQ_DISABLE_DATA_UPDATE"] = "1"

    print(f"\n  Claude Quant Web  http://{host}:{port}\n")
    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        reload=not prod,
        log_level="info",
    )


if __name__ == "__main__":
    main()
