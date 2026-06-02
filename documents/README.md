# Excel 快捷查询助手 (Excel Quick Query Helper)

Excel 快捷查询助手是一款面向数据密集型工作流的高效后台常驻辅助系统。该应用常驻于系统托盘，监听全局自定义快捷键（如 `Ctrl + Shift + F`），能够无缝读取当前活动 Excel 工作簿中的数据，调用指定的本地或远程数据库后端执行超快速检索，并将选中的结果行以毫秒级速度安全写回 Excel 当前行。

---

## ⚡ 项目核心功能亮点

1. **全局热键唤起与极速查询**  
   常驻系统右下角托盘，全局快捷键秒级唤起数据查询面板或直接运行批量填充。
2. **多源多表融合与极速双击预览**  
   支持自由添加多个数据源（文件/目录），自动扫描多表。在设置界面，工作簿中所有 Sheet 以复选列表清晰勾选；**双击任意 Sheet** 即可极速弹出包含 ASCII 字母列标（`A`, `B`, `C`...）的高保真 20 行样例数据预览窗，极大地方便了对照配置。
3. **三列式高级列映射 (Column Mappings)**  
   映射列表升级为 3 列结构：`[选择, 列标识, 字段名]`。首列为居中复选框，勾选立即启用映射，未勾选的行在保存时将被过滤和废弃，修改和配置极为平滑。
4. **批量智能自动填充 (Auto Fill)**  
   支持单行或 Ctrl 多选不连续区，一键执行批量后台查询。循环读取单元格 -> 执行 `limit=1` 精确/模糊/向量查询 -> 自动智能写回，并在底栏实时展示“成功”、“跳过”和“失败”的行数统计。
5. **高度自适应弹性布局 (Responsive Form Tables)**  
   完美解决 Excel 列映射表、SQL 字段配置表、Qdrant 返回字段表在纵向高度被 QScrollArea 挤压的痛点，高度能够根据行数和窗口大小纵向无限自由自适应拉伸，彻底杜绝出现双嵌套滚动条。
6. **自动感知与手动切换中英文界面**  
   运行时自动获取当前操作系统习俗区域设置（中文环境使用简体中文，英文或非中文环境默认加载英文），同时在设置窗口提供手动切换下拉选单并完美将设置持久化到 `config.yaml` 配置文件中。

---

## 📦 安装与环境依赖指南

### 1. 系统要求与运行环境
- **操作系统**：Windows 7 / 10 / 11 
- **Python 版本**：Python 3.9 及以上版本

### 2. 虚拟环境搭建与依赖安装
建议使用 Python 内置的 `venv` 虚拟环境隔离项目依赖：

```powershell
# 1. 克隆或解压项目到本地目录，并进入项目根路径
cd Excel_Helper

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活虚拟环境
.venv\Scripts\activate

# 4. 安装运行所需的全部第三方依赖包
pip install -r requirements.txt
```

> [!TIP]
> 为了获得更极致的 Excel 文件查询读取性能，建议安装 Calamine 读取引擎（基于 Rust 编写），其读取速度比纯 Python 实现的 openpyxl 快 **10 到 30 倍**：
> ```powershell
> pip install python-calamine
> ```

### 3. 程序启动
确保虚拟环境激活状态下，执行以下命令即可启动常驻后台托盘：
```powershell
python main.py
```
双击右下角闪电图标即可打开“设置”控制台。

---

## ⚙ `config.yaml` 详细配置解析

项目所有模块、映射组、交互面板的配置均保存在根目录下的 `config.yaml` 文件中。以下为各核心配置节点的深度说明：

### 1. `global` 全局通用配置
```yaml
global:
  hotkey_enabled: true          # 是否启用全局键盘快捷键监听
  show_notifications: true      # 数据源自动重载或应用配置生效时是否在右下角弹出托盘气泡提示
  language: auto                # 界面语言: auto (跟随系统) | zh (简体中文) | en (English)
```

### 2. `query_modules` 数据源查询模块
定义支持的具体查询后端。系统支持 `excel_file`、`sql_table`、`qdrant_vector` 三类后端。
```yaml
query_modules:
  excel_source:                 # 模块 ID（不可重复）
    type: excel_file            # 后端类型名
    enabled: true               # 是否启用
    name: 备件表文件查询         # 模块别名
    has_header: true            # 数据源首行是否包含表头
    sheet: "备件库"             # 勾选的工作表，多表以逗号拼接，如 "备件,历史"
    max_rows: 100000            # 最大加载行数
    sources:                    # 文件与目录数据源列表
      - type: file
        path: H:/data/spare_parts.xlsx
    columns:                    # 列映射列表（留空表示返回全表字段）
      - col: A
        field: 物料代码
      - col: B
        field: 物资名称
```

### 3. `interaction_modules` 交互面板与控件布局
控制当快捷键唤起时，弹出的搜索面板或展示面板的 UI 外观与输入输出字段映射关系。
```yaml
interaction_modules:
  material_search_ui:
    name: 物料查询交互
    base_type: search_panel     # 交互基类：search_panel (搜索面板) | auto_fill (自动回写) | display_panel (展示面板) | popup_table (常规弹窗)
    components:                 # UI 面板内的输入/筛选组件
      - field: query_text
        label: 物资名称
        comp_type: text
        placeholder: 输入名称模糊检索...
      - field: limit_count
        label: 返回行数
        comp_type: number_input
        default: 20
    display_columns:            # 搜索结果表格的表头与宽度配置
      - field: 物料代码
        header: 物料编码
        width: 150
      - field: 物资名称
        header: 备件名称
        width: 250
```

---

## 🛠 后端扩展开发指南 (Backend Extension Developer Guide)

