"""
ui/tray_icon.py

系统托盘图标 —— 常驻后台，提供右键菜单入口。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt5.QtWidgets import QSystemTrayIcon, QMenu, QAction, QMessageBox

from core.i18n import _t

if TYPE_CHECKING:
    from main import Application

logger = logging.getLogger(__name__)

# 图标文件路径（与 main.py 同目录）
_ICON_PATH = Path(__file__).resolve().parent.parent / "tray_icon.png"


def _create_icon(active: bool = True) -> QIcon:
    """
    加载 tray_icon.png 作为底图，右下角叠加状态小点。
    绿点 = 监听中，灰点 = 已暂停。
    若图标文件不存在则回退到程序绘制图标。
    """
    size = 64

    # ── 尝试加载 PNG 底图 ────────────────────────────────────────
    if _ICON_PATH.exists():
        base = QPixmap(str(_ICON_PATH)).scaled(
            size, size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if base.isNull():
            base = _draw_fallback(size)
    else:
        logger.warning("托盘图标文件不存在: %s，使用默认图标", _ICON_PATH)
        base = _draw_fallback(size)

    # ── 叠加状态小圆点（右下角）────────────────────────────────
    result = QPixmap(size, size)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.drawPixmap(0, 0, base)

    dot_size = 16
    dot_x = size - dot_size - 2
    dot_y = size - dot_size - 2
    dot_color = QColor("#4CAF50") if active else QColor("#9E9E9E")

    # 白色描边
    painter.setBrush(QColor("white"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(dot_x - 2, dot_y - 2, dot_size + 4, dot_size + 4)
    # 状态色填充
    painter.setBrush(dot_color)
    painter.drawEllipse(dot_x, dot_y, dot_size, dot_size)
    painter.end()

    return QIcon(result)


def _draw_fallback(size: int) -> QPixmap:
    """回退图标：深青色圆形 + 白色闪电"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#1a6b5a"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 12, 12)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Arial", 30, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "⚡")
    painter.end()
    return pixmap


class TrayIcon(QSystemTrayIcon):
    """系统托盘图标"""

    def __init__(self, app_controller: Application):
        super().__init__()
        self._app = app_controller
        self._active = True

        self.setIcon(_create_icon(True))
        self.setToolTip(_t("Excel 快捷查询助手"))

        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()

        # 切换监听
        self._toggle_action = QAction(_t("⏸ 暂停监听"), self)
        self._toggle_action.triggered.connect(self._toggle_hotkey)
        menu.addAction(self._toggle_action)

        menu.addSeparator()

        # 打开设置
        settings_action = QAction(_t("⚙ 设置"), self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        # 退出
        quit_action = QAction(_t("✖ 退出"), self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def update_icon_state(self, active: bool) -> None:
        """更新托盘图标状态"""
        self._active = active
        self.setIcon(_create_icon(active))
        self._toggle_action.setText(_t("⏸ 暂停监听") if active else _t("▶ 启用监听"))
        tip = _t("Excel 快捷查询助手") + " — " + (_t("监听中") if active else _t("已暂停"))
        self.setToolTip(tip)

    # ── 事件处理 ─────────────────────────────────────────────────

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._open_settings()

    def _toggle_hotkey(self) -> None:
        if self._active:
            self._app.pause_hotkeys()
            self.update_icon_state(False)
        else:
            self._app.resume_hotkeys()
            self.update_icon_state(True)

    def _open_settings(self) -> None:
        self._app.show_settings()

    def _quit(self) -> None:
        self._app.quit_app()
