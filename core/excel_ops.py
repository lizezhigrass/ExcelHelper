"""
core/excel_ops.py

Excel 读写操作封装 —— 通过 xlwings COM 接口与活动 Excel 交互。
"""

from __future__ import annotations

import logging
from typing import Any

import xlwings as xw

from core.mapping import InputMapping, OutputMapping, col_letter_to_number

logger = logging.getLogger(__name__)


class ExcelOps:
    """封装 xlwings 的 Excel 读写操作"""

    @staticmethod
    def get_active_row() -> int | None:
        """获取当前活动单元格所在行号（1-based），Excel 未打开时返回 None"""
        try:
            app = xw.apps.active
            if app is None:
                return None
            cell = app.api.ActiveCell
            return cell.Row
        except Exception as exc:
            logger.warning("获取活动行失败: %s", exc)
            return None

    @staticmethod
    def get_active_sheet():
        """获取当前活动工作表对象，失败返回 None"""
        try:
            app = xw.apps.active
            if app is None:
                return None
            wb = app.books.active
            if wb is None:
                return None
            return wb.sheets.active
        except Exception as exc:
            logger.warning("获取活动工作表失败: %s", exc)
            return None

    @staticmethod
    def read_input(sheet, row: int, input_mappings: list[InputMapping]) -> dict[str, str]:
        """
        按输入映射从 Excel 读取数据并拼接。

        同时支持 Excel 列模式以及包含默认值的静态/UI组件模式。

        Returns
        -------
        dict  查询字段字典，如 {"query": "不锈钢 螺栓"}
        """
        fields: dict[str, str] = {}
        for mapping in input_mappings:
            key = mapping.query_field if mapping.query_field else mapping.ui_component
            if not key:
                continue

            if not mapping.columns:          # UI 组件 / 静态默认值模式
                def_val = getattr(mapping, "default_value", "")
                if def_val:
                    fields[key] = def_val
                continue

            values: list[str] = []
            for col_id in mapping.columns:
                # 支持字母（A, B）和数字（1, 2）
                if col_id.strip().isdigit():
                    col_num = int(col_id.strip())
                else:
                    col_num = col_letter_to_number(col_id)
                if col_num < 1:
                    continue
                try:
                    val = sheet.cells(row, col_num).value
                    if val is not None:
                        values.append(str(val).strip())
                except Exception as exc:
                    logger.warning("读取 (%d, %s) 失败: %s", row, col_id, exc)
            val = mapping.separator.join(v for v in values if v)
            if not val and getattr(mapping, "default_value", ""):
                val = mapping.default_value
            fields[key] = val
        return fields


    @staticmethod
    def write_output(sheet, row: int, output_mappings: list[OutputMapping], data: dict[str, Any]) -> None:
        """
        按输出映射将选中的结果数据写入 Excel 对应列。
        """
        for mapping in output_mappings:
            value = data.get(mapping.result_field, "")
            if mapping.column.strip().isdigit():
                col_num = int(mapping.column.strip())
            else:
                col_num = col_letter_to_number(mapping.column)
            if col_num < 1:
                continue
            try:
                sheet.cells(row, col_num).value = value
            except Exception as exc:
                logger.warning("写入 (%d, %s) 失败: %s", row, mapping.column, exc)

    @staticmethod
    def get_selected_rows() -> list[int]:
        """
        返回当前 Excel 选中区域的所有**可见**行号（1-based，去重，升序）。
        支持不连续多选区（按 Ctrl 框选多块）。
        隐藏行（筛选隐藏或手动隐藏）自动跳过，不计入结果。
        未选中或发生错误时返回空列表。
        """
        try:
            app = xw.apps.active
            if app is None:
                return []
            sel = app.api.Selection
            rows_set: set[int] = set()
            # Areas 处理不连续选区（普通单选/框选只有 1 个 Area）
            for area in sel.Areas:
                start_row = area.Row
                row_count = area.Rows.Count
                for i in range(row_count):
                    row_num = start_row + i
                    try:
                        # EntireRow.Hidden：True 表示该行被筛选/手动隐藏
                        if area.Worksheet.Rows(row_num).Hidden:
                            continue
                    except Exception:
                        pass  # 获取 Hidden 属性失败时保守地保留该行
                    rows_set.add(row_num)
            return sorted(rows_set)
        except Exception as exc:
            logger.warning("获取选中行列表失败: %s", exc)
            return []

    @staticmethod
    def read_cell(sheet, row: int, col: str) -> Any:
        """
        读取单个单元格的值。

        Parameters
        ----------
        sheet : xlwings Sheet 对象
        row   : 行号（1-based）
        col   : 列字母（如 "A"、"AB"）或数字字符串
        """
        from core.mapping import col_letter_to_number
        if str(col).strip().isdigit():
            col_num = int(str(col).strip())
        else:
            col_num = col_letter_to_number(str(col).strip().upper())
        if col_num < 1:
            return None
        try:
            return sheet.cells(row, col_num).value
        except Exception as exc:
            logger.warning("read_cell (%d, %s) 失败: %s", row, col, exc)
            return None

    @staticmethod
    def read_row_by_cols(sheet, row: int, cols: set[str]) -> dict[str, str]:
        """
        批量读取一行中指定列字母集合对应的单元格值。

        Returns
        -------
        dict  {列字母大写: 值字符串}
        """
        result: dict[str, str] = {}
        from core.mapping import col_letter_to_number
        for col in cols:
            col_up = col.strip().upper()
            if col_up.isdigit():
                col_num = int(col_up)
            else:
                col_num = col_letter_to_number(col_up)
            if col_num < 1:
                continue
            try:
                val = sheet.cells(row, col_num).value
                if val is not None:
                    result[col_up] = str(val).strip()
            except Exception as exc:
                logger.warning("read_row_by_cols (%d, %s) 失败: %s", row, col, exc)
        return result