系统采用了基于 Python **动态类装饰器与注解自动发现注册机制**。开发人员无需修改主控制器核心代码，只需在指定的插件目录下放置新写的后端 Python 脚本，系统在启动时就会自动扫描、加载、并展示在设置界面的下拉选单中。

### 1. 扩展三步曲
1. **新建文件**：在 `core/query_backends/` 目录下新建一个扩展 Python 脚本（例如 `my_custom_query.py`）。
2. **继承基类**：自定义的类必须继承抽象基类 `QueryBase` (在 `core.query_base` 中定义)。
3. **添加注解**：使用 `@register_backend("你的后端标识")` 装饰器对类进行注解注册。

### 2. 接口生命周期与核心方法
新编写的查询后端类必须实现以下 6 个核心生命周期与功能接口方法：

| 方法名称 | 接收参数 | 返回值类型 | 描述说明 |
| :--- | :--- | :--- | :--- |
| `from_config(cls, config)` | `config: dict` | `Self` 实例对象 | 类工厂方法。用于解析 YAML 中该模块的参数并实例化后端。 |
| `connect(self)` | 无 | `bool` (是否成功) | 建立连接。如加载文件或执行数据库握手，将 `self._status` 更新为 `ready` / `error`。 |
| `search(self, fields, params)` | `fields: dict`, `params: dict` | `tuple[list[dict], float]` | 执行检索。接收 UI 传来的查询条件字典与附加参数，返回结果列表（键值对）及检索耗时。 |
| `get_status(self)` | 无 | `str` | 返回当前连接状态，应在 `"ready"`, `"connecting"`, `"error"` 中选择。 |
| `get_available_fields(self)` | 无 | `list[tuple[str, str]]` | 返回此数据源下立即可用的字段键及显示名称列表，供列映射下拉框显示。 |
| `refresh_fields(self)` | 无 | `list[tuple[str, str]]` | 强制重新扫描获取最新的表结构/列字段，在用户手动点击刷新时触发。 |

### 3. 标准开发脚手架模板 (Template Code)

以下为编写自定义数据源查询后端的标准代码模板：

```python
"""
core/query_backends/my_custom_query.py

自定义查询后端插件扩展示例模板。
"""

from __future__ import annotations
import logging
import time
from typing import Any

# 1. 导入基类、注册器以及国际化翻译函数
from core.query_base import QueryBase, register_backend
from core.i18n import _t

logger = logging.getLogger(__name__)

# 2. 使用注册装饰器指定后端唯一标识名（用户可在 config.yaml 里的 type 中填此名字）
@register_backend("my_custom_api")
class MyCustomQuery(QueryBase):
    """
    自定义 API 查询后端。
    """

    def __init__(self, api_url: str, timeout: int = 10):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._status = "connecting"
        
    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MyCustomQuery":
        """
        工厂方法：从配置字典解析实例化。
        """
        # 注意对缺失参数的处理以及数据类型的校验
        api_url = config.get("api_url", "")
        if not api_url:
            raise ValueError(_t("API 地址不能为空"))
        timeout = int(config.get("timeout", 10))
        return cls(api_url=api_url, timeout=timeout)

    def connect(self) -> bool:
        """
        连接阶段：在此处执行实际的网络握手或文件解析。
        """
        try:
            logger.info("正在建立与 API %s 的连接...", self.api_url)
            # 模拟连接握手...
            time.sleep(0.5) 
            
            self._status = "ready"
            logger.info("自定义 API 连接成功。")
            return True
        except Exception as exc:
            self._status = "error"
            logger.error("自定义 API 连接失败: %s", exc)
            return False

    def search(
        self,
        fields: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[list[dict], float]:
        """
        核心检索方法：当快捷键触发时运行在后台子线程中。
        
        fields : UI 界面或 Excel 行读取出的搜索键值对，例如 {"物资名称": "轴承"}
        params : limit, mode 等控制流参数
        """
        query_text = fields.get("query", "").strip()
        if not query_text:
            return [], 0.0

        # 如果连接异常，抛出带有 _t 包装的翻译异常
        if self._status != "ready":
            raise RuntimeError(_t("查询后端未就绪（状态: {status}）").format(status=self._status))

        t0 = time.perf_counter()
        
        try:
            # ── 在此编写您实际的 HTTP 请求、数据库检索或算法检索逻辑 ──
            # 示例：
            # response = requests.post(f"{self.api_url}/search", json={"q": query_text}, timeout=self.timeout)
            # response.raise_for_status()
            # data = response.json()
            
            # 以下为模拟的检索结果：
            results = [
                {"物料代码": "M1001", "物资名称": f"{query_text}001", "规格": "100mm", "score": 0.99},
                {"物料代码": "M1002", "物资名称": f"{query_text}002", "规格": "120mm", "score": 0.85},
            ]
        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 3)
            logger.error("自定义 API 检索发生错误（耗时 %.3fs）: %s", elapsed, exc)
            raise

        elapsed = round(time.perf_counter() - t0, 3)
        return results, elapsed

    def get_status(self) -> str:
        """
        返回连接状态
        """
        return self._status

    def get_available_fields(self) -> list[tuple[str, str]]:
        """
        获取可用字段列表：返回 (字段标识, 表头显示名) 的元组列表。
        这可使用户在配置列映射时，直接在下拉框里看到并勾选这些字段。
        """
        return [
            ("物料代码", "物料代码"),
            ("物资名称", "物资名称"),
            ("规格", "规格"),
            ("score", "匹配得分"),
        ]

    def refresh_fields(self) -> list[tuple[str, str]]:
        """
        手动刷新可用字段列表，可直接调用并返回 get_available_fields()。
        """
        return self.get_available_fields()
```

---

## 📜 许可

本项目遵循 GPLv3 开源许可协议（禁止任何闭源商业用途），详情请参见 LICENSE 文件。
