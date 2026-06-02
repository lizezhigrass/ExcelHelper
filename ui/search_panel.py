"""
ui/search_panel.py

搜索面板表格操作模块 —— 带文本框的独立查询弹窗。
特性：
  - 顶部搜索栏：映射组定义的文本框，自动从 Excel 预填（列为空则跳过）
  - 快捷键触发时从 Excel 读取数据填入文本框，自动执行查询
  - 可手动修改文本框后点击「查询」按鈕（或按 Enter）
  - 文本框为空时跳过该字段（不参与检索）
  - 「正则」开关：启用时使用 PostgreSQL ~* 运算符进行正则匹配
  - 展示模式：不写入数据到 Excel
  - 底部只读文字框：点击单元格时在文字框内显示对应内容
  - 列宽记忆：关闭时保存人工调整后的列宽，下次自动载入
  - ESC 关闭，窗口不影响主程序
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal, QThread, QPoint, QRect, QSize
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QCheckBox,
    QHeaderView, QAbstractItemView, QFrame, QPlainTextEdit,
    QSplitter, QWidget, QLayout,
)

from core.mapping import MappingGroup
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


# ── 后台查询线程 ──────────────────────────────────────────────────

class _SearchWorker(QThread):
    finished = pyqtSignal(list, float)
    error    = pyqtSignal(str)

    def __init__(self, query_module, fields: dict, params: dict, parent=None):
        super().__init__(parent)
        self._qm = query_module
        self._fields = fields
        self._params = params

    def run(self):
        try:
            results, elapsed = self._qm.search(self._fields, self._params)
            self.finished.emit(results, elapsed)
        except Exception as exc:
            self.error.emit(str(exc))


# ── 搜索面板主窗口 ────────────────────────────────────────────────

class SearchPanelDialog(QDialog):

    # 用户双击某行时发出，携带该行完整数据字典
    row_selected = pyqtSignal(dict)
    """
    带搜索文本框的查询面板弹窗。

    Parameters
    ----------
    query_module    : 查询模块实例（需实现 search(fields, params)）
    mapping_group   : MappingGroup（含 display_columns, query_params）
    prefill_fields  : 从 Excel 预读的字段值 {"物料代码": "C123", ...}，
                      键与 search_box_defs 中的 field 对应，空值不填入
    search_box_defs : 搜索框定义列表：
                      [{"field": "备件名称", "label": "备件名称", "width": 160}, ...]
    title           : 弹窗标题
    """

    def __init__(
        self,
        query_module,
        mapping_group: MappingGroup,
        prefill_fields: dict[str, str],
        search_box_defs: list[dict],
        title: str = _t("历史计划查询"),
        config_manager=None,
        group_id: str = "",
        parent=None,
    ):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._qm = query_module
        self._mg = mapping_group
        self._results: list[dict] = []
        self._worker: _SearchWorker | None = None
        self._search_box_defs = search_box_defs
        self._inputs: dict[str, QLineEdit] = {}   # field -> QLineEdit
        self._config_manager = config_manager
        self._group_id = group_id

        self._setup_ui()

        # 预填数据（仅非空值）
        # 1. 建立 query_field -> ui_component 映射关系。如果 query_field 对应某个 ui_component，则将 prefill_fields 中的 key 转换为 ui_component
        mapped_prefill: dict[str, str] = {}
        for mapping in self._mg.input_mappings:
            ui_key = mapping.ui_component or mapping.query_field
            lookup_key = mapping.query_field if mapping.query_field else mapping.ui_component
            if lookup_key in prefill_fields:
                mapped_prefill[ui_key] = prefill_fields[lookup_key]
        # 2. 复制其他不属于 mapping 的字段
        for k, v in prefill_fields.items():
            if k not in mapped_prefill:
                mapped_prefill[k] = v


        for field, value in mapped_prefill.items():
            if field in self._inputs and value and str(value).strip():
                self._inputs[field].setText(str(value).strip())

        # 从配置恢复列宽
        self._load_column_widths()

        # 窗口定位
        pos = QCursor.pos()
        self.move(max(0, pos.x() - 520), max(0, pos.y() - 80))
        self.resize(1100, 580)
        # 从配置恢复窗口尺寸（覆盖默认大小）
        self._load_window_size()
        # 从配置恢复 Splitter 分割比例
        self._load_splitter_sizes()

        # 有预填内容则自动查询一次
        if any(str(v).strip() for v in prefill_fields.values() if v):
            self._do_search()

    # ── UI 构建 ──────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # ── 搜索栏（FlowLayout 响应式流式布局）────────────────────
        flow = FlowLayout()
        flow.setContentsMargins(0, 0, 0, 0)

        for sdef in self._search_box_defs:
            field = sdef.get("field", "")
            label = sdef.get("label", field)
            width = sdef.get("width", 140)

            # 将单个标签和其输入框绑定在一个不可拆分的容器 Widget 中，防止折行拆散
            container = QWidget()
            box = QHBoxLayout(container)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)  # 标签和文本框紧密排布

            lbl = QLabel(label + ":")
            lbl.setStyleSheet("font-size: 12px;")
            lbl.setFixedWidth(110)  # 固定标签宽度（从 75 增加到 110），确保中英文文字不被裁剪且完美对齐
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            inp = QLineEdit()
            inp.setPlaceholderText(_t("留空=不过滤"))
            inp.setFixedWidth(width)
            inp.setStyleSheet("font-size: 12px; padding: 3px;")
            inp.returnPressed.connect(self._do_search)
            self._inputs[field] = inp

            box.addWidget(lbl)
            box.addWidget(inp)
            flow.addWidget(container)

        layout.addLayout(flow)

        # ── 按钮栏（正则、查询、清空按钮单独一行，美观且对齐）──
        btn_line = QHBoxLayout()
        btn_line.setContentsMargins(0, 4, 0, 0)
        btn_line.setSpacing(8)

        # 正则开关
        self._regex_cb = QCheckBox(_t("正则"))
        self._regex_cb.setToolTip(_t(
            "启用后文本框内容作为正则表达式匹配（PostgreSQL ~* 运算符，大小写不敏感）\n"
            "示例：^6312  表示以6312开头；(轴承|齿轮)  表示含轴承或齿轮"
        ))
        self._regex_cb.setStyleSheet("font-size: 12px;")
        btn_line.addWidget(self._regex_cb)
        btn_line.addSpacing(8)

        # 查询按钮
        self._search_btn = QPushButton(_t("🔍 查询"))
        self._search_btn.setFixedWidth(82)
        self._search_btn.setStyleSheet(
            "font-size: 12px; background:#0078d4; color:white;"
            " border-radius:4px; padding:4px;"
        )
        self._search_btn.clicked.connect(self._do_search)
        btn_line.addWidget(self._search_btn)

        # 清空按钮
        self._clear_btn = QPushButton(_t("清空"))
        self._clear_btn.setFixedWidth(56)
        self._clear_btn.setStyleSheet("font-size: 12px; padding:4px;")
        self._clear_btn.clicked.connect(self._clear_inputs)
        btn_line.addWidget(self._clear_btn)
        btn_line.addStretch()

        layout.addLayout(btn_line)

        # ── 分隔线 ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #ddd;")
        layout.addWidget(sep)

        # ── 状态栏 ───────────────────────────────────────────────
        self._status_label = QLabel(_t("就绪 — 填写条件后点击查询"))
        self._status_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._status_label)

        # ── 结果表格 ─────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setStyleSheet("""
            QTableWidget {
                font-size: 12px;
                gridline-color: #e0e0e0;
                alternate-background-color: #f4f6f9;
            }
            QTableWidget::item:selected {
                background-color: #1a73e8;
                color: white;
            }
            QHeaderView::section {
                background-color: #eaecf0;
                padding: 4px 6px;
                font-weight: bold;
                font-size: 12px;
                border: 1px solid #ccc;
            }
        """)

        dc_list = self._mg.display_columns
        self._table.setColumnCount(len(dc_list))
        self._table.setHorizontalHeaderLabels([dc.header for dc in dc_list])
        hdr = self._table.horizontalHeader()
        for i, dc in enumerate(dc_list):
            self._table.setColumnWidth(i, dc.width)
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        # 最后一列伸展填满
        if dc_list:
            hdr.setStretchLastSection(True)

        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.cellDoubleClicked.connect(self._on_double_click)

        # ── 预览容器 Widget（用于放入 Splitter） ────────────────────
        preview_widget = QWidget()
        preview_widget.setMinimumHeight(60)
        preview_layout = QHBoxLayout(preview_widget)
        preview_layout.setSpacing(6)
        preview_layout.setContentsMargins(0, 4, 0, 0)
        cell_label = QLabel(_t("内容:"))
        cell_label.setStyleSheet("color: #555; font-size: 12px;")
        cell_label.setFixedWidth(36)
        cell_label.setAlignment(Qt.AlignTop)
        self._cell_preview = QPlainTextEdit()
        self._cell_preview.setReadOnly(True)
        self._cell_preview.setPlaceholderText(_t("点击单元格查看完整内容…"))
        self._cell_preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self._cell_preview.setStyleSheet(
            "QPlainTextEdit {"
            "  font-size: 13px; color: #222; background: #f7f8fa;"
            "  border: 1px solid #d0d0d0; border-radius: 4px;"
            "  padding: 4px 6px;"
            "}"
        )
        preview_layout.addWidget(cell_label)
        preview_layout.addWidget(self._cell_preview)

        # ── 垂直 Splitter：表格 | 预览区 ─────────────────────────
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setHandleWidth(6)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(preview_widget)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([400, 130])
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: #d0d0d0; }"
            "QSplitter::handle:hover { background: #0078d4; }"
        )
        layout.addWidget(self._splitter)

        # ── 底部提示 ───────────────────────────────────────────────
        hint = QLabel(_t("💡 双击结果行可写入 Excel 并关闭窗口  |  支持正则表达式（勾选「正则」）"))
        hint.setStyleSheet("color: #aaa; font-size: 10px;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

    # ── 查询执行 ─────────────────────────────────────────────────

    def _collect_search_fields(self) -> dict[str, str]:
        """收集文本框中的非空内容"""
        return {
            field: inp.text().strip()
            for field, inp in self._inputs.items()
            if inp.text().strip()
        }

    def _do_search(self) -> None:
        ui_fields = self._collect_search_fields()

        # 将 UI 组件的 key (ui_component) 映射回数据库查询字段的 query_field
        query_fields: dict[str, str] = {}
        ui_to_query_map: dict[str, str] = {}
        mapping_by_ui: dict[str, InputMapping] = {}
        for mapping in self._mg.input_mappings:
            ui_key = mapping.ui_component or mapping.query_field
            ui_to_query_map[ui_key] = mapping.query_field
            mapping_by_ui[ui_key] = mapping

        # 收集输入框的值与默认值
        # 1. 优先从 input_mappings 遍历，取 UI 实际值，若为空且有默认值则使用默认值
        for ui_key, mapping in mapping_by_ui.items():
            query_key = mapping.query_field
            if not query_key:
                continue
            val = ui_fields.get(ui_key, "").strip()
            if not val and getattr(mapping, "default_value", ""):
                val = mapping.default_value
            if val:
                query_fields[query_key] = val

        # 2. 补漏：处理 ui_fields 中其他没有在 input_mappings 中定义但有非空值的键
        for ui_key, val in ui_fields.items():
            if ui_key not in mapping_by_ui:
                query_key = ui_to_query_map.get(ui_key, ui_key)
                if query_key:
                    query_fields[query_key] = val

        if not query_fields:
            self._status_label.setText(_t("⚠ 请至少填写一个有效的查询条件"))
            return


        params = dict(self._mg.query_params)
        params["use_regex"] = self._regex_cb.isChecked()

        self._set_searching(True)
        self._worker = _SearchWorker(self._qm, query_fields, params)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _set_searching(self, searching: bool) -> None:
        self._search_btn.setEnabled(not searching)
        self._table.setEnabled(not searching)
        if searching:
            mode = _t("正则") if self._regex_cb.isChecked() else _t("模糊")
            self._status_label.setText(_t("正在{mode}查询…").format(mode=mode))

    def _on_done(self, results: list[dict], elapsed: float) -> None:
        self._results = results
        self._set_searching(False)
        mode = _t("（正则）") if self._regex_cb.isChecked() else ""
        self._status_label.setText(
            _t("共 {count} 条记录{mode}  |  耗时 {elapsed:.3f}s").format(count=len(results), mode=mode, elapsed=elapsed)
        )
        self._populate_table(results)

    def _on_error(self, msg: str) -> None:
        self._set_searching(False)
        # 正则语法错误给出友好提示
        if "invalid regular expression" in msg.lower() or "error" in msg.lower():
            self._status_label.setText(_t("❌ 查询失败（可能是正则语法错误）: {msg}").format(msg=msg[:120]))
        else:
            self._status_label.setText(_t("❌ 查询失败: {msg}").format(msg=msg[:120]))
        logger.error("搜索面板查询失败: %s", msg)

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

    def _clear_inputs(self) -> None:
        for inp in self._inputs.values():
            inp.clear()
        self._table.setRowCount(0)
        self._status_label.setText(_t("已清空"))
        if hasattr(self, '_cell_preview'):
            self._cell_preview.clear()

    def _on_cell_clicked(self, row: int, col: int) -> None:
        """点击单元格 → 在底部文字框显示完整内容"""
        item = self._table.item(row, col)
        if item is not None:
            self._cell_preview.setPlainText(item.text())
        else:
            self._cell_preview.clear()

    def _on_double_click(self, row: int, col: int) -> None:
        """双击某行 → 发出 row_selected 信号，携带完整数据，并关闭窗口"""
        dc_list = self._mg.display_columns
        # 优先从 _results 取（含未显示列），若排序后对不上则从表格 item 读取
        raw_row = self._table.item(row, 0)
        if raw_row is not None:
            # 通过 UserRole+2 取原始索引（_populate_table 未存储则回退到逐列读）
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

    # ── 列宽持久化 ───────────────────────────────────────────────

    def _load_column_widths(self) -> None:
        """从配置读取已保存的列宽并应用到表格"""
        if not self._config_manager or not self._group_id:
            return
        saved = self._config_manager.get_column_widths(self._group_id)
        if not saved:
            return
        dc_list = self._mg.display_columns
        hdr = self._table.horizontalHeader()
        for i, dc in enumerate(dc_list):
            if dc.header in saved:
                self._table.setColumnWidth(i, saved[dc.header])
        if dc_list:
            hdr.setStretchLastSection(True)

    def _save_column_widths(self) -> None:
        """将当前列宽保存到配置"""
        if not self._config_manager or not self._group_id:
            return
        dc_list = self._mg.display_columns
        widths = {
            dc.header: self._table.columnWidth(i)
            for i, dc in enumerate(dc_list)
        }
        self._config_manager.save_column_widths(self._group_id, widths)

    def _load_window_size(self) -> None:
        """从配置恢复窗口尺寸"""
        if not self._config_manager or not self._group_id:
            return
        size = self._config_manager.get_window_size(f"search_{self._group_id}")
        if size:
            self.resize(size[0], size[1])

    def _save_window_size(self) -> None:
        """保存当前窗口尺寸到配置"""
        if not self._config_manager or not self._group_id:
            return
        self._config_manager.save_window_size(
            f"search_{self._group_id}", self.width(), self.height()
        )

    def _load_splitter_sizes(self) -> None:
        """从配置恢复 Splitter 分割比例"""
        if not self._config_manager or not self._group_id:
            return
        sizes = self._config_manager.get_splitter_sizes(f"search_{self._group_id}")
        if sizes:
            self._splitter.setSizes(sizes)

    def _save_splitter_sizes(self) -> None:
        """保存 Splitter 分割比例到配置"""
        if not self._config_manager or not self._group_id:
            return
        self._config_manager.save_splitter_sizes(
            f"search_{self._group_id}", self._splitter.sizes()
        )

    def closeEvent(self, event) -> None:
        """关闭时保存列宽、窗口尺寸、Splitter 比例"""
        self._save_column_widths()
        self._save_window_size()
        self._save_splitter_sizes()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
