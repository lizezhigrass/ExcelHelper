"""
ui/popup_table.py

弹窗结果表格 —— 显示查询结果，支持双击选中回写。
特性：
  - 始终置顶，出现在鼠标附近
  - 顶部搜索栏：自动带入查询文本，可手动修改后点「🔍 查询」重新查询
  - 动态 UI 控件（来自映射组 ui_controls）：复选框、下拉框
    每次控件状态改变，将所有控件当前值收集为 dict 发出 requery_requested(dict)
  - 表格支持点击排序
  - 双击行 → 回写 → 关闭
  - 底部只读文字框：点击单元格时显示单元格内容
  - 列宽记忆：关闭时保存人工调整后的列宽，下次自动载入
  - ESC 关闭
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QCheckBox, QComboBox, QFrame,
    QHeaderView, QAbstractItemView, QPlainTextEdit, QSplitter, QWidget,
)

from core.mapping import DisplayColumn, UIControlMapping
from core.i18n import _t


class PopupTableDialog(QDialog):
    """查询结果弹窗表格"""

    # 控件状态变化 → 重新查询（携带控件参数 dict）
    requery_requested = pyqtSignal(dict)
    # 用户手动修改文本后点查询 → 重新查询（携带新查询文本 str + 控件参数 dict）
    text_requery_requested = pyqtSignal(str, dict)
    # 用户选择了一行
    row_selected = pyqtSignal(dict)

    def __init__(
        self,
        query_text: str,
        results: list[dict],
        display_columns: list[DisplayColumn],
        ui_controls: list[UIControlMapping],
        elapsed: float = 0.0,
        config_manager=None,
        group_id: str = "",
        parent=None,
    ):
        super().__init__(parent, Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle(_t("查询结果"))
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._results = results
        self._display_columns = display_columns
        self._ui_controls = ui_controls
        self._query_text = query_text
        self._config_manager = config_manager
        self._group_id = group_id

        self._control_widgets: list[QCheckBox | QComboBox] = []

        self._setup_ui(query_text, elapsed)
        self._populate_table(results)

        # 从配置恢复列宽
        self._load_column_widths()

        pos = QCursor.pos()
        self.move(max(0, pos.x() - 400), max(0, pos.y() - 50))
        self.resize(960, 560)
        # 从配置恢复窗口尺寸（覆盖默认大小）
        self._load_window_size()
        # 从配置恢复 Splitter 分割比例
        self._load_splitter_sizes()

    # ── UI 构建 ──────────────────────────────────────────────────

    def _setup_ui(self, query_text: str, elapsed: float) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        # ── 搜索栏（文本框 + 查询按钮） ─────────────────────────
        search_bar = QHBoxLayout()
        search_bar.setSpacing(6)

        search_bar.addWidget(QLabel(_t("查询:")))
        self._query_input = QLineEdit(query_text)
        self._query_input.setStyleSheet("font-size: 12px; padding: 3px;")
        self._query_input.setPlaceholderText(_t("输入查询内容后按 Enter 或点击查询按钮"))
        self._query_input.returnPressed.connect(self._on_text_search)
        search_bar.addWidget(self._query_input, 1)

        self._search_btn = QPushButton(_t("🔍 查询"))
        self._search_btn.setFixedWidth(80)
        self._search_btn.setStyleSheet(
            "font-size: 12px; background:#0078d4; color:white;"
            " border-radius:4px; padding:4px;"
        )
        self._search_btn.clicked.connect(self._on_text_search)
        search_bar.addWidget(self._search_btn)

        layout.addLayout(search_bar)

        # ── 状态栏 + UI 控件 ─────────────────────────────────────
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(8)

        self._info_label = QLabel()
        self._info_label.setStyleSheet("color: #555; font-size: 11px;")
        self._update_info(query_text, len(self._results), elapsed)
        ctrl_bar.addWidget(self._info_label, 1)

        # 动态渲染 UI 控件
        self._control_widgets = []
        for ctrl in self._ui_controls:
            if ctrl.control_type == "checkbox":
                cb = QCheckBox(ctrl.label)
                cb.setChecked(ctrl.default_checked)
                cb.setStyleSheet("font-size: 12px;")
                cb.stateChanged.connect(self._on_control_changed)
                ctrl_bar.addWidget(cb)
                self._control_widgets.append(cb)

            elif ctrl.control_type == "dropdown":
                lbl = QLabel(ctrl.label + ":")
                lbl.setStyleSheet("font-size: 12px;")
                ctrl_bar.addWidget(lbl)
                combo = QComboBox()
                combo.setStyleSheet("font-size: 12px;")
                for opt in ctrl.options:
                    combo.addItem(opt.get("label", ""), opt.get("value", ""))
                idx = ctrl.default_index
                if 0 <= idx < combo.count():
                    combo.setCurrentIndex(idx)
                combo.currentIndexChanged.connect(self._on_control_changed)
                ctrl_bar.addWidget(combo)
                self._control_widgets.append(combo)

        layout.addLayout(ctrl_bar)

        # ── 分隔线 ───────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #e0e0e0;")
        layout.addWidget(sep)

        # ── 表格 ─────────────────────────────────────────────────
        self._table = QTableWidget()
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.setStyleSheet("""
            QTableWidget {
                font-size: 12px;
                gridline-color: #ddd;
                alternate-background-color: #f8f9fa;
            }
            QTableWidget::item:selected {
                background-color: #0078d4;
                color: white;
            }
            QHeaderView::section {
                background-color: #e9ecef;
                padding: 4px;
                font-weight: bold;
                font-size: 12px;
                border: 1px solid #ccc;
            }
        """)

        col_count = len(self._display_columns)
        self._table.setColumnCount(col_count)
        self._table.setHorizontalHeaderLabels([dc.header for dc in self._display_columns])
        hdr = self._table.horizontalHeader()
        for i, dc in enumerate(self._display_columns):
            self._table.setColumnWidth(i, dc.width)
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        if self._display_columns:
            hdr.setStretchLastSection(True)

        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.cellClicked.connect(self._on_cell_clicked)

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
        self._splitter.setHandleWidth(6)          # 拖动手柄宽度
        self._splitter.setChildrenCollapsible(False)  # 两侧不可完全折叠
        self._splitter.addWidget(self._table)
        self._splitter.addWidget(preview_widget)
        self._splitter.setStretchFactor(0, 3)     # 表格区加权
        self._splitter.setStretchFactor(1, 1)     # 预览区加权
        self._splitter.setSizes([380, 130])       # 默认分配大小
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: #d0d0d0; }"
            "QSplitter::handle:hover { background: #0078d4; }"
        )
        layout.addWidget(self._splitter)

        # ── 底部提示 ─────────────────────────────────────────────
        hint = QLabel(_t("💡 双击某行将数据写入 Excel 当前行  |  可修改查询文本后重新查询"))
        hint.setStyleSheet("color: #aaa; font-size: 10px;")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

    # ── 数据填充 ─────────────────────────────────────────────────

    def _populate_table(self, results: list[dict]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(results))
        for row_idx, record in enumerate(results):
            for col_idx, dc in enumerate(self._display_columns):
                value = record.get(dc.field, "")
                if dc.field == "score" and isinstance(value, (int, float)):
                    text = f"{value:.4f}"
                elif isinstance(value, float):
                    text = f"{value:.2f}"
                else:
                    text = str(value) if value is not None else ""
                item = QTableWidgetItem(text)
                if isinstance(value, (int, float)):
                    item.setData(Qt.UserRole, value)
                item.setToolTip(text)
                self._table.setItem(row_idx, col_idx, item)
        self._table.setSortingEnabled(True)

    def _update_info(self, query_text: str, count: int, elapsed: float) -> None:
        short = query_text if len(query_text) <= 50 else query_text[:50] + "…"
        self._info_label.setText(
            _t("「{query}」  结果: {count} 条  耗时: {elapsed:.3f}s").format(query=short, count=count, elapsed=elapsed)
        )

    # ── 收集控件当前值 ────────────────────────────────────────────

    def _collect_control_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        widget_idx = 0
        for ctrl in self._ui_controls:
            if widget_idx >= len(self._control_widgets):
                break
            widget = self._control_widgets[widget_idx]
            widget_idx += 1
            if ctrl.control_type == "checkbox" and isinstance(widget, QCheckBox):
                if widget.isChecked():
                    if ctrl.checked_value is not None:
                        params[ctrl.query_field] = ctrl.checked_value
                else:
                    if ctrl.unchecked_value is not None:
                        params[ctrl.query_field] = ctrl.unchecked_value
            elif ctrl.control_type == "dropdown" and isinstance(widget, QComboBox):
                value = widget.currentData()
                t = widget.currentText().strip().lower()
                is_all = False
                if t in ("全部", "all", "（全部）", "(全部)"):
                    is_all = True
                if value is not None:
                    v_str = str(value).strip().lower()
                    if v_str in ("", "全部", "all", "（全部）", "(全部)"):
                        is_all = True
                if not is_all and value is not None and value != "":
                    params[ctrl.query_field] = value
        return params

    # ── 公开方法 ─────────────────────────────────────────────────

    def update_results(self, results: list[dict], elapsed: float,
                       new_query_text: str | None = None) -> None:
        """外部调用：更新表格数据（重新查询后）"""
        self._results = results
        if new_query_text is not None:
            self._query_text = new_query_text
            self._query_input.setText(new_query_text)
        self._update_info(self._query_text, len(results), elapsed)
        self._populate_table(results)

    def set_loading(self, loading: bool) -> None:
        self._search_btn.setEnabled(not loading)
        self._table.setEnabled(not loading)
        if loading:
            self._info_label.setText(_t("正在查询…"))

    def get_query_text(self) -> str:
        return self._query_input.text().strip()

    # ── 列宽持久化 ───────────────────────────────────────────────

    def _load_column_widths(self) -> None:
        """从配置读取已保存的列宽并应用到表格"""
        if not self._config_manager or not self._group_id:
            return
        saved = self._config_manager.get_column_widths(self._group_id)
        if not saved:
            return
        hdr = self._table.horizontalHeader()
        for i, dc in enumerate(self._display_columns):
            key = dc.header
            if key in saved:
                self._table.setColumnWidth(i, saved[key])
        # 最后一列伸展仍保持
        if self._display_columns:
            hdr.setStretchLastSection(True)

    def _save_column_widths(self) -> None:
        """将当前列宽保存到配置"""
        if not self._config_manager or not self._group_id:
            return
        widths = {
            dc.header: self._table.columnWidth(i)
            for i, dc in enumerate(self._display_columns)
        }
        self._config_manager.save_column_widths(self._group_id, widths)

    def _load_window_size(self) -> None:
        """从配置恢复窗口尺寸"""
        if not self._config_manager or not self._group_id:
            return
        size = self._config_manager.get_window_size(f"popup_{self._group_id}")
        if size:
            self.resize(size[0], size[1])

    def _save_window_size(self) -> None:
        """保存当前窗口尺寸到配置"""
        if not self._config_manager or not self._group_id:
            return
        self._config_manager.save_window_size(
            f"popup_{self._group_id}", self.width(), self.height()
        )

    def _load_splitter_sizes(self) -> None:
        """从配置恢复 Splitter 分割比例"""
        if not self._config_manager or not self._group_id:
            return
        sizes = self._config_manager.get_splitter_sizes(f"popup_{self._group_id}")
        if sizes:
            self._splitter.setSizes(sizes)

    def _save_splitter_sizes(self) -> None:
        """保存 Splitter 分割比例到配置"""
        if not self._config_manager or not self._group_id:
            return
        self._config_manager.save_splitter_sizes(
            f"popup_{self._group_id}", self._splitter.sizes()
        )

    # ── 事件处理 ─────────────────────────────────────────────────

    def _on_text_search(self) -> None:
        """用户修改文本框后点击查询"""
        new_text = self._query_input.text().strip()
        if not new_text:
            return
        self._query_text = new_text
        ctrl_params = self._collect_control_params()
        self.set_loading(True)
        self.text_requery_requested.emit(new_text, ctrl_params)

    def _on_control_changed(self, *_) -> None:
        """任意控件状态变化时重新查询（保持当前文本框内容）"""
        params = self._collect_control_params()
        self.set_loading(True)
        self.requery_requested.emit(params)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        """点击单元格 → 在底部文字框显示完整内容"""
        item = self._table.item(row, col)
        if item is not None:
            self._cell_preview.setPlainText(item.text())
        else:
            self._cell_preview.clear()

    def _on_double_click(self, row: int, col: int) -> None:
        if 0 <= row < len(self._results):
            self.row_selected.emit(self._results[row])
            self.close()

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
