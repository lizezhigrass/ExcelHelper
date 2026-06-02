"""
core/query_base.py

查询模块抽象基类 —— 所有数据源查询器都继承此类。
支持插件发现与自注册机制。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# 全局后端注册表
BACKEND_REGISTRY: dict[str, type[QueryBase]] = {}


def register_backend(backend_type: str):
    """
    装饰器：注册插件后端类。
    
    Parameters
    ----------
    backend_type : str
        配置文件中对应 backend 的 type 值，例如 "vector_api"
    """
    def decorator(cls: type[QueryBase]):
        BACKEND_REGISTRY[backend_type] = cls
        logger.info("已注册后端插件: %s -> %s", backend_type, cls.__name__)
        return cls
    return decorator


class QueryBase(ABC):
    """查询模块抽象基类"""

    @classmethod
    @abstractmethod
    def from_config(cls, config: dict[str, Any]) -> QueryBase:
        """
        根据 YAML 配置字典实例化插件类。
        
        Parameters
        ----------
        config : dict
            对应 query_modules 中的模块配置参数
        """

    @abstractmethod
    def connect(self) -> bool:
        """建立连接，返回是否成功"""

    @abstractmethod
    def search(self, fields: dict[str, str], params: dict[str, Any]) -> tuple[list[dict], float]:
        """
        执行查询。

        Parameters
        ----------
        fields : dict
            查询字段，如 {"query": "不锈钢螺栓"}
        params : dict
            额外参数，如 {"limit": 20, "valid_only": True}

        Returns
        -------
        (results, elapsed)
            results: 展平后的结果列表 [{field: value, ...}, ...]
            elapsed: 耗时（秒）
        """

    @abstractmethod
    def get_status(self) -> str:
        """返回连接状态: ready / connecting / error"""

    @abstractmethod
    def get_available_fields(self) -> list[tuple[str, str]]:
        """返回可用的结果字段列表 [(field_name, display_name), ...]"""


def load_backends() -> None:
    """动态扫描并加载 core/query_backends/ 目录下的所有插件。

    注意：PyInstaller 打包后，pkgutil.iter_modules() 对冻结的 zipimporter
    路径无法枚举子模块，因此在冻结环境中回退到静态列表导入。
    """
    import importlib
    import pkgutil
    import sys

    package_name = "core.query_backends"

    # PyInstaller 冻结环境的静态后备列表
    # 每新增一个后端插件，须同时在此处和 .spec 的 hiddenimports 中添加
    _KNOWN_BACKENDS = [
        "core.query_backends.excel_file_query",
        "core.query_backends.qdrant_vector_query",
        "core.query_backends.sql_table_query",
    ]

    try:
        package = importlib.import_module(package_name)
    except ImportError as exc:
        logger.error("导入插件包 %s 失败: %s", package_name, exc)
        return

    # 判断是否运行于 PyInstaller 冻结环境
    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        # 冻结环境：pkgutil.iter_modules 对 zipimporter 返回空列表，使用静态列表
        logger.info("冻结环境检测到，使用静态插件列表加载...")
        modules_to_load = _KNOWN_BACKENDS
    else:
        # 普通 Python 环境：动态扫描目录
        logger.info("开始扫描并加载查询插件...")
        modules_to_load = [
            f"{package_name}.{module_name}"
            for _, module_name, is_pkg in pkgutil.iter_modules(package.__path__)
            if not is_pkg
        ]

    for full_module_name in modules_to_load:
        try:
            importlib.import_module(full_module_name)
            logger.info("已成功加载插件: %s", full_module_name)
        except Exception as exc:
            logger.error("加载插件 %s 时失败: %s", full_module_name, exc)

    logger.info("插件加载流程结束。当前支持的后端类型: %s", list(BACKEND_REGISTRY.keys()))
