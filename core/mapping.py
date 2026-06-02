"""
core/mapping.py

数据模型 + 配置管理器。

三层架构：
  交互模块 (InteractionModule)  → 窗口长什么样（组件/search_boxes/ui_controls）
  映射组   (MappingGroup)       → 数据怎么流（Excel→查询→回写）
  快捷键   (table_modules)      → 哪个快捷键触发哪个映射组
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── 数据模型 ─────────────────────────────────────────────────────

@dataclass
class InputMapping:
    """
    定义单个查询字段的来源，两种模式互斜：

    模式 A — Excel 列来源：
        columns 有内容，separator 拼接，ui_component 留空
        示例: 查询 material_code 字段，读取 Excel A 列

    模式 B — UI 组件来源：
        ui_component 填交互模块中的组件 field，columns 留空
        示例: 查询 valid_only 字段，值来自 "仅查有效" 复选框（ui_component=valid_only）
    """
    query_field:   str       = ""   # 查询 API 的字段名，如 "query"
    columns:       list[str] = field(default_factory=list)  # Excel 列标识，如 ["A", "B"]
    separator:     str       = " "  # 多列拼接分隔符
    ui_component:  str       = ""   # 交互模块中组件的 field（与 ComponentDef.field / UIControlMapping.query_field 对应）
    default_value: str       = ""   # 查询默认值，在 UI 组件和 Excel 列查询内容为空时使用

    def to_dict(self) -> dict:
        return {
            "query_field":   self.query_field,
            "columns":       self.columns,
            "separator":     self.separator,
            "ui_component":  self.ui_component,
            "default_value": self.default_value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> InputMapping:
        return cls(
            query_field   = d.get("query_field", ""),
            columns       = d.get("columns", []),
            separator     = d.get("separator", " "),
            ui_component  = d.get("ui_component", ""),
            default_value = d.get("default_value", ""),
        )


@dataclass
class OutputMapping:
    """查询结果字段 → 写入 Excel 列"""
    result_field: str          # 结果中的字段名，如 "material_code"
    column: str                # 写入 Excel 的列标识，如 "E"

    def to_dict(self) -> dict:
        return {"result_field": self.result_field, "column": self.column}

    @classmethod
    def from_dict(cls, d: dict) -> OutputMapping:
        return cls(
            result_field=d.get("result_field", ""),
            column=d.get("column", ""),
        )


@dataclass
class DisplayColumn:
    """弹窗表格中显示的列定义"""
    field: str                 # 结果字段名
    header: str                # 表头显示名
    width: int = 120           # 列宽（像素）

    def to_dict(self) -> dict:
        return {"field": self.field, "header": self.header, "width": self.width}

    @classmethod
    def from_dict(cls, d: dict) -> DisplayColumn:
        return cls(
            field=d.get("field", ""),
            header=d.get("header", ""),
            width=d.get("width", 120),
        )


@dataclass
class UIControlMapping:
    """
    弹窗中的 UI 控件映射 —— 控件状态变化时将对应值注入查询参数。

    control_type = "checkbox":
        选中时向 query_field 传 checked_value；
        未选中时传 unchecked_value（空字符串表示不传该字段）。

    control_type = "dropdown":
        options 格式: [{"label": "有效", "value": "有效"}, ...]
        用户选择某项时向 query_field 传对应 value。
        value 为空字符串表示不传该字段。
    """
    control_type: str    = "checkbox"   # "checkbox" | "dropdown"
    label:        str    = ""           # 控件标签文字
    query_field:  str    = ""           # 注入的查询字段名

    # ── checkbox 专用 ──────────────────────────────────────────
    checked_value:   Any  = True        # 选中时的值（支持 bool / str / int）
    unchecked_value: Any  = None        # 未选中时的值（None 表示不传该字段）
    default_checked: bool = True        # 默认是否选中

    # ── dropdown 专用 ──────────────────────────────────────────
    options:       list   = field(default_factory=list)  # [{"label":..., "value":...}]
    default_index: int    = 0           # 默认选中项索引

    def to_dict(self) -> dict:
        return {
            "control_type":    self.control_type,
            "label":           self.label,
            "query_field":     self.query_field,
            "checked_value":   self.checked_value,
            "unchecked_value": self.unchecked_value,
            "default_checked": self.default_checked,
            "options":         self.options,
            "default_index":   self.default_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> UIControlMapping:
        return cls(
            control_type=d.get("control_type", "checkbox"),
            label=d.get("label", ""),
            query_field=d.get("query_field", ""),
            checked_value=d.get("checked_value", True),
            unchecked_value=d.get("unchecked_value", None),
            default_checked=d.get("default_checked", True),
            options=d.get("options", []),
            default_index=d.get("default_index", 0),
        )


@dataclass
class ComponentDef:
    """
    display_panel 窗口中单个 UI 组件的定义。
    注意：数据绑定（Excel 哪列→预填哪个组件）由 InputMapping 负责，
    ComponentDef 只定义 UI 外观和行为。

    comp_type 枚举：
        text          — 普通文本框
        dropdown      — 下拉选单
        checkbox      — 复选框
        date_text     — 日期文本框（支持范围解析）
        number_text   — 数字文本框（支持范围解析）
        number_input  — 数字输入框（QDoubleSpinBox）
        date_input    — 日期输入框（QDateEdit）
    """
    label:     str
    comp_type: str              # 组件类型
    field:     str              # 后端查询字段名（与 InputMapping.query_field 对应）
    options:   list[str] = field(default_factory=list)  # dropdown 的选项列表
    default:   str       = ""  # 默认值

    def to_dict(self) -> dict:
        return {
            "label":     self.label,
            "comp_type": self.comp_type,
            "field":     self.field,
            "options":   self.options,
            "default":   self.default,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentDef":
        return cls(
            label     = d.get("label", ""),
            comp_type = d.get("comp_type", "text"),
            field     = d.get("field", ""),
            options   = d.get("options", []),
            default   = d.get("default", ""),
        )


# ── 交互模块 ─────────────────────────────────────────────────────

# 支持的基础类型
BASE_TYPES = ["popup_table", "search_panel", "auto_fill", "display_panel"]

BASE_TYPE_LABELS = {
    "popup_table":    "弹窗查询",
    "search_panel":   "搜索面板",
    "auto_fill":      "自动填充",
    "display_panel":  "自定义面板",
}


@dataclass
class InteractionModule:
    """
    交互模块 —— 定义窗口长什么样。

    base_type 决定使用哪种窗口代码：
      popup_table   → PopupTableDialog（固定布局 + ui_controls）
      search_panel  → SearchPanelDialog（固定布局 + search_boxes）
      auto_fill     → 无窗口，批量填充 + 进度条
      display_panel → DisplayPanelDialog（完全自定义 components）
    """
    name: str
    base_type: str = "display_panel"

    # ── display_panel 专用 ──────────────────────────────────────
    components: list[ComponentDef] = field(default_factory=list)

    # ── popup_table 专用 ────────────────────────────────────────
    ui_controls: list[UIControlMapping] = field(default_factory=list)

    # ── search_panel 专用 ───────────────────────────────────────
    search_boxes: list[dict] = field(default_factory=list)
    # 格式: [{"field": "xxx", "label": "xxx", "width": 140}, ...]

    # ── auto_fill 专用 ──────────────────────────────────────────
    auto_close_seconds: int = 3

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "name":      self.name,
            "base_type": self.base_type,
        }
        if self.base_type == "display_panel":
            d["components"] = [c.to_dict() for c in self.components]
        elif self.base_type == "popup_table":
            d["ui_controls"] = [u.to_dict() for u in self.ui_controls]
        elif self.base_type == "search_panel":
            d["search_boxes"] = self.search_boxes
        elif self.base_type == "auto_fill":
            d["auto_close_seconds"] = self.auto_close_seconds
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "InteractionModule":
        bt = d.get("base_type", "display_panel")
        return cls(
            name=d.get("name", ""),
            base_type=bt,
            components=[ComponentDef.from_dict(c) for c in d.get("components", [])],
            ui_controls=[UIControlMapping.from_dict(u) for u in d.get("ui_controls", [])],
            search_boxes=d.get("search_boxes", []),
            auto_close_seconds=d.get("auto_close_seconds", 3),
        )


# ── 映射组 ───────────────────────────────────────────────────────

@dataclass
class MappingGroup:
    """
    映射组 —— 定义数据怎么流。

    连接 交互模块 + 查询模块，定义：
      - 从 Excel 哪些列读取数据（input_mappings.columns）
      - 哪些查询字段来自 UI 组件（input_mappings.ui_component）
      - 查询结果如何显示（display_columns）
      - 用户选择后写回 Excel 哪些列（output_mappings）
    """
    name: str
    interaction_module: str = ""                      # 引用 InteractionModule ID
    query_module: str = ""                            # 引用查询模块 ID
    input_mappings:   list[InputMapping]          = field(default_factory=list)
    output_mappings:  list[OutputMapping]         = field(default_factory=list)
    display_columns:  list[DisplayColumn]         = field(default_factory=list)
    query_params:     dict[str, Any]              = field(default_factory=lambda: {"limit": 20})

    def to_dict(self) -> dict:
        return {
            "name":               self.name,
            "interaction_module": self.interaction_module,
            "query_module":       self.query_module,
            "input_mappings":     [m.to_dict() for m in self.input_mappings],
            "output_mappings":    [m.to_dict() for m in self.output_mappings],
            "display_columns":    [c.to_dict() for c in self.display_columns],
            "query_params":       self.query_params,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MappingGroup":
        return cls(
            name=d.get("name", ""),
            interaction_module=d.get("interaction_module", ""),
            query_module=d.get("query_module", ""),
            input_mappings=[InputMapping.from_dict(m) for m in d.get("input_mappings", [])],
            output_mappings=[OutputMapping.from_dict(m) for m in d.get("output_mappings", [])],
            display_columns=[DisplayColumn.from_dict(c) for c in d.get("display_columns", [])],
            query_params=d.get("query_params", {"limit": 20}),
        )


# ── 列标识转换工具 ───────────────────────────────────────────────

def col_letter_to_number(letter: str) -> int:
    """将列字母转为 1-based 数字: A→1, B→2, ..., Z→26, AA→27"""
    letter = letter.upper().strip()
    result = 0
    for ch in letter:
        if ch.isalpha():
            result = result * 26 + (ord(ch) - ord('A') + 1)
    return result


def col_number_to_letter(num: int) -> str:
    """将 1-based 数字转为列字母: 1→A, 26→Z, 27→AA"""
    result = ""
    while num > 0:
        num, remainder = divmod(num - 1, 26)
        result = chr(65 + remainder) + result
    return result


# ── 配置管理器 ───────────────────────────────────────────────────

class ConfigManager:
    """管理 config.yaml 的加载与保存"""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config: dict = {}
        self.load()

    def load(self) -> None:
        """从 YAML 文件加载配置"""
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
        else:
            self.config = {}
        # 确保必要的顶级键存在
        global_cfg = self.config.setdefault("global", {"hotkey_enabled": True, "language": "en"})
        
        # 读取语言设定并应用到 i18n 核心（默认载入英文版）
        from core.i18n import set_language, detect_system_lang
        saved_lang = global_cfg.get("language", "en")
        if saved_lang == "auto":
            set_language(detect_system_lang())
        else:
            set_language(saved_lang)
            
        self.config.setdefault("query_modules", {})
        self.config.setdefault("table_modules", {})
        self.config.setdefault("interaction_modules", {})
        self.config.setdefault("mapping_groups", {})
        self.config.setdefault("column_widths", {})
        self.config.setdefault("window_sizes", {})
        self.config.setdefault("splitter_sizes", {})

    def save(self) -> None:
        """将当前配置写回 YAML 文件"""
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info("配置已保存到 %s", self.config_path)

    # ── ID 生成 ──────────────────────────────────────────────────

    @staticmethod
    def generate_id(prefix: str = "") -> str:
        """生成唯一 ID：prefix + 8位十六进制"""
        return f"{prefix}{uuid.uuid4().hex[:8]}"

    # ── 交互模块 CRUD ────────────────────────────────────────────

    def get_interaction_modules(self) -> dict[str, InteractionModule]:
        """返回所有交互模块 {im_id: InteractionModule}"""
        raw = self.config.get("interaction_modules", {})
        if not isinstance(raw, dict):
            return {}
        return {k: InteractionModule.from_dict(v) for k, v in raw.items()}

    def get_interaction_module(self, im_id: str) -> InteractionModule | None:
        """获取单个交互模块"""
        raw = self.config.get("interaction_modules", {}).get(im_id)
        if raw is None:
            return None
        return InteractionModule.from_dict(raw)

    def save_interaction_module(self, im_id: str, im: InteractionModule) -> None:
        """创建或更新交互模块"""
        self.config.setdefault("interaction_modules", {})[im_id] = im.to_dict()
        self.save()

    def delete_interaction_module(self, im_id: str) -> None:
        """删除交互模块，同时清除引用"""
        ims = self.config.get("interaction_modules", {})
        ims.pop(im_id, None)
        # 清除映射组中的引用
        for mg in self.config.get("mapping_groups", {}).values():
            if isinstance(mg, dict) and mg.get("interaction_module") == im_id:
                mg["interaction_module"] = ""
        self.save()

    def get_referencing_mg_for_im(self, im_id: str) -> list[str]:
        """返回所有引用了指定交互模块的映射组 ID 列表"""
        result: list[str] = []
        for gid, mg in self.config.get("mapping_groups", {}).items():
            if isinstance(mg, dict) and mg.get("interaction_module") == im_id:
                result.append(gid)
        return result

    # ── 映射组 CRUD ──────────────────────────────────────────────

    def get_mapping_groups(self) -> dict[str, MappingGroup]:
        """返回所有映射组 {group_id: MappingGroup}"""
        raw = self.config.get("mapping_groups", {})
        if not isinstance(raw, dict):
            return {}
        return {k: MappingGroup.from_dict(v) for k, v in raw.items()}

    def get_mapping_group(self, group_id: str) -> MappingGroup | None:
        """获取单个映射组"""
        raw = self.config.get("mapping_groups", {}).get(group_id)
        if raw is None:
            return None
        return MappingGroup.from_dict(raw)

    def save_mapping_group(self, group_id: str, group: MappingGroup) -> None:
        """创建或更新映射组"""
        self.config.setdefault("mapping_groups", {})[group_id] = group.to_dict()
        self.save()

    def delete_mapping_group(self, group_id: str) -> None:
        """删除映射组"""
        groups = self.config.get("mapping_groups", {})
        groups.pop(group_id, None)
        # 同时清除引用此映射组的 table_modules
        for mod_cfg in self.config.get("table_modules", {}).values():
            if mod_cfg.get("mapping_group") == group_id:
                mod_cfg["mapping_group"] = ""
        self.save()

    def move_mapping_group(self, group_id: str, direction: int) -> bool:
        """
        上移或下移映射组。
        direction: -1 表示上移，+1 表示下移。
        成功移动返回 True，否则返回 False。
        """
        groups = self.config.get("mapping_groups", {})
        if not groups or group_id not in groups:
            return False

        keys = list(groups.keys())
        idx = keys.index(group_id)
        new_idx = idx + direction

        if new_idx < 0 or new_idx >= len(keys):
            return False  # 已经到最顶或最底

        # 交换位置
        keys[idx], keys[new_idx] = keys[new_idx], keys[idx]

        # 重新构建有序字典
        ordered_groups = {}
        for k in keys:
            ordered_groups[k] = groups[k]

        self.config["mapping_groups"] = ordered_groups
        self.save()
        return True

    # ── 列宽持久化 ───────────────────────────────────────────────

    def get_column_widths(self, group_id: str) -> dict[str, int]:
        """获取指定映射组的已保存列宽 {header: width}，无记录则返回空字典"""
        return dict(self.config.get("column_widths", {}).get(group_id, {}))

    def save_column_widths(self, group_id: str, widths: dict[str, int]) -> None:
        """保存指定映射组的列宽，合并到现有记录后写回 YAML"""
        self.config.setdefault("column_widths", {})[group_id] = widths
        self.save()

    # ── 窗口尺寸持久化 ──────────────────────────────────────────────

    def get_window_size(self, key: str) -> tuple[int, int] | None:
        """获取已保存的窗口尺寸 (width, height)，无记录返回 None"""
        data = self.config.get("window_sizes", {}).get(key)
        if isinstance(data, dict) and "width" in data and "height" in data:
            return int(data["width"]), int(data["height"])
        return None

    def save_window_size(self, key: str, width: int, height: int) -> None:
        """保存窗口尺寸并写回 YAML"""
        self.config.setdefault("window_sizes", {})[key] = {"width": width, "height": height}
        self.save()

    # ── Splitter 分割比例持久化 ──────────────────────────────────────────

    def get_splitter_sizes(self, key: str) -> list[int] | None:
        """获取已保存的 splitter 分割大小 [panel1_h, panel2_h]，无记录返回 None"""
        data = self.config.get("splitter_sizes", {}).get(key)
        if isinstance(data, list) and len(data) >= 2:
            return [int(v) for v in data]
        return None

    def save_splitter_sizes(self, key: str, sizes: list[int]) -> None:
        """保存 splitter 分割大小并写回 YAML"""
        self.config.setdefault("splitter_sizes", {})[key] = list(sizes)
        self.save()

    # ── 便捷方法 ─────────────────────────────────────────────────

    def is_hotkey_enabled(self) -> bool:
        return self.config.get("global", {}).get("hotkey_enabled", True)

    def set_hotkey_enabled(self, enabled: bool) -> None:
        self.config.setdefault("global", {})["hotkey_enabled"] = enabled
        self.save()

    def get_table_modules(self) -> dict[str, dict]:
        return self.config.get("table_modules", {})

    def get_query_modules(self) -> dict[str, dict]:
        return self.config.get("query_modules", {})

    # ── 查询模块 CRUD ────────────────────────────────────────────

    def save_query_module(self, mod_id: str, cfg: dict) -> None:
        """创建或更新单个查询模块配置，并持久化到 YAML"""
        self.config.setdefault("query_modules", {})[mod_id] = dict(cfg)
        self.save()

    def delete_query_module(self, mod_id: str) -> None:
        """
        删除查询模块，同时清理所有映射组中对该模块的引用。
        被引用映射组的 query_module 字段将被置空字符串。
        """
        qm = self.config.get("query_modules", {})
        qm.pop(mod_id, None)
        for mg in self.config.get("mapping_groups", {}).values():
            if isinstance(mg, dict) and mg.get("query_module") == mod_id:
                mg["query_module"] = ""
        self.save()

    def get_referencing_mapping_groups(self, mod_id: str) -> list[str]:
        """返回所有引用了指定查询模块的映射组 ID 列表"""
        result: list[str] = []
        for gid, mg in self.config.get("mapping_groups", {}).items():
            if isinstance(mg, dict) and mg.get("query_module") == mod_id:
                result.append(gid)
        return result
