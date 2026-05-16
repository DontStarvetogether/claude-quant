"""
数据源工厂。

用法：
    from cq.data.source import create_source
    source = create_source("akshare")   # 或 "baostock"
"""

from cq.data.source.base import DataSource


def create_source(name: str) -> DataSource:
    """
    根据名称创建数据源实例。

    支持的数据源：
      - "akshare"：akshare（推荐，免费，以 EastMoney/Sina 为后端）
      - "baostock"：baostock（备用，免费，较旧）

    使用延迟导入，未安装对应库时不会在 import 时报错。
    """
    if name == "akshare":
        from cq.data.source.akshare import AkshareSource
        return AkshareSource()
    elif name == "baostock":
        from cq.data.source.baostock import BaostockSource
        return BaostockSource()
    else:
        raise ValueError(
            f"未知数据源: {name!r}。"
            f"支持的选项：'akshare'、'baostock'"
        )
