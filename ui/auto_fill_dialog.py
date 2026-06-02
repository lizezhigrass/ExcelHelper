"""
ui/auto_fill_dialog.py

自动回写进度对话框（多行版本）。

支持遍历选中的多行，逐行显示：
  ① 读取字段
  ② 查询
  ③ 回写

底部实时统计：写入 N  跳过 N  失败 N
全部行完成后倒计时自动关闭。
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy, QProgressBar,
)
from core.i18n import _t

logger = logging.getLogger(__name__)


# ── 状态图标常量 ─────────────────────────────────────────────────

_ICON_WAIT   = "○"
_ICON_ACTIVE = "⏳"
_ICON_OK     = "✅"
_ICON_SKIP   = "⚠"
_ICON_ERROR  = "❌"
_ICON_DASH   = "—"


# ── 单步行布局 ───────────────────────────────────────────────────

class _StepWidget:
    """一步的显示逻辑（标题标签 + 图标标签 + 摘要标签）"""

    _SEQ = "①②③④⑤"

    def __init__(self, index: int, title: str, parent_vbox: QVBoxLayout):
        row_layout = QHBoxLayout()
        row_layout.setSpacing(6)

        num_lbl = QLabel(self._SEQ[index])
        num_lbl.setFixedWidth(18)
        num_lbl.setStyleSheet("color:#777; font-size:13px;")

        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet("font-size:13px;")

        self._icon_lbl = QLabel(_ICON_WAIT)
        self._icon_lbl.setFixedWidth(22)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet("font-size:14px;")

        row_layout.addWidget(num_lbl)
        row_layout.addWidget(self._title_lbl, 1)
        row_layout.addWidget(self._icon_lbl)
        parent_vbox.addLayout(row_layout)

        # 摘要小字
        self._sub_lbl = QLabel("")
        self._sub_lbl.setWordWrap(True)
        self._sub_lbl.setStyleSheet("color:#555; font-size:11px; margin-left:24px;")
        parent_vbox.addWidget(self._sub_lbl)

    def set_icon(self, icon: str) -> None:
        self._icon_lbl.setText(icon)

    def set_sub(self, text: str, *, color: str = "#555") -> None:
        self._sub_lbl.setText(text)
        self._sub_lbl.setStyleSheet(
            f"color:{color}; font-size:11px; margin-left:24px;"
        )

    def reset(self) -> None:
        self._icon_lbl.setText(_ICON_WAIT)
        self._sub_lbl.setText("")
        self._sub_lbl.setStyleSheet("color:#555; font-size:11px; margin-left:24px;")


# ══════════════════════════════════════════════════════════════════

class AutoFillProgressDialog(QDialog):
    """
    多行自动回写进度窗口。

    参数
    ----
    total_rows  : 选中的总行数
    title       : 窗口标题前缀（来自 table_module.title）
    auto_close  : 全部完成后自动关闭延迟秒数，0 = 不自动关闭
    """

    def __init__(
        self,
        total_rows: int,
        title:      str = _t("自动填充"),
        auto_close: int = 3,
        parent=None,
    ):
        super().__init__(parent)
        self._total     = total_rows
        self._auto_close = auto_close
        self._countdown = auto_close
        self._timer     = QTimer(self)
        self._timer.setInterval(1_000)
        self._timer.timeout.connect(self._tick)

        # 统计
        self._ok    = 0
        self._skip  = 0
        self._error = 0

        self.setWindowTitle(_t("{title}  —  共 {total_rows} 行").format(title=title, total_rows=total_rows))
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.WindowCloseButtonHint
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
        )
        self.setFixedWidth(440)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._build_ui()

    # ── UI 构建 ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(4)

        # ── 标题行 ──────────────────────────────────────────────
        title_row = QHBoxLayout()
        icon_lbl  = QLabel("⚡")
        icon_lbl.setStyleSheet("font-size:18px;")
        self._head_lbl = QLabel(_t("准备处理 <b>{total}</b> 行…").format(total=self._total))
        self._head_lbl.setStyleSheet("font-size:13px;")
        title_row.addWidget(icon_lbl)
        title_row.addWidget(self._head_lbl, 1)
        root.addLayout(title_row)

        # ── 进度条 ──────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, self._total)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setStyleSheet(
            "QProgressBar{border:none; background:#e0e0e0; border-radius:3px;}"
            "QProgressBar::chunk{background:#1976d2; border-radius:3px;}"
        )
        root.addWidget(self._progress)
        root.addSpacing(4)

        root.addWidget(_hline())

        # ── 当前行信息 ──────────────────────────────────────────
        self._row_lbl = QLabel("—")
        self._row_lbl.setStyleSheet("color:#555; font-size:11px;")
        root.addWidget(self._row_lbl)
        root.addSpacing(4)

        # ── 三步骤 ──────────────────────────────────────────────
        self._step1 = _StepWidget(0, _t("读取字段"), root)
        root.addSpacing(2)
        self._step2 = _StepWidget(1, _t("查询"), root)
        root.addSpacing(2)
        self._step3 = _StepWidget(2, _t("回写结果"), root)
        root.addSpacing(4)

        root.addWidget(_hline())

        # ── 统计行 ──────────────────────────────────────────────
        stat_row = QHBoxLayout()
        self._stat_ok    = QLabel(_t("✅ 写入: {ok}").format(ok=0))
        self._stat_skip  = QLabel(_t("⚠ 跳过: {skip}").format(skip=0))
        self._stat_error = QLabel(_t("❌ 失败: {error}").format(error=0))
        for lbl in (self._stat_ok, self._stat_skip, self._stat_error):
            lbl.setStyleSheet("font-size:12px;")
            stat_row.addWidget(lbl)
        stat_row.addStretch()
        root.addLayout(stat_row)

        root.addWidget(_hline())

        # ── 底栏 ────────────────────────────────────────────────
        bot = QHBoxLayout()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("font-size:11px; color:#777;")
        self._close_btn = QPushButton(_t("关闭"))
        self._close_btn.setFixedWidth(72)
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)
        bot.addWidget(self._status_lbl, 1)
        bot.addWidget(self._close_btn)
        root.addLayout(bot)

        self.adjustSize()

    # ── 外部调用接口 ─────────────────────────────────────────────

    def start_row(self, idx: int, row_num: int) -> None:
        """
        开始处理第 idx 行（0-based），对应 Excel 行号 row_num。
        重置三步状态。
        """
        self._head_lbl.setText(
            _t("正在处理 <b>{idx}</b> / {total} 行").format(idx=idx + 1, total=self._total)
        )
        self._row_lbl.setText(_t("Excel 第 {row_num} 行").format(row_num=row_num))
        self._progress.setValue(idx)
        self._step1.reset()
        self._step2.reset()
        self._step3.reset()
        self._status_lbl.setText(_t("第 {row_num} 行 — 读取中…").format(row_num=row_num))

    def set_reading(self) -> None:
        self._step1.set_icon(_ICON_ACTIVE)

    def set_read_done(self, fields: dict[str, str]) -> None:
        self._step1.set_icon(_ICON_OK)
        parts = [f"{k}: {v}" for k, v in fields.items() if str(v).strip()]
        sub = "  ".join(parts) if parts else _t("（当前行无数据）")
        self._step1.set_sub(sub, color="#333")

    def set_querying(self) -> None:
        self._step2.set_icon(_ICON_ACTIVE)
        self._status_lbl.setText(_t("查询中…"))

    def set_query_done(self, count: int, elapsed: float) -> None:
        self._step2.set_icon(_ICON_OK)
        self._step2.set_sub(_t("共 {count} 条结果  耗时 {elapsed:.2f}s").format(count=count, elapsed=elapsed), color="#333")

    def set_writing(self) -> None:
        self._step3.set_icon(_ICON_ACTIVE)
        self._status_lbl.setText(_t("回写中…"))

    def set_row_done(self, summary: str) -> None:
        """当前行完成（成功回写）"""
        self._step3.set_icon(_ICON_OK)
        self._step3.set_sub(summary, color="#2e7d32")
        self._ok += 1
        self._refresh_stats()

    def set_row_no_result(self) -> None:
        """当前行无结果，跳过"""
        self._step2.set_icon(_ICON_SKIP)
        self._step2.set_sub(_t("无匹配结果，跳过回写"), color="#e65100")
        self._step3.set_icon(_ICON_DASH)
        self._skip += 1
        self._refresh_stats()

    def set_row_error(self, msg: str) -> None:
        """当前行出错"""
        self._step3.set_icon(_ICON_ERROR)
        self._step3.set_sub(_t("错误：{msg}").format(msg=msg), color="#c62828")
        self._error += 1
        self._refresh_stats()

    def set_all_done(self, ok: int, skip: int, error: int) -> None:
        """所有行处理完毕，启动倒计时关闭"""
        self._ok    = ok
        self._skip  = skip
        self._error = error
        self._refresh_stats()
        self._progress.setValue(self._total)
        self._head_lbl.setText(
            _t("✅ 全部完成  —  共 <b>{total}</b> 行").format(total=self._total)
        )
        self._close_btn.setEnabled(True)
        if self._auto_close > 0:
            self._countdown = self._auto_close
            self._update_countdown()
            self._timer.start()
        else:
            self._status_lbl.setText(_t("已完成"))

    # ── 内部 ─────────────────────────────────────────────────────

    def _refresh_stats(self) -> None:
        self._stat_ok.setText(_t("✅ 写入: {ok}").format(ok=self._ok))
        self._stat_skip.setText(_t("⚠ 跳过: {skip}").format(skip=self._skip))
        self._stat_error.setText(_t("❌ 失败: {error}").format(error=self._error))

    @pyqtSlot()
    def _tick(self) -> None:
        self._countdown -= 1
        if self._countdown <= 0:
            self._timer.stop()
            self.accept()
        else:
            self._update_countdown()

    def _update_countdown(self) -> None:
        self._status_lbl.setText(_t("{countdown}s 后自动关闭").format(countdown=self._countdown))
        self._status_lbl.setStyleSheet("font-size:11px; color:#777;")

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)


# ── 工具 ─────────────────────────────────────────────────────────

def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color:#e0e0e0;")
    return line
