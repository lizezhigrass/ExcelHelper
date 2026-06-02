"""
main.py

Excel 快捷查询助手 —— 入口文件。
以系统托盘形式常驻后台，通过全局快捷键触发查询，结果弹窗展示。

运行方式：
    python main.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot, Qt
from PyQt5.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from core.i18n import _t

# ── 日志 ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("excel_helper")

# ── 路径 ────────────────────────────────────────────────────────
import sys as _sys
if getattr(_sys, "frozen", False):
    # PyInstaller onedir 模式：
    #   - exe 文件位于: <dist>/<name>/<name>.exe
    #   - datas 文件位于: <dist>/<name>/_internal/  (sys._MEIPASS)
    # config.yaml 优先读取 exe 同级目录（方便用户修改），
    # 找不到则回退到打包时内置的 _MEIPASS 路径。
    _EXE_DIR    = Path(_sys.executable).resolve().parent       # exe 所在目录
    _MEIPASS    = Path(getattr(_sys, "_MEIPASS", str(_EXE_DIR)))  # _internal 目录
    _CONFIG_CANDIDATE = _EXE_DIR / "config.yaml"
    _SCRIPT_DIR = _EXE_DIR if _CONFIG_CANDIDATE.exists() else _MEIPASS
else:
    _SCRIPT_DIR = Path(__file__).resolve().parent



# ══════════════════════════════════════════════════════════════════
# 辅助函数
# ══════════════════════════════════════════════════════════════════

def _ctrl_default_value(ctrl):
    """取 UIControlMapping 控件的默认值，无默认值返回 None"""
    if ctrl.control_type == "checkbox":
        if ctrl.default_checked and ctrl.checked_value is not None:
            return ctrl.checked_value
        if not ctrl.default_checked and ctrl.unchecked_value is not None:
            return ctrl.unchecked_value
    elif ctrl.control_type == "dropdown":
        opts = ctrl.options
        idx = ctrl.default_index
        if 0 <= idx < len(opts):
            v = opts[idx].get("value", "")
            if v != "":
                return v
    return None


# ══════════════════════════════════════════════════════════════════
# 查询工作线程（避免 HTTP 阻塞 UI）
# ══════════════════════════════════════════════════════════════════

class _QueryWorker(QThread):
    """在后台执行查询"""
    finished = pyqtSignal(list, float)     # results, elapsed
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


class _AutoFillWorker(QThread):
    """
    auto_fill 模式专用工作线程。
    强制 limit=1，只取第一条结果。
    """
    finished = pyqtSignal(list, float)   # results(0或1条), elapsed
    error    = pyqtSignal(str)

    def __init__(self, query_module, fields: dict, params: dict, parent=None):
        super().__init__(parent)
        self._qm     = query_module
        self._fields = fields
        # 强制 limit=1，减少不必要的数据传输
        self._params = dict(params)
        self._params["limit"] = 1

    def run(self):
        try:
            results, elapsed = self._qm.search(self._fields, self._params)
            self.finished.emit(results[:1], elapsed)
        except Exception as exc:
            self.error.emit(str(exc))


class _ConnectWorker(QThread):
    """
    异步执行查询模块的 connect() 方法。
    适用于需要建立连接状态的后端（如 qdrant_vector）。
    """
    finished = pyqtSignal(str, bool)   # mod_id, success

    def __init__(self, mod_id: str, qm, parent=None):
        super().__init__(parent)
        self._mod_id = mod_id
        self._qm = qm

    def run(self):
        try:
            ok = self._qm.connect()
            self.finished.emit(self._mod_id, bool(ok))
        except Exception as exc:
            logger.error("查询模块 %s connect() 失败: %s", self._mod_id, exc)
            self.finished.emit(self._mod_id, False)


# ══════════════════════════════════════════════════════════════════
# 主应用控制器
# ══════════════════════════════════════════════════════════════════

class Application(QObject):
    """串联所有模块的主控制器"""

    def __init__(self):
        super().__init__()

        from core.mapping import ConfigManager
        from core.excel_ops import ExcelOps
        from hotkeys.listener import HotkeyManager
        from ui.tray_icon import TrayIcon

        # 配置管理
        self.config_mgr = ConfigManager(_SCRIPT_DIR / "config.yaml")

        # 语言初始化
        lang = self.config_mgr.config.get("global", {}).get("language", "en")
        from core.i18n import set_language, detect_system_lang
        if lang == "auto":
            set_language(detect_system_lang())
        else:
            set_language(lang)

        # 查询模块池
        self.query_modules: dict[str, Any] = {}
        self._init_query_modules()

        # Excel 操作
        self.excel_ops = ExcelOps()

        # 快捷键管理
        self.hotkey_mgr = HotkeyManager()
        self.hotkey_mgr.hotkey_triggered.connect(self._on_hotkey)

        # 系统托盘（先创建但不立即 show，等事件循环启动后再显示）
        self.tray = TrayIcon(self)

        # 设置窗口（延迟创建）
        self._settings_win = None

        # 当前弹窗引用（用于 requery）
        self._current_popup = None
        self._current_context: dict = {}

        # 搜索面板实例池（防止被 GC）
        self._search_panels: list = []

        # 工作线程引用（防止被 GC）
        self._worker = None

        # auto_fill 进度对话框引用池
        self._auto_fill_dialogs: list = []

        # 延迟初始化：等 Qt 事件循环首次 tick 后再 show 托盘和注册快捷键
        # Windows 上 QSystemTrayIcon.show() 必须在 exec_() 之后才能正常出现
        QTimer.singleShot(100, self._deferred_start)

    # ── 初始化 ───────────────────────────────────────────────────

    def _deferred_start(self) -> None:
        """事件循环启动后执行：显示托盘 + 注册快捷键"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error("系统不支持托盘图标！")
            return

        self.tray.show()
        # 再等一个 tick 确保托盘真正显示后再设置状态
        QTimer.singleShot(200, self._init_hotkeys)

    def _init_hotkeys(self) -> None:
        """托盘显示后注册快捷键"""
        if self.config_mgr.is_hotkey_enabled():
            self._register_hotkeys()
        else:
            self.tray.update_icon_state(False)

    def _init_query_modules(self) -> None:
        from core.query_base import load_backends, BACKEND_REGISTRY
        # 动态扫描并加载所有插件后端
        load_backends()

        qm_cfg = self.config_mgr.get_query_modules()
        for mod_id, mod_cfg in qm_cfg.items():
            # 检查 enabled 字段，默认为 true
            if not mod_cfg.get("enabled", True):
                logger.info("查询模块已禁用（跳过加载）: %s", mod_id)
                # 确保内存中没有该模块的残留实例
                self.query_modules.pop(mod_id, None)
                continue
            b_type = mod_cfg.get("type")
            cls = BACKEND_REGISTRY.get(b_type)
            if cls is not None:
                try:
                    qm = cls.from_config(mod_cfg)
                    self.query_modules[mod_id] = qm
                    logger.info("查询模块已动态注册: %s (%s)", mod_id, b_type)
                    # 如果模块需要异步连接，在后台线程中执行
                    self._start_connect(mod_id, qm)
                except Exception as exc:
                    logger.error("实例化查询后端 %s (%s) 失败: %s", mod_id, b_type, exc)
            else:
                logger.warning("未找到匹配的查询后端插件类型: %s (ID: %s)", b_type, mod_id)

    def _register_hotkeys(self) -> None:
        tm_cfg = self.config_mgr.get_table_modules()
        hotkey_map: dict[str, str] = {}
        for mod_id, mod_cfg in tm_cfg.items():
            if mod_cfg.get("enabled", False) and mod_cfg.get("hotkey"):
                hotkey_map[mod_cfg["hotkey"]] = mod_id
        self.hotkey_mgr.update_hotkeys(hotkey_map)
        self.tray.update_icon_state(True)

    # ── 快捷键事件处理 ───────────────────────────────────────────

    @pyqtSlot(str)
    def _on_hotkey(self, module_id: str) -> None:
        """快捷键触发（从 pynput 线程 emit，在主线程执行）"""
        logger.info("处理快捷键触发: module=%s", module_id)

        # 获取模块配置
        tm_cfg = self.config_mgr.get_table_modules().get(module_id, {})
        mg_id = tm_cfg.get("mapping_group", "")
        if not mg_id:
            self.tray.showMessage(_t("提示"), _t("模块 {module_id} 未配置映射组，请在设置中配置").format(module_id=module_id), self.tray.Warning, 3000)
            return

        # 获取映射组
        mapping_group = self.config_mgr.get_mapping_group(mg_id)
        if mapping_group is None:
            self.tray.showMessage(_t("错误"), _t("映射组 {mg_id} 不存在").format(mg_id=mg_id), self.tray.Critical, 3000)
            return

        # 获取交互模块
        im = self.config_mgr.get_interaction_module(mapping_group.interaction_module)
        if im is None:
            self.tray.showMessage(
                _t("错误"),
                _t("交互模块 {im_id} 不存在，请在「设置 → 映射组」中配置").format(im_id=mapping_group.interaction_module),
                self.tray.Critical, 3000,
            )
            return

        # 获取查询模块
        qm = self.query_modules.get(mapping_group.query_module)
        if qm is None:
            all_qm_cfg = self.config_mgr.get_query_modules()
            mod_cfg_check = all_qm_cfg.get(mapping_group.query_module, {})
            if not mod_cfg_check.get("enabled", True):
                self.tray.showMessage(
                    _t("提示"),
                    _t("查询模块 {qm_id} 已暂停使用，请在「设置 → 查询模块」中启用").format(qm_id=mapping_group.query_module),
                    self.tray.Warning, 4000,
                )
            else:
                self.tray.showMessage(
                    _t("错误"),
                    _t("查询模块 {qm_id} 未注册").format(qm_id=mapping_group.query_module),
                    self.tray.Critical, 3000,
                )
            return

        # 读取 Excel 数据
        sheet = self.excel_ops.get_active_sheet()
        if sheet is None:
            self.tray.showMessage(_t("提示"), _t("未检测到打开的 Excel 工作表"), self.tray.Warning, 3000)
            return

        row = self.excel_ops.get_active_row()
        if row is None:
            self.tray.showMessage(_t("提示"), _t("无法获取当前活动行"), self.tray.Warning, 3000)
            return

        # 由交互模块的 base_type 决定路由
        base_type = im.base_type
        if base_type == "search_panel":
            self._open_search_panel(module_id, tm_cfg, mapping_group, im, qm, sheet, row)
        elif base_type == "auto_fill":
            self._start_auto_fill(tm_cfg, mapping_group, im, qm, sheet, row)
        elif base_type == "display_panel":
            self._open_display_panel(module_id, tm_cfg, mapping_group, im, qm, sheet, row)
        else:
            # 默认 popup_table 流程
            fields = self.excel_ops.read_input(sheet, row, mapping_group.input_mappings)
            logger.info("从 Excel 读取: row=%d, fields=%s", row, fields)

            # 过滤掉空的/提示用的查询字段
            query_fields = {}
            for inp in mapping_group.input_mappings:
                if inp.query_field:
                    k = inp.query_field
                    if k in fields:
                        query_fields[k] = fields[k]

            has_content = any(v.strip() for v in query_fields.values())
            if not has_content:
                self.tray.showMessage(_t("提示"), _t("当前行对应列没有有效数据进行查询"), self.tray.Warning, 3000)
                return

            self._current_context = {
                "sheet": sheet, "row": row, "fields": query_fields,
                "mapping_group": mapping_group, "interaction_module": im,
                "query_module": qm, "mg_id": mg_id,
            }
            params = dict(mapping_group.query_params)
            params.update(self._default_control_params(im, mapping_group))
            self._start_query(query_fields, params, qm, mapping_group, is_requery=False)


    def _open_search_panel(
        self, module_id: str, tm_cfg: dict, mapping_group, im, qm, sheet, row: int
    ) -> None:
        """打开搜索面板（search_panel 类型）"""
        from ui.search_panel import SearchPanelDialog

        # 从 Excel 读取预填字段
        prefill_fields: dict[str, str] = {}
        if sheet and row:
            prefill_fields = self.excel_ops.read_input(sheet, row, mapping_group.input_mappings)
            logger.info("搜索面板预填: row=%d fields=%s", row, prefill_fields)

        # 搜索框定义从交互模块读取
        search_box_defs = im.search_boxes or [
            {"field": "query", "label": "查询", "width": 200},
        ]

        panel = SearchPanelDialog(
            query_module=qm,
            mapping_group=mapping_group,
            prefill_fields=prefill_fields,
            search_box_defs=search_box_defs,
            title=tm_cfg.get("title", "搜索面板"),
            config_manager=self.config_mgr,
            group_id=tm_cfg.get("mapping_group", ""),
        )
        # 双击回写：在双击弹出时动态获取 Excel 当前活动行
        panel.row_selected.connect(
            lambda data, mg=mapping_group: self._on_panel_row_selected(data, mg)
        )
        panel.destroyed.connect(lambda: self._search_panels.remove(panel)
                                if panel in self._search_panels else None)
        self._search_panels.append(panel)
        panel.show()
        panel.activateWindow()

    def _open_display_panel(
        self, module_id: str, tm_cfg: dict, mapping_group, im, qm, sheet, row: int
    ) -> None:
        """打开通用显示模组面板（display_panel 类型）"""
        from ui.display_panel import DisplayPanelDialog

        # 从 Excel 读取预填字段（通过 input_mappings 和 query_field 匹配组件）
        prefill_fields: dict[str, str] = {}
        if sheet and row:
            prefill_fields = self.excel_ops.read_input(sheet, row, mapping_group.input_mappings)
            logger.info("display_panel 预填: row=%d fields=%s", row, prefill_fields)

        panel = DisplayPanelDialog(
            query_module    = qm,
            mapping_group   = mapping_group,
            components      = im.components,
            prefill_fields  = prefill_fields,
            title           = tm_cfg.get("title", "查询面板"),
            config_manager  = self.config_mgr,
            group_id        = tm_cfg.get("mapping_group", ""),
        )
        # 双击回写：在双击弹出时动态获取 Excel 当前活动行
        panel.row_selected.connect(
            lambda data, mg=mapping_group: self._on_panel_row_selected(data, mg)
        )
        panel.destroyed.connect(lambda: self._search_panels.remove(panel)
                                if panel in self._search_panels else None)
        self._search_panels.append(panel)
        panel.show()
        panel.activateWindow()

    @pyqtSlot(dict)
    def _on_panel_row_selected(self, data: dict, mapping_group) -> None:
        """
        search_panel / display_panel 双击回写的统一处理槽。

        在双击弹出的個刻动态读取 Excel 当前活动 sheet / row，
        确保即使用户在弹窗期间切换了行，回写位置也正确。
        """
        try:
            sheet = self.excel_ops.get_active_sheet()
            row   = self.excel_ops.get_active_row()
        except Exception as exc:
            logger.warning("获取 Excel 活动行失败: %s", exc)
            self.tray.showMessage(
                _t("写入失败"),
                _t("无法获取 Excel 当前行，请先单击 Excel 内某个单元格"),
                self.tray.Warning, 4000,
            )
            return
        if not sheet or not row:
            self.tray.showMessage(
                _t("写入失败"),
                _t("请先在 Excel 中选中一个单元格再双击查询结果"),
                self.tray.Warning, 4000,
            )
            return
        self.excel_ops.write_output(sheet, row, mapping_group.output_mappings, data)
        logger.info("面板双击回写: row=%d data_keys=%s", row, list(data.keys()))
        self.tray.showMessage(
            _t("写入成功"),
            _t("已将数据写入第 {row} 行").format(row=row),
            self.tray.Information, 2000,
        )

    def _start_auto_fill(
        self,
        tm_cfg:        dict,
        mapping_group,
        im,
        qm,
        sheet,
        _active_row:   int,   # 保留参数兼容签名，实际使用选中行列表
    ) -> None:
        """
        auto_fill 多行模式主流程：
          1. 获取当前选中的全部行号（支持连续框选 / Ctrl 不连续多选）
          2. 为所有行创建一个进度对话框
          3. 逐行：读取字段 → 后台查询（limit=1）→ 回写第一条结果
          4. 全部完成后对话框倒计时自动关闭
        """
        from ui.auto_fill_dialog import AutoFillProgressDialog

        # ── 获取选中行 ──────────────────────────────────────────
        all_rows = self.excel_ops.get_selected_rows()
        if not all_rows:
            if _active_row:
                all_rows = [_active_row]
            else:
                self.tray.showMessage(_t("提示"), _t("无法确定要处理的行，请先在 Excel 中选择行"), self.tray.Warning, 3000)
                return

        auto_close = im.auto_close_seconds
        title      = tm_cfg.get("title", _t("自动填充"))[:30]

        dlg = AutoFillProgressDialog(
            total_rows=len(all_rows),
            title=title,
            auto_close=auto_close,
        )
        dlg.destroyed.connect(
            lambda: self._auto_fill_dialogs.remove(dlg)
            if dlg in self._auto_fill_dialogs else None
        )
        self._auto_fill_dialogs.append(dlg)
        dlg.show()
        dlg.raise_()

        # ── 会话对象（dict 作为轻量引用容器） ──────────────────
        params = dict(mapping_group.query_params)
        params.update(self._default_control_params(im, mapping_group))

        session: dict = {
            "rows":          all_rows,
            "sheet":         sheet,
            "mapping_group": mapping_group,
            "qm":            qm,
            "params":        params,
            "dlg":           dlg,
            "current_idx":   0,
            "ok":            0,
            "skip":          0,
            "error":         0,
            "worker":        None,   # 防 GC 占位
        }

        logger.info(
            "auto_fill 启动: 共 %d 行  rows=%s", len(all_rows), all_rows
        )
        self._process_auto_fill_row(session)

    # ── 逐行处理 ─────────────────────────────────────────────────

    def _process_auto_fill_row(self, session: dict) -> None:
        """处理 session 当前索引对应的行。"""
        idx     = session["current_idx"]
        row_num = session["rows"][idx]
        dlg     = session["dlg"]
        sheet   = session["sheet"]
        mg      = session["mapping_group"]

        dlg.start_row(idx, row_num)
        dlg.set_reading()

        # 读取字段（COM 调用，主线程同步，速度快）
        fields = self.excel_ops.read_input(sheet, row_num, mg.input_mappings)
        dlg.set_read_done(fields)
        logger.info("auto_fill row=%d fields=%s", row_num, fields)

        # 过滤掉空的/提示用的查询字段
        query_fields = {}
        for inp in mg.input_mappings:
            if inp.query_field:
                k = inp.query_field
                if k in fields:
                    query_fields[k] = fields[k]

        has_content = any(str(v).strip() for v in query_fields.values())
        if not has_content:
            dlg.set_row_no_result()
            logger.info("auto_fill row=%d 无有效查询数据，跳过", row_num)
            self._advance_auto_fill(session)
            return

        # 启动后台查询
        dlg.set_querying()
        worker = _AutoFillWorker(session["qm"], query_fields, session["params"])
        worker.finished.connect(
            lambda results, elapsed, _s=session, _r=row_num:
                self._on_auto_fill_row_done(results, elapsed, _s, _r)
        )
        worker.error.connect(
            lambda msg, _s=session: self._on_auto_fill_row_error(msg, _s)
        )
        session["worker"] = worker   # 防 GC
        worker.start()

    def _on_auto_fill_row_done(
        self,
        results: list,
        elapsed: float,
        session: dict,
        row_num: int,
    ) -> None:
        """单行查询完成回调。"""
        dlg = session["dlg"]
        dlg.set_query_done(len(results), elapsed)

        if not results:
            dlg.set_row_no_result()
            logger.info("auto_fill row=%d 无结果", row_num)
            self._advance_auto_fill(session)
            return

        # 回写
        dlg.set_writing()
        data = results[0]
        mg   = session["mapping_group"]
        try:
            self.excel_ops.write_output(
                session["sheet"], row_num, mg.output_mappings, data
            )
        except Exception as exc:
            dlg.set_row_error(f"回写失败: {exc}")
            logger.error("auto_fill row=%d 回写失败: %s", row_num, exc)
            self._advance_auto_fill(session)
            return

        # 构建摘要
        parts = []
        for om in mg.output_mappings[:3]:
            v = data.get(om.result_field, "")
            if v:
                parts.append(f"{om.result_field}: {str(v)[:18]}")
        summary = "  ".join(parts) or "已回写"
        dlg.set_row_done(summary)
        logger.info("auto_fill row=%d 回写完成: %s", row_num, summary)

        self._advance_auto_fill(session)

    def _on_auto_fill_row_error(self, msg: str, session: dict) -> None:
        """单行查询异常回调。"""
        session["dlg"].set_row_error(msg)
        logger.error("auto_fill 查询失败: %s", msg)
        self._advance_auto_fill(session)

    def _advance_auto_fill(self, session: dict) -> None:
        """推进到下一行；若已处理完所有行则触发完成逻辑。"""
        session["current_idx"] += 1

        # 从 dialog 同步统计计数器
        dlg = session["dlg"]
        session["ok"]    = dlg._ok
        session["skip"]  = dlg._skip
        session["error"] = dlg._error

        total = len(session["rows"])
        idx   = session["current_idx"]

        if idx < total:
            self._process_auto_fill_row(session)
        else:
            ok    = session["ok"]
            skip  = session["skip"]
            error = session["error"]
            dlg.set_all_done(ok, skip, error)
            logger.info(
                "auto_fill 全部完成: rows=%d  写入=%d  跳过=%d  失败=%d",
                total, ok, skip, error,
            )
            self.tray.showMessage(
                _t("自动填充完成"),
                _t("共 {total} 行：写入 {ok}，跳过 {skip}，失败 {error}").format(total=total, ok=ok, skip=skip, error=error),
                self.tray.Information,
                3000,
            )


    def _start_query(self, fields, params, qm, mapping_group, is_requery=False):
        """启动后台查询线程"""
        self._worker = _QueryWorker(qm, fields, params)
        self._worker.finished.connect(
            lambda results, elapsed: self._on_query_done(results, elapsed, mapping_group, is_requery)
        )
        self._worker.error.connect(self._on_query_error)
        self._worker.start()

    @pyqtSlot(list, float)
    def _on_query_done(self, results: list, elapsed: float, mapping_group, is_requery: bool) -> None:
        """查询完成，显示或更新弹窗"""
        from ui.popup_table import PopupTableDialog

        if is_requery and self._current_popup is not None:
            new_fields = self._current_context.get("fields", {})
            new_qt = " ".join(v for v in new_fields.values() if v) or None
            self._current_popup.update_results(results, elapsed, new_query_text=new_qt)
            self._current_popup.set_loading(False)
            return

        # 构建查询文本（仅拼接值，不含字段名前缀）
        fields = self._current_context.get("fields", {})
        query_text = " ".join(v for v in fields.values() if v)

        # 创建新弹窗（mg_id 已在触发时存入上下文）
        mg_id_for_popup = self._current_context.get("mg_id", "")
        im = self._current_context.get("interaction_module")
        ui_controls = im.ui_controls if im else []

        popup = PopupTableDialog(
            query_text=query_text,
            results=results,
            display_columns=mapping_group.display_columns,
            ui_controls=ui_controls,
            elapsed=elapsed,
            config_manager=self.config_mgr,
            group_id=mg_id_for_popup,
        )
        popup.row_selected.connect(self._on_row_selected)
        popup.requery_requested.connect(self._on_requery)
        popup.text_requery_requested.connect(self._on_text_requery)
        popup.destroyed.connect(self._on_popup_closed)
        self._current_popup = popup
        popup.show()

    @pyqtSlot(str)
    def _on_query_error(self, error_msg: str) -> None:
        """查询失败"""
        logger.error("查询失败: %s", error_msg)
        if self._current_popup:
            self._current_popup.set_loading(False)
        self.tray.showMessage(_t("查询失败"), _t(error_msg), self.tray.Critical, 5000)

    @pyqtSlot(dict)
    def _on_row_selected(self, data: dict) -> None:
        """用户在弹窗中双击选择了一行"""
        ctx = self._current_context
        sheet = ctx.get("sheet")
        row = ctx.get("row")
        mg = ctx.get("mapping_group")
        if sheet and row and mg:
            self.excel_ops.write_output(sheet, row, mg.output_mappings, data)
            logger.info("已将数据写入 Excel row=%d", row)
            self.tray.showMessage(_t("写入成功"), _t("已将数据写入第 {row} 行").format(row=row), self.tray.Information, 2000)

    @pyqtSlot(dict)
    def _on_requery(self, control_params: dict) -> None:
        """弹窗中 UI 控件发生变化，将控件当前值并入基础参数后重新查询"""
        ctx = self._current_context
        fields = ctx.get("fields", {})
        mg = ctx.get("mapping_group")
        qm = ctx.get("query_module")
        if fields and mg and qm:
            params = dict(mg.query_params)
            mapped_params = {}
            for k, v in control_params.items():
                mapped_key = k
                for inp in mg.input_mappings:
                    if inp.ui_component == k:
                        mapped_key = inp.query_field
                        break
                mapped_params[mapped_key] = v
            params.update(mapped_params)
            self._start_query(fields, params, qm, mg, is_requery=True)

    @pyqtSlot(str, dict)
    def _on_text_requery(self, new_text: str, control_params: dict) -> None:
        """用户修改了查询文本框后点击查询，用新文本重新构建 fields 并查询"""
        ctx = self._current_context
        mg = ctx.get("mapping_group")
        qm = ctx.get("query_module")
        if not mg or not qm:
            return
        # 根据映射组的第一个 input_mapping 字段名填入新文本
        if mg.input_mappings:
            first_field = mg.input_mappings[0].query_field
            new_fields = {first_field: new_text}
        else:
            new_fields = {"query": new_text}
        # 保存到上下文（供后续双击写入时读回）
        ctx["fields"] = new_fields
        params = dict(mg.query_params)
        mapped_params = {}
        for k, v in control_params.items():
            mapped_key = k
            for inp in mg.input_mappings:
                if inp.ui_component == k:
                    mapped_key = inp.query_field
                    break
            mapped_params[mapped_key] = v
        params.update(mapped_params)
        self._start_query(new_fields, params, qm, mg, is_requery=True)

    @staticmethod
    def _default_control_params(im, mg=None) -> dict:
        """
        计算首次查询的初始参数（来自 UI 组件的默认值）。

        路由逻辑：
          - 遍历 mg.input_mappings 中 ui_component 不为空的行
          - 在 im.ui_controls 中按 query_field（控件标识）找到对应控件
          - 取控件默认值，以 inp.query_field 为键写入 params

        若 mg 为 None（兼容旧调用），则直接以 ctrl.query_field 为键（旧行为）。
        """
        params: dict = {}
        if not hasattr(im, 'ui_controls'):
            return params

        if mg is not None and hasattr(mg, 'input_mappings'):
            # 构建控件查找表：控件标识 → UIControlMapping
            ctrl_by_field = {ctrl.query_field: ctrl for ctrl in im.ui_controls}
            for inp in mg.input_mappings:
                if not inp.ui_component:
                    continue
                ctrl = ctrl_by_field.get(inp.ui_component)
                if ctrl is None:
                    continue
                val = _ctrl_default_value(ctrl)
                if val is not None:
                    params[inp.query_field] = val
        else:
            # 兼容旧调用（无 mg）：ctrl.query_field 直接作为参数名
            for ctrl in im.ui_controls:
                val = _ctrl_default_value(ctrl)
                if val is not None:
                    params[ctrl.query_field] = val
        return params


    def _on_popup_closed(self) -> None:
        self._current_popup = None

    # ── 公开方法（供托盘/设置调用） ──────────────────────────────

    def pause_hotkeys(self) -> None:
        self.hotkey_mgr.stop()
        self.config_mgr.set_hotkey_enabled(False)
        logger.info("快捷键监听已暂停")

    def resume_hotkeys(self) -> None:
        self.config_mgr.set_hotkey_enabled(True)
        self._register_hotkeys()
        logger.info("快捷键监听已恢复")

    def show_settings(self) -> None:
        from ui.settings_window import SettingsWindow
        if self._settings_win is None:
            self._settings_win = SettingsWindow(self)
            self._settings_win.config_changed.connect(self._on_config_changed)
        self._settings_win.show()
        self._settings_win.activateWindow()

    def _on_config_changed(self) -> None:
        """设置保存后重新加载"""
        self.config_mgr.load()
        self._init_query_modules()
        if self.config_mgr.is_hotkey_enabled():
            self._register_hotkeys()
        else:
            self.hotkey_mgr.stop()
            self.tray.update_icon_state(False)
        logger.info("配置已重新加载")

    def _reinit_single_module(self, mod_id: str, mod_cfg: dict) -> None:
        """
        对单个查询模块进行即时重新实例化。
        供设置界面在点击「应用修改」时即时生效，无需等到关闭设置窗口。
        """
        from core.query_base import BACKEND_REGISTRY
        b_type = mod_cfg.get("type")
        cls = BACKEND_REGISTRY.get(b_type)
        if cls is not None:
            try:
                qm = cls.from_config(mod_cfg)
                self.query_modules[mod_id] = qm
                logger.info("查询模块已即时重载: %s (%s)", mod_id, b_type)
                self._start_connect(mod_id, qm)
            except Exception as exc:
                logger.error("重新实例化查询模块 %s 失败: %s", mod_id, exc)
        else:
            logger.warning("重载时未找到匹配的后端类型: %s (ID: %s)", b_type, mod_id)

    def _start_connect(self, mod_id: str, qm) -> None:
        """
        若模块有 connect() 方法，在后台线程中调用它。
        适用于需要建立连接的后端（如 qdrant_vector）；
        无需 connect() 的后端（如 vector_api、pg）直接跳过。
        """
        if not callable(getattr(qm, "connect", None)):
            return
        worker = _ConnectWorker(mod_id, qm, parent=self)
        worker.finished.connect(self._on_connect_done)
        # 持有引用防止 GC
        if not hasattr(self, "_connect_workers"):
            self._connect_workers: list = []
        self._connect_workers.append(worker)
        worker.finished.connect(lambda mid, ok, w=worker: self._connect_workers.remove(w)
                                if w in self._connect_workers else None)
        worker.start()
        logger.info("已开启后台连接: %s", mod_id)

    def _on_connect_done(self, mod_id: str, success: bool) -> None:
        if success:
            logger.info("查询模块已就绪: %s", mod_id)
            self.tray.showMessage(
                _t("连接就绪"),
                _t("查询模块「{mod_id}」已成功连接，可以开始查询。").format(mod_id=mod_id),
                self.tray.Information, 3000,
            )
        else:
            logger.error("查询模块连接失败: %s", mod_id)
            self.tray.showMessage(
                _t("连接失败"),
                _t("查询模块「{mod_id}」连接失败，请检查配置。").format(mod_id=mod_id),
                self.tray.Warning, 5000,
            )

    def quit_app(self) -> None:
        self.hotkey_mgr.stop()
        self.tray.hide()
        QApplication.instance().quit()


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

def main():
    # 高 DPI 支持（必须在 QApplication 创建前设置）
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 关闭弹窗不退出程序
    app.setApplicationName(_t("Excel 快捷查询助手"))

    # 检查系统托盘支持
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.error("当前系统不支持系统托盘，程序无法启动")
        sys.exit(1)

    controller = Application()

    print("\n" + "=" * 50)
    print("  Excel 快捷查询助手已启动")
    print("  图标将在任务栏通知区域出现")
    print("  双击托盘图标打开设置")
    print("=" * 50 + "\n")

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
