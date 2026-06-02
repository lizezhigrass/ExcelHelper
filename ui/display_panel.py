"""
ui/display_panel.py

通用显示模组窗口（display_panel 类型表格操作模块）。

特性：
  - 顶部组件区：文本框/下拉/数字框/日期框/复选框，流式等宽布局
  - 数字文本框支持范围解析：A:B / :B / A:  → field_min / field_max
  - 日期文本框支持范围解析：同上，支持 20260310 / 2026-3-10 / 2026.3.10 等格式
  - 复选框排在所有组件之后（最右侧）
  - 结果表格 + 底部内容预览
  - 列宽/窗口尺寸/Splitter 比例持久化
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal, QThread, QDate, QRect, QPoint, QSize
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QCheckBox, QDoubleSpinBox, QDateEdit,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QPlainTextEdit,
    QSplitter, QWidget, QSizePolicy, QScrollArea, QApplication, QLayout,
)

from core.mapping import MappingGroup, ComponentDef
from core.i18n import _t

logger = logging.getLogger(__name__)


# ── 流式布局管理器 ───────────────────────────────────────────────

class FlowLayout(QLayout):
    """
    标准的 Qt 流式布局管理器。
    当父窗口水平宽度改变时，子组件将自动折行放置。
    """
    def __init__(self, parent=None, margin=0, hspacing=12, vspacing=8):
        super().__init__(parent)
        self._items = []
        self._hspacing = hspacing
        self._vspacing = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def horizontalSpacing(self):
        return self._hspacing

    def verticalSpacing(self):
        return self._vspacing

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        h_spacing = self.horizontalSpacing()
        v_spacing = self.verticalSpacing()

        for item in self._items:
            widget = item.widget()
            if widget and not widget.isVisible():
                continue
            space_x = h_spacing
            space_y = v_spacing

            item_size = item.sizeHint()
            next_x = x + item_size.width() + space_x
            if next_x - space_x > rect.right() - margins.right() and line_height > 0:
                x = rect.x() + margins.left()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()


# ── 组件类型常量 ───────────────────────────────────────────────────
COMP_TEXT         = "text"
COMP_DROPDOWN     = "dropdown"
COMP_CHECKBOX     = "checkbox"
COMP_DATE_TEXT    = "date_text"
COMP_NUMBER_TEXT  = "number_text"
COMP_NUMBER_INPUT = "number_input"
COMP_DATE_INPUT   = "date_input"

ALL_COMP_TYPES = [
    COMP_TEXT, COMP_DROPDOWN, COMP_CHECKBOX,
    COMP_DATE_TEXT, COMP_NUMBER_TEXT,
    COMP_NUMBER_INPUT, COMP_DATE_INPUT,
]

COMP_TYPE_LABELS = {
    COMP_TEXT:         "文本框",
    COMP_DROPDOWN:     "下拉选单",
    COMP_CHECKBOX:     "复选框",
    COMP_DATE_TEXT:    "日期文本框",
    COMP_NUMBER_TEXT:  "数字文本框",
    COMP_NUMBER_INPUT: "数字输入框",
    COMP_DATE_INPUT:   "日期输入框",
}


# ── 日期解析工具 ───────────────────────────────────────────────────

def _parse_date_str(s: str) -> date | None:
    """
    解析多种日期格式，返回 date 对象，失败返回 None。
    支持：20260310 / 2026-3-10 / 2026.3.10 / 2026/3/10 / 2026 3 10
    """
    s = s.strip()
    if not s:
        return None
    # 纯数字 8 位：20260310
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    # 分隔符分隔
    m = re.fullmatch(r"(\d{4})[-./\s](\d{1,2})[-./\s](\d{1,2})", s)
    if m:
        y, mo, d_ = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d_)
        except ValueError:
            return None
    return None


def _parse_date_range(text: str) -> dict[str, str]:
    """
    解析日期范围文本，返回 {"_from": iso, "_to": iso} 子集。
    格式：A:B / :B / A:
    """
    text = text.strip()
    if not text:
        return {}
    if ":" in text:
        parts = text.split(":", 1)
        lo_str, hi_str = parts[0].strip(), parts[1].strip()
        result = {}
        if lo_str:
            d = _parse_date_str(lo_str)
            if d:
                result["_from"] = d.isoformat()
        if hi_str:
            d = _parse_date_str(hi_str)
            if d:
                result["_to"] = d.isoformat()
        return result
    # 单值
    d = _parse_date_str(text)
    return {"_from": d.isoformat(), "_to": d.isoformat()} if d else {}


def _parse_number_range(text: str) -> dict[str, float]:
    """
    解析数字范围文本，返回 {"_min": val, "_max": val} 子集。
    格式：A:B / :B / A:
    """
    text = text.strip()
    if not text:
        return {}
    if ":" in text:
        parts = text.split(":", 1)
        lo_str, hi_str = parts[0].strip(), parts[1].strip()
        result = {}
        if lo_str:
            try:
                result["_min"] = float(lo_str)
            except ValueError:
                pass
        if hi_str:
            try:
                result["_max"] = float(hi_str)
            except ValueError:
                pass
        return result
    # 单值
    try:
        v = float(text)
        return {"_min": v, "_max": v}
    except ValueError:
        return {}


# ── 后台查询线程 ──────────────────────────────────────────────────

class _DisplayWorker(QThread):
    finished = pyqtSignal(list, float)
    error    = pyqtSignal(str)

    def __init__(self, query_module, fields: dict, params: dict, parent=None):
        super().__init__(parent)
        self._qm     = query_module
        self._fields = fields
        self._params = params

    def run(self):
        try:
            results, elapsed = self._qm.search(self._fields, self._params)
            self.finished.emit(results, elapsed)
        except Exception as exc:
            self.error.emit(str(exc))


# ── 通用显示模组主窗口 ────────────────────────────────────────────

class DisplayPanelDialog(QDialog):

    # 用户双击某行时发出，携带该行完整数据字典
    row_selected = pyqtSignal(dict)
    """
    通用显示模组弹窗。

    Parameters
    ----------
    query_module    : 查询模块实例
    mapping_group   : MappingGroup（含 display_columns / query_params）
    components      : list[ComponentDef] — 组件列表（来自 InteractionModule）
    prefill_fields  : 从 Excel 预读的字段值 {query_field: 拼接后的值}
    title           : 弹窗标题
    config_manager  : ConfigManager 实例（用于持久化）
    group_id        : 映射组 ID（用于持久化键）
    """

    def __init__(
        self,
        query_module,
        mapping_group: MappingGroup,
        components: list[ComponentDef] | None = None,
        prefill_fields: dict[str, str] | None = None,
        title: str = _t("查询面板"),
        config_manager=None,
        group_id: str = "",
        parent=None,
        # 兼容旧参数
        prefill_values: dict[str, str] | None = None,
    ):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._qm             = query_module
        self._mg             = mapping_group
        self._components     = components or []
        self._prefill        = prefill_fields or prefill_values or {}
        # 预填数据（仅非空值）
        # 1. 建立 query_field -> ui_component 映射关系。如果 query_field 对应某个 ui_component，则将 prefill_fields 中的 key 转换为 ui_component
        mapped_prefill: dict[str, str] = {}
        for mapping in self._mg.input_mappings:
            ui_key = mapping.ui_component or mapping.query_field
            lookup_key = mapping.query_field if mapping.query_field else mapping.ui_component
            if lookup_key in self._prefill:
                mapped_prefill[ui_key] = self._prefill[lookup_key]
        # 2. 复制其他不属于 mapping 的字段
        for k, v in self._prefill.items():
            if k not in mapped_prefill:
                mapped_prefill[k] = v
        self._prefill = mapped_prefill

        self._config_manager = config_manager
        self._group_id       = group_id
        self._worker: _DisplayWorker | None = None
        self._results: list[dict] = []

        # 控件引用：{comp_def.field: widget}
        self._widgets: dict[str, Any] = {}

        self._setup_ui()
        self._prefill_components()

        self._load_column_widths()
        self._load_window_size()
        self._load_splitter_sizes()

        # 若有预填内容则自动触发查询
        if any(str(v).strip() for v in self._prefill.values() if v):
            self._do_search()

    # ── UI 构建 ───────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # ── 顶部组件区 ───────────────────────────────────────────
        layout.addWidget(self._build_component_area())

        # ── 分隔线 ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#ddd;")
        layout.addWidget(sep)

        # ── 状态栏 ───────────────────────────────────────────────
        self._status_label = QLabel(_t("就绪 — 设置条件后点击查询"))
        self._status_label.setStyleSheet("color:#666; font-size:11px;")
        layout.addWidget(self._status_label)

        # ── 结果表格 ─────────────────────────────────────────────
        self._table = self._build_table()

        # ── 底部预览 ─────────────────────────────────────────────
        preview_w = QWidget()
        preview_w.setMinimumHeight(55)
        pl = QHBoxLayout(preview_w)
        pl.setContentsMargins(0, 2, 0, 0)
        pl.setSpacing(4)
        cell_lbl = QLabel(_t("内容:"))
        cell_lbl.setStyleSheet("color:#555; font-size:12px;")
        cell_lbl.setFixedWidth(36)
        cell_lbl.setAlignment(Qt.AlignTop)
        self._cell_preview = QPlainTextEdit()
        self._cell_preview.setReadOnly(True)
        self._cell_preview.setPlaceholderText(_t("点击单元格查看完整内容…"))
        self._cell_preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self._cell_preview.setStyleSheet(
            "QPlainTextEdit {"
            "  font-size:13px; color:#222; background:#f7f8fa;"
            "  border:1px solid #d0d0d0; border-radius:4px; padding:4px 6px;"
            "}"
        )
        pl.addWidget(cell_lbl)
        pl.addWidget(self._cell_preview)

        # ── Splitter ─────────────────────────────────────────────
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setHandleWidth(6)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(preview_w)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([430, 120])
        self._splitter.setStyleSheet(
            "QSplitter::handle{background:#d0d0d0;}"
            "QSplitter::handle:hover{background:#0078d4;}"
        )
        layout.addWidget(self._splitter, 1)

        hint = QLabel(_t("💡 双击结果行可写入 Excel 并关闭窗口（如已配置输出映射）"))
        hint.setStyleSheet("color:#aaa; font-size:10px;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

    def _build_component_area(self) -> QWidget:
        """
        构建顶部组件区：
          - 搜索组件采用流式响应式折行布局（FlowLayout）
          - 每个组件与标签绑定在不可拆分容器中
          - 底部独立一行为正则、查询、清空操作按钮，位置稳固
        """
        comps = self._components

        # 分离 checkbox 与普通组件
        normal_comps = [c for c in comps if c.comp_type != COMP_CHECKBOX]
        check_comps  = [c for c in comps if c.comp_type == COMP_CHECKBOX]

        WIDGET_W = 160

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(6)

        # FlowLayout 流式响应布局
        flow = FlowLayout(margin=0, hspacing=12, vspacing=8)

        # 1. 渲染普通搜索组件
        for comp in normal_comps:
            widget = self._make_widget(comp, WIDGET_W)
            self._widgets[comp.field] = widget
            
            # 将单个标签和其输入框绑定在一个不可拆分的容器 Widget 中，防止折行拆散
            container = QWidget()
            box = QHBoxLayout(container)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)  # 标签和输入框紧密排布
            
            lbl = QLabel(comp.label + ":")
            lbl.setStyleSheet("font-size: 12px;")
            lbl.setFixedWidth(110)  # 固定标签宽度（从 75 增加到 110），确保中英文文字不被裁剪且完美对齐
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            
            box.addWidget(lbl)
            box.addWidget(widget)
            flow.addWidget(container)

        # 2. 渲染 Checkbox 属性筛选组件
        for comp in check_comps:
            cb = QCheckBox(comp.label)
            cb.setStyleSheet("font-size: 12px;")
            if comp.default.lower() in ("true", "1", "yes", "是"):
                cb.setChecked(True)
            self._widgets[comp.field] = cb
            
            container = QWidget()
            box = QHBoxLayout(container)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)
            
            # 使用空白标签占位，使复选框与上方文本框在多列时上下对齐
            lbl_spacer = QLabel("")
            lbl_spacer.setFixedWidth(110)
            cb.setFixedWidth(WIDGET_W)
            
            box.addWidget(lbl_spacer)
            box.addWidget(cb)
            flow.addWidget(container)

        outer_layout.addLayout(flow)

        # ── 按钮栏（正则、查询、清空按钮单独一行，美观且对齐）──
        btn_line = QHBoxLayout()
        btn_line.setContentsMargins(0, 4, 0, 0)
        btn_line.setSpacing(8)

        # 正则开关
        self._regex_cb = QCheckBox(_t("正则"))
        self._regex_cb.setStyleSheet("font-size: 12px;")
        self._regex_cb.setToolTip(_t("选中后支持在文本输入框中使用正则表达式进行高级检索"))
        btn_line.addWidget(self._regex_cb)
        btn_line.addSpacing(8)

        # 查询按钮
        self._search_btn = QPushButton(_t("🔍 查询"))
        self._search_btn.setFixedWidth(84)
        self._search_btn.setStyleSheet(
            "font-size:12px; background:#0078d4; color:white;"
            " border-radius:4px; padding:4px;"
        )
        self._search_btn.clicked.connect(self._do_search)
        btn_line.addWidget(self._search_btn)

        # 清空按钮
        self._clear_btn = QPushButton(_t("清空"))
        self._clear_btn.setFixedWidth(56)
        self._clear_btn.setStyleSheet("font-size:12px; padding:4px;")
        self._clear_btn.clicked.connect(self._clear_all)
        btn_line.addWidget(self._clear_btn)

        btn_line.addStretch()
        outer_layout.addLayout(btn_line)

        return outer

    def _make_widget(self, comp: ComponentDef, width: int) -> QWidget:
        """根据组件定义创建对应 Qt 控件"""
        ct = comp.comp_type

        if ct == COMP_TEXT:
            w = QLineEdit()
            w.setPlaceholderText(_t("留空不过滤"))
            w.setFixedWidth(width)
            w.setStyleSheet("font-size:12px; padding:3px;")
            w.returnPressed.connect(self._do_search)
            if comp.default:
                w.setText(comp.default)
            return w

        if ct == COMP_DROPDOWN:
            w = QComboBox()
            w.setFixedWidth(width)
            w.setStyleSheet("font-size:12px;")
            w.addItem(_t("（全部）"), "")
            for opt in comp.options:
                w.addItem(opt, opt)
            if comp.default:
                idx = w.findData(comp.default)
                if idx >= 0:
                    w.setCurrentIndex(idx)
            return w

        if ct == COMP_DATE_TEXT:
            w = QLineEdit()
            w.setPlaceholderText(_t("如 20260101 或 2026-1-1:2026-12-31"))
            w.setFixedWidth(width)
            w.setStyleSheet("font-size:12px; padding:3px;")
            w.setToolTip(
                "支持范围：A:B（从A到B）/ :B（到B）/ A:（从A起）\n"
                "日期格式：20260101 / 2026-1-1 / 2026.1.1 / 2026/1/1"
            )
            w.returnPressed.connect(self._do_search)
            if comp.default:
                w.setText(comp.default)
            return w

        if ct == COMP_NUMBER_TEXT:
            w = QLineEdit()
            w.setPlaceholderText(_t("如 100 或 100:500 或 :500"))
            w.setFixedWidth(width)
            w.setStyleSheet("font-size:12px; padding:3px;")
            w.setToolTip(
                "支持范围：A:B（A到B）/ :B（≤B）/ A:（≥A）\n"
                "单值表示精确匹配"
            )
            w.returnPressed.connect(self._do_search)
            if comp.default:
                w.setText(comp.default)
            return w

        if ct == COMP_NUMBER_INPUT:
            w = QDoubleSpinBox()
            w.setFixedWidth(width)
            w.setStyleSheet("font-size:12px;")
            w.setRange(-1e12, 1e12)
            w.setDecimals(2)
            w.setSpecialValueText("（不过滤）")
            w.setValue(w.minimum())
            if comp.default:
                try:
                    w.setValue(float(comp.default))
                except ValueError:
                    pass
            return w

        if ct == COMP_DATE_INPUT:
            w = QDateEdit()
            w.setFixedWidth(width)
            w.setStyleSheet("font-size:12px;")
            w.setCalendarPopup(True)
            w.setDisplayFormat("yyyy-MM-dd")
            w.setSpecialValueText("（不过滤）")
            # 设置最小值为特殊值（不过滤）
            w.setDate(QDate(1900, 1, 1))
            w.setMinimumDate(QDate(1900, 1, 1))
            if comp.default:
                d = _parse_date_str(comp.default)
                if d:
                    w.setDate(QDate(d.year, d.month, d.day))
            return w

        # 兜底
        w = QLineEdit()
        w.setFixedWidth(width)
        return w

    def _build_table(self) -> QTableWidget:
        tbl = QTableWidget()
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setAlternatingRowColors(True)
        tbl.setSortingEnabled(True)
        tbl.verticalHeader().setDefaultSectionSize(24)
        tbl.setStyleSheet("""
            QTableWidget {
                font-size:12px;
                gridline-color:#e0e0e0;
                alternate-background-color:#f4f6f9;
            }
            QTableWidget::item:selected {
                background-color:#1a73e8;
                color:white;
            }
            QHeaderView::section {
                background-color:#eaecf0;
                padding:4px 6px;
                font-weight:bold;
                font-size:12px;
                border:1px solid #ccc;
            }
        """)
        dc_list = self._mg.display_columns
        tbl.setColumnCount(len(dc_list))
        tbl.setHorizontalHeaderLabels([dc.header for dc in dc_list])
        hdr = tbl.horizontalHeader()
        for i, dc in enumerate(dc_list):
            tbl.setColumnWidth(i, dc.width)
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        if dc_list:
            hdr.setStretchLastSection(True)
        tbl.cellClicked.connect(self._on_cell_clicked)
        tbl.cellDoubleClicked.connect(self._on_double_click)
        return tbl

    # ── 预填 ──────────────────────────────────────────────────────

    def _prefill_components(self) -> None:
        """根据 prefill_fields (query_field → value) 填入对应组件"""
        comps = self._components
        for comp in comps:
            value = self._prefill.get(comp.field, "").strip()
            if not value:
                continue
            widget = self._widgets.get(comp.field)
            if widget is None:
                continue
            ct = comp.comp_type
            if ct in (COMP_TEXT, COMP_DATE_TEXT, COMP_NUMBER_TEXT):
                widget.setText(value)
            elif ct == COMP_DROPDOWN:
                idx = widget.findData(value)
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            elif ct == COMP_NUMBER_INPUT:
                try:
                    widget.setValue(float(value))
                except ValueError:
                    pass
            elif ct == COMP_DATE_INPUT:
                d = _parse_date_str(value)
                if d:
                    widget.setDate(QDate(d.year, d.month, d.day))

    # ── 查询 ──────────────────────────────────────────────────────

    def _collect_query_params(self) -> dict[str, Any]:
        """
        遍历所有组件，收集非空/非默认值，构建查询参数字典。

        字段映射规则：
          text/dropdown          → {field: value}
          number_input           → {field: value}（等于特殊值则跳过）
          date_input             → {field: iso_str}（等于 1900-01-01 则跳过）
          number_text  范围      → {field_min: val, field_max: val}
          date_text    范围      → {field_from: val, field_to: val}
          checkbox               → {field: True/False}
        """
        result: dict[str, Any] = {}
        comps = self._components

        for comp in comps:
            widget = self._widgets.get(comp.field)
            if widget is None:
                continue
            ct = comp.comp_type
            field = comp.field

            if ct == COMP_TEXT:
                v = widget.text().strip()
                if v:
                    result[field] = v

            elif ct == COMP_DROPDOWN:
                v = widget.currentData()
                t = widget.currentText().strip().lower()
                is_all = False
                if t in ("全部", "all", "（全部）", "(全部)"):
                    is_all = True
                if v is not None:
                    v_str = str(v).strip().lower()
                    if v_str in ("", "全部", "all", "（全部）", "(全部)"):
                        is_all = True
                if not is_all and v:
                    result[field] = v

            elif ct == COMP_CHECKBOX:
                result[field] = widget.isChecked()

            elif ct == COMP_NUMBER_TEXT:
                v = widget.text().strip()
                if v:
                    parsed = _parse_number_range(v)
                    if "_min" in parsed:
                        result[f"{field}_min"] = parsed["_min"]
                    if "_max" in parsed:
                        result[f"{field}_max"] = parsed["_max"]

            elif ct == COMP_DATE_TEXT:
                v = widget.text().strip()
                if v:
                    parsed = _parse_date_range(v)
                    if "_from" in parsed:
                        result[f"{field}_from"] = parsed["_from"]
                    if "_to" in parsed:
                        result[f"{field}_to"] = parsed["_to"]

            elif ct == COMP_NUMBER_INPUT:
                # 特殊值（最小值）表示不过滤
                if widget.value() != widget.minimum():
                    result[field] = widget.value()

            elif ct == COMP_DATE_INPUT:
                qd = widget.date()
                if qd != QDate(1900, 1, 1):
                    result[field] = qd.toString("yyyy-MM-dd")

        return result

    def _do_search(self) -> None:
        params = dict(self._mg.query_params)
        params["use_regex"] = self._regex_cb.isChecked()
        comp_params = self._collect_query_params()

        # 1. 建立 UI 组件到 InputMapping 的映射关系
        mapping_by_ui: dict[str, InputMapping] = {}
        ui_to_query_map: dict[str, str] = {}
        for mapping in self._mg.input_mappings:
            ui_key = mapping.ui_component or mapping.query_field
            mapping_by_ui[ui_key] = mapping
            ui_to_query_map[ui_key] = mapping.query_field

        # 2. 对每个 input_mapping，如果其在 comp_params 中不存在或为空，且有 default_value，则填充
        for ui_key, mapping in mapping_by_ui.items():
            if getattr(mapping, "default_value", ""):
                # 检查普通和范围形式的 key 是否在 comp_params 中有非空值
                has_val = False
                for suffix in ("", "_min", "_max", "_from", "_to"):
                    k = ui_key + suffix
                    if k in comp_params and comp_params[k] not in (None, ""):
                        has_val = True
                        break
                if not has_val:
                    comp_params[ui_key] = mapping.default_value

        # 3. 区分 fields（文本匹配）和 params（过滤条件）
        # 约定：text 类型→ fields，其余→ params["filters"]
        fields: dict[str, str] = {}
        filters: dict[str, Any] = {}

        comps = self._components
        text_fields = {c.field for c in comps if c.comp_type == COMP_TEXT}

        for k, v in comp_params.items():
            base_field = k
            suffix = ""
            for sfx in ("_min", "_max", "_from", "_to"):
                if k.endswith(sfx):
                    base_field = k[:-len(sfx)]
                    suffix = sfx
                    break

            query_base = ui_to_query_map.get(base_field, base_field)
            if not query_base:  # 提示字段：不作为查询
                continue

            query_key = query_base + suffix
            if base_field in text_fields:
                fields[query_key] = str(v)
            else:
                filters[query_key] = v

        # 若 text 组件提供了内容，用第一个作为主查询字段
        if not fields:
            for c in comps:
                if c.comp_type == COMP_TEXT:
                    w = self._widgets.get(c.field)
                    if w and w.text().strip():
                        query_key = ui_to_query_map.get(c.field, c.field)
                        if query_key:
                            fields[query_key] = w.text().strip()
                        break

        # 没有任何查询条件时，将 filters 全部发给后端
        if filters:
            params["filters"] = filters

        self._set_searching(True)
        self._worker = _DisplayWorker(self._qm, fields or {"query": ""}, params)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _set_searching(self, busy: bool) -> None:
        self._search_btn.setEnabled(not busy)
        self._table.setEnabled(not busy)
        if busy:
            self._status_label.setText(_t("⏳ 正在查询…"))

    def _on_done(self, results: list[dict], elapsed: float) -> None:
        self._results = results
        self._set_searching(False)
        self._status_label.setText(
            _t("共 {count} 条记录  |  耗时 {elapsed:.3f}s").format(count=len(results), elapsed=elapsed)
        )
        self._populate_table(results)

    def _on_error(self, msg: str) -> None:
        self._set_searching(False)
        self._status_label.setText(_t("❌ 查询失败: {msg}").format(msg=msg[:150]))
        logger.error("display_panel 查询失败: %s", msg)

    def _populate_table(self, results: list[dict]) -> None:
        dc_list = self._mg.display_columns
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(results))
        for row_idx, record in enumerate(results):
            for col_idx, dc in enumerate(dc_list):
                value = record.get(dc.field, "")
                text = str(value) if value not in (None, "") else ""
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                # 第 0 列存储原始索引，供排序后双击时正确还原数据
                if col_idx == 0:
                    item.setData(Qt.UserRole + 2, row_idx)
                self._table.setItem(row_idx, col_idx, item)
        self._table.setSortingEnabled(True)

    def _clear_all(self) -> None:
        for comp in self._components:
            widget = self._widgets.get(comp.field)
            if widget is None:
                continue
            ct = comp.comp_type
            if ct in (COMP_TEXT, COMP_DATE_TEXT, COMP_NUMBER_TEXT):
                widget.clear()
            elif ct == COMP_DROPDOWN:
                widget.setCurrentIndex(0)
            elif ct == COMP_CHECKBOX:
                widget.setChecked(False)
            elif ct == COMP_NUMBER_INPUT:
                widget.setValue(widget.minimum())
            elif ct == COMP_DATE_INPUT:
                widget.setDate(QDate(1900, 1, 1))
        self._table.setRowCount(0)
        self._regex_cb.setChecked(False)
        if hasattr(self, "_cell_preview"):
            self._cell_preview.clear()
        self._status_label.setText(_t("已清空"))

    def _on_cell_clicked(self, row: int, col: int) -> None:
        item = self._table.item(row, col)
        self._cell_preview.setPlainText(item.text() if item else "")

    def _on_double_click(self, row: int, col: int) -> None:
        """双击某行 → 发出 row_selected 信号，携带完整数据，并关闭窗口"""
        dc_list = self._mg.display_columns
        raw_row = self._table.item(row, 0)
        if raw_row is not None:
            orig_idx = raw_row.data(Qt.UserRole + 2)
            if orig_idx is not None and 0 <= int(orig_idx) < len(self._results):
                data = dict(self._results[int(orig_idx)])
            else:
                data = {
                    dc.field: (self._table.item(row, i).text()
                               if self._table.item(row, i) else "")
                    for i, dc in enumerate(dc_list)
                }
        else:
            data = {}
        if data:
            self.row_selected.emit(data)
        self.close()

    # ── 持久化 ────────────────────────────────────────────────────

    def _load_column_widths(self) -> None:
        if not self._config_manager or not self._group_id:
            return
        saved = self._config_manager.get_column_widths(self._group_id)
        if not saved:
            return
        for i, dc in enumerate(self._mg.display_columns):
            if dc.header in saved:
                self._table.setColumnWidth(i, saved[dc.header])

    def _save_column_widths(self) -> None:
        if not self._config_manager or not self._group_id:
            return
        widths = {
            dc.header: self._table.columnWidth(i)
            for i, dc in enumerate(self._mg.display_columns)
        }
        self._config_manager.save_column_widths(self._group_id, widths)

    def _load_window_size(self) -> None:
        if not self._config_manager or not self._group_id:
            return
        size = self._config_manager.get_window_size(f"display_{self._group_id}")
        if size:
            self.resize(size[0], size[1])
        else:
            self.resize(1100, 620)

    def _save_window_size(self) -> None:
        if not self._config_manager or not self._group_id:
            return
        self._config_manager.save_window_size(
            f"display_{self._group_id}", self.width(), self.height()
        )

    def _load_splitter_sizes(self) -> None:
        if not self._config_manager or not self._group_id:
            return
        sizes = self._config_manager.get_splitter_sizes(f"display_{self._group_id}")
        if sizes:
            self._splitter.setSizes(sizes)

    def _save_splitter_sizes(self) -> None:
        if not self._config_manager or not self._group_id:
            return
        self._config_manager.save_splitter_sizes(
            f"display_{self._group_id}", self._splitter.sizes()
        )

    # ── 事件 ──────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        self._save_column_widths()
        self._save_window_size()
        self._save_splitter_sizes()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
