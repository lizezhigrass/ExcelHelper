"""
rthook_backends.py  —— PyInstaller Runtime Hook

在 exe 进入 main.py 之前，由 PyInstaller bootloader 最先执行此文件。
作用：强制导入所有查询后端插件，触发 @register_backend 装饰器，
      确保 BACKEND_REGISTRY 在 _init_query_modules() 调用前已填充完毕。

冻结环境中 pkgutil.iter_modules() 对 zipimporter 返回空，
靠此钩子绕过动态扫描的限制。
"""
import sys

# 只在冻结环境（PyInstaller exe）中执行
if getattr(sys, "frozen", False):
    _BACKENDS = [
        "core.query_backends.excel_file_query",
        "core.query_backends.sql_table_query",
        "core.query_backends.qdrant_vector_query",
    ]
    import importlib
    for _mod in _BACKENDS:
        try:
            importlib.import_module(_mod)
        except Exception as _e:
            # 不能用 logging（此时还未初始化），打印到 stderr 供调试
            print(f"[rthook] 预导入后端失败: {_mod}: {_e}", file=sys.stderr)
