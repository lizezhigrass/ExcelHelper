# -*- mode: python ; coding: utf-8 -*-
"""
ExcelHelper.spec  —— PyInstaller onedir 打包配置

打包模式：onedir（目录发布）
  产物结构：
    dist/ExcelHelper/
    ├── ExcelHelper.exe          ← 主程序，双击运行
    ├── config.yaml              ← 用户将自己的配置放这里
    └── _internal/               ← 运行时依赖（PyInstaller 自动生成）

优点（相对 onefile）：
  - 启动速度快（无需每次解压到临时目录）
  - config.yaml 放在 exe 同级即可读取，便于用户直接修改

解决「查询模块未注册」的关键措施：
  1. runtime_hooks: 在进入 main.py 前强制 import 所有后端，触发 @register_backend
  2. hiddenimports: 覆盖所有 transitive 依赖，防止冻结环境 import 链断裂
  3. collect_submodules/collect_data_files: 收集 qdrant_client 全部子包和资源
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ── 收集 qdrant_client 的运行时资源（protobuf stubs / grpc）─────────
qdrant_datas  = collect_data_files('qdrant_client')
qdrant_hidden = collect_submodules('qdrant_client')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('tray_icon.ico', '.'),    # 托盘图标
        *qdrant_datas,             # qdrant_client 运行时资源
    ],
    hiddenimports=[
        # ── 所有查询后端插件（冻结环境 pkgutil 扫描失效，必须显式列出）──
        'core.query_backends.excel_file_query',
        'core.query_backends.qdrant_vector_query',
        'core.query_backends.sql_table_query',
        # ── core 子包 ──────────────────────────────────────────────
        'core',
        'core.query_base',
        'core.mapping',
        'core.excel_ops',
        'core.i18n',
        # ── PostgreSQL 驱动（可选，仅 sql_table + postgresql 需要）─
        'psycopg2',
        'psycopg2.extras',
        'psycopg2._psycopg',
        # ── qdrant_client 全部子模块 ─────────────────────────────
        *qdrant_hidden,
        # ── grpc ─────────────────────────────────────────────────
        'grpc',
        'grpc._channel',
        'grpc._server',
        'grpc._utilities',
        'grpc.experimental',
        # ── pydantic（qdrant_client 依赖）────────────────────────
        'pydantic',
        'pydantic.v1',
        'pydantic_core',
        'pydantic_core._pydantic_core',
        # ── httpx / h2（qdrant HTTP 模式）────────────────────────
        'httpx',
        'httpcore',
        'h2',
        'h2.connection',
        'h2.config',
        'hpack',
        'hyperframe',
        'hyperframe.frame',
        # ── requests（vector_api / qdrant embed）─────────────────
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        # ── openpyxl / calamine（excel_file 后端）────────────────
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        'et_xmlfile',
        'python_calamine',
        # ── xlwings COM（Excel 操作）─────────────────────────────
        'xlwings',
        'xlwings.constants',
        # ── pynput 热键（Windows 后端显式列出）───────────────────
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        # ── PyQt5 插件 ───────────────────────────────────────────
        'PyQt5',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'PyQt5.sip',
        # ── yaml ─────────────────────────────────────────────────
        'yaml',
        # ── 标准库补丁 ───────────────────────────────────────────
        'importlib.metadata',
        'importlib.resources',
        'pkg_resources',
        'pkg_resources.extern',
    ],
    # ── Runtime Hook：进入 main.py 前强制预导入所有后端 ─────────────
    runtime_hooks=['rthook_backends.py'],
    hookspath=[],
    hooksconfig={},
    excludes=[
        'matplotlib', 'scipy', 'pandas',
        'PIL', 'tkinter', 'unittest',
        'email.mime', 'http.server', 'xmlrpc',
        'notebook', 'IPython',
    ],
    noarchive=False,
    optimize=0,   # 0 = 保留 docstring，方便调试；稳定后可改为 1
)

pyz = PYZ(a.pure)

# ── onedir 模式：exe 与依赖分离 ─────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],                            # onedir: binaries/datas 由 COLLECT 收集
    exclude_binaries=True,         # onedir 标志：True = 分离模式
    name='ExcelHelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        # grpc 二进制不压缩（UPX 压缩后加载会出错）
        '_grpc*.pyd',
        'grpc*.dll',
        'libssl*.dll',
        'libcrypto*.dll',
    ],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='tray_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        '_grpc*.pyd',
        'grpc*.dll',
        'libssl*.dll',
        'libcrypto*.dll',
    ],
    name='ExcelHelper',            # 产物目录名：dist/ExcelHelper/
)
