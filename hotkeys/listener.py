"""
hotkeys/listener.py

pynput 全局快捷键监听器。
在子线程中运行，通过 Qt 信号将事件安全传递到主线程。
"""

from __future__ import annotations

import logging
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal
from pynput import keyboard

logger = logging.getLogger(__name__)

# 需要用 <> 包裹的特殊键
_SPECIAL_KEYS = {
    "ctrl", "ctrl_l", "ctrl_r",
    "shift", "shift_l", "shift_r",
    "alt", "alt_l", "alt_r",
    "cmd", "cmd_l", "cmd_r",
    "tab", "enter", "return", "esc", "escape",
    "space", "backspace", "delete",
    "up", "down", "left", "right",
    "home", "end", "page_up", "page_down",
    "insert", "print_screen", "scroll_lock", "pause",
    "caps_lock", "num_lock",
} | {f"f{i}" for i in range(1, 25)}


def user_hotkey_to_pynput(hotkey_str: str) -> str:
    """
    将用户友好格式转为 pynput GlobalHotKeys 格式。
    例如: "ctrl+q" → "<ctrl>+q"
          "ctrl+shift+f2" → "<ctrl>+<shift>+<f2>"
    """
    parts = hotkey_str.lower().replace(" ", "").split("+")
    result = []
    for p in parts:
        if p in _SPECIAL_KEYS:
            result.append(f"<{p}>")
        else:
            result.append(p)
    return "+".join(result)


class HotkeyManager(QObject):
    """管理 pynput 全局快捷键，通过 Qt 信号通知主线程"""

    # 携带 module_id 的信号
    hotkey_triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.GlobalHotKeys | None = None
        self._hotkey_map: dict[str, str] = {}  # user_format → module_id
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def update_hotkeys(self, hotkey_map: dict[str, str]) -> None:
        """
        更新快捷键绑定并重启监听。
        hotkey_map: {"ctrl+q": "popup_selector", ...}
        """
        self.stop()
        self._hotkey_map = hotkey_map
        if hotkey_map:
            self.start()

    def start(self) -> None:
        """启动快捷键监听"""
        if self._listener is not None:
            self.stop()

        pynput_map: dict[str, Callable] = {}
        for user_key, mod_id in self._hotkey_map.items():
            pynput_key = user_hotkey_to_pynput(user_key)
            # 使用工厂函数避免闭包变量捕获问题
            def _make_cb(mid: str) -> Callable:
                def _cb():
                    logger.info("快捷键触发: %s → %s", user_key, mid)
                    self.hotkey_triggered.emit(mid)
                return _cb
            pynput_map[pynput_key] = _make_cb(mod_id)
            logger.info("注册快捷键: %s (%s) → %s", user_key, pynput_key, mod_id)

        if pynput_map:
            self._listener = keyboard.GlobalHotKeys(pynput_map)
            self._listener.daemon = True
            self._listener.start()
            self._active = True
            logger.info("快捷键监听已启动")

    def stop(self) -> None:
        """停止快捷键监听"""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            self._active = False
            logger.info("快捷键监听已停止")
