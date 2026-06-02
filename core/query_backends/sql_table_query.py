"""
core/query_backends/sql_table_query.py

通用 SQL 数据库查询后端 —— 支持 PostgreSQL 和 SQLite。

特性：
  - 动态构建 WHERE 子句，支持 ILIKE 模糊匹配（PG）/ LIKE 忽略大小写（SQLite）
  - search_fields 定义允许用于 WHERE 条件的字段名列表
  - return_fields 定义返回字段（空=全部返回）
  - get_tables() / get_fields() 支持 UI 下拉联动（无需实例化查询）
  - 每次 search() 重新建立短连接（无连接池，适合低频查询场景）

config 结构示例（PostgreSQL）：
  type: sql_table
  enabled: true
  name: 物料清单查询
  db_type: postgresql
  host: 127.0.0.1
  port: 5432
  dbname: erp_db
  user: reader
  password: "secret"
  table: product_list
  search_fields:
    - product_code
    - product_name
  return_fields:
    - product_code
    - product_name
    - spec
    - unit
  match_mode: ilike      # ilike（模糊）| exact（精确）
  timeout: 15

config 结构示例（SQLite）：
  type: sql_table
  db_type: sqlite
  db_path: C:/data/my_database.db
  table: products
  search_fields:
    - name
  return_fields: []      # 空=返回全部字段
  match_mode: ilike
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from core.query_base import QueryBase, register_backend
from core.i18n import _t

logger = logging.getLogger(__name__)

# ── 驱动可用性检测 ────────────────────────────────────────────────

_HAS_PSYCOPG2 = False
_HAS_SQLITE   = True   # sqlite3 是标准库，永远可用

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    pass

import sqlite3  # noqa: E402  (标准库，一定成功)


# ── 工具函数 ──────────────────────────────────────────────────────

def _sqlite_regexp(expr: str, item: Any) -> bool:
    """SQLite 注册的 REGEXP 函数，执行大小写不敏感的正则匹配"""
    if item is None:
        return False
    try:
        return bool(re.search(expr, str(item), re.IGNORECASE))
    except Exception:
        return False


def _quote_ident(name: str, db_type: str) -> str:
    """对字段名/表名加引号，防止 SQL 注入和关键字冲突。"""
    if db_type == "sqlite":
        return f'"{name}"'
    # postgresql
    return f'"{name}"'


def _make_connection(cfg: dict) -> Any:
    """
    根据 db_type 建立数据库连接。
    返回 DBAPI 连接对象，失败时抛出异常。
    """
    db_type = cfg.get("db_type", "postgresql").lower()

    if db_type == "sqlite":
        db_path = cfg.get("db_path", "")
        if not db_path:
            raise ValueError(_t("SQLite 配置缺少 db_path"))
        conn = sqlite3.connect(db_path, timeout=cfg.get("timeout", 15))
        conn.create_function("regexp", 2, _sqlite_regexp)
        # 让查询结果可按列名访问
        conn.row_factory = sqlite3.Row
        return conn

    elif db_type == "postgresql":
        if not _HAS_PSYCOPG2:
            raise ImportError(
                "PostgreSQL 驱动未安装，请运行: pip install psycopg2-binary"
            )
        host     = cfg.get("host", "localhost")
        port     = int(cfg.get("port", 5432))
        dbname   = cfg.get("dbname", "")
        user     = cfg.get("user", "")
        password = cfg.get("password", "")
        timeout  = int(cfg.get("timeout", 15))
        return psycopg2.connect(
            host=host, port=port, dbname=dbname,
            user=user, password=password,
            connect_timeout=timeout,
        )
    else:
        raise ValueError(_t("不支持的数据库类型: {db_type}（仅支持 postgresql / sqlite）").format(db_type=db_type))


def get_tables_from_cfg(cfg: dict) -> list[str]:
    """
    临时建立连接，获取数据库中所有用户表/视图名列表。
    UI 辅助方法，独立于 SqlTableQuery 实例调用。
    """
    db_type = cfg.get("db_type", "postgresql").lower()
    conn = _make_connection(cfg)
    try:
        cur = conn.cursor()
        if db_type == "sqlite":
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        else:  # postgresql
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_type IN ('BASE TABLE','VIEW') "
                "ORDER BY table_name"
            )
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]
    finally:
        conn.close()


def get_fields_from_cfg(cfg: dict, table: str) -> list[str]:
    """
    临时建立连接，获取指定表的所有字段名。
    UI 辅助方法，独立于 SqlTableQuery 实例调用。
    """
    db_type = cfg.get("db_type", "postgresql").lower()
    conn = _make_connection(cfg)
    try:
        cur = conn.cursor()
        if db_type == "sqlite":
            cur.execute(f'PRAGMA table_info("{table}")')
            rows = cur.fetchall()
            cur.close()
            return [r[1] for r in rows]   # column 1 = name
        else:  # postgresql
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s "
                "ORDER BY ordinal_position",
                (table,),
            )
            rows = cur.fetchall()
            cur.close()
            return [r[0] for r in rows]
    finally:
        conn.close()


# ── 查询后端类 ────────────────────────────────────────────────────

@register_backend("sql_table")
class SqlTableQuery(QueryBase):
    """
    通用 SQL 表/视图查询后端。

    每次 search() 新建短连接 → 执行查询 → 关闭连接。
    适合低频交互式查询，不适合高并发批量场景。

    fields  示例: {"product_code": "C12345", "product_name": "螺栓"}
    params  示例: {"limit": 50}
    """

    def __init__(
        self,
        db_type:       str,
        table:         str,
        search_fields: list[str],
        return_fields: list[str],
        match_mode:    str  = "ilike",
        timeout:       int  = 15,
        # PostgreSQL 专用
        host:     str = "localhost",
        port:     int = 5432,
        dbname:   str = "",
        user:     str = "",
        password: str = "",
        # SQLite 专用
        db_path:  str = "",
    ):
        self._db_type       = db_type.lower()
        self._table         = table
        self._search_fields = search_fields   # 允许用作 WHERE 条件的字段
        self._return_fields = return_fields   # 要返回的字段（空=全部）
        self._match_mode    = match_mode.lower()  # "ilike" | "exact"
        self._timeout       = timeout

        # PostgreSQL 连接参数
        self._host     = host
        self._port     = port
        self._dbname   = dbname
        self._user     = user
        self._password = password

        # SQLite 连接参数
        self._db_path  = db_path

        self._status            = "connecting"
        self._discovered_fields: list[str] = []

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "SqlTableQuery":
        db_type = config.get("db_type", "postgresql").lower()
        return cls(
            db_type       = db_type,
            table         = config.get("table", ""),
            search_fields = config.get("search_fields", []),
            return_fields = config.get("return_fields", []),
            match_mode    = config.get("match_mode", "ilike"),
            timeout       = int(config.get("timeout", 15)),
            host          = config.get("host", "localhost"),
            port          = int(config.get("port", 5432)),
            dbname        = config.get("dbname", ""),
            user          = config.get("user", ""),
            password      = config.get("password", ""),
            db_path       = config.get("db_path", ""),
        )

    # ── 连接检查 ─────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        建立测试连接，验证表存在，顺带发现字段列表。
        返回 True 表示就绪。
        """
        if self._db_type == "postgresql" and not _HAS_PSYCOPG2:
            self._status = "error"
            logger.error("psycopg2 未安装，PostgreSQL 后端不可用")
            return False

        if not self._table:
            self._status = "error"
            logger.error("sql_table 后端：table 配置为空")
            return False

        try:
            cfg = self._build_cfg()
            conn = _make_connection(cfg)
            try:
                cur = conn.cursor()
                # 验证表存在：用 LIMIT 0 查询一下
                q = self._quote(self._table)
                cur.execute(f"SELECT * FROM {q} LIMIT 1")
                col_names = [desc[0] for desc in cur.description]
                cur.close()
                self._discovered_fields = col_names
            finally:
                conn.close()

            self._status = "ready"
            logger.info(
                "sql_table 后端就绪: db_type=%s table=%s fields=%s",
                self._db_type, self._table, col_names,
            )
            return True

        except Exception as exc:
            self._status = "error"
            logger.error("sql_table 连接失败: %s", exc)
            return False

    # ── 查询接口 ─────────────────────────────────────────────────

    def search(
        self,
        fields: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[list[dict], float]:
        """
        动态构建 SELECT ... FROM table WHERE ... 查询。

        fields: {field_name: value}  只使用在 search_fields 中声明的字段
        params: limit (int, 默认 100), use_regex (bool, 默认 False)
        """
        limit = min(int(params.get("limit", 100)), 5000)
        use_regex = bool(params.get("use_regex", False))

        # 只使用声明为 search_fields 的字段，且值不为空
        active: dict[str, str] = {}
        for k, v in fields.items():
            if self._search_fields and k not in self._search_fields:
                continue
            sv = str(v).strip() if v is not None else ""
            if sv:
                active[k] = sv

        # 构建 SELECT 列表
        if self._return_fields:
            select_cols = ", ".join(
                self._quote(f) for f in self._return_fields
            )
        else:
            select_cols = "*"

        # 构建 WHERE 子句
        conditions: list[str] = []
        values: list[Any] = []

        for field_name, value in active.items():
            qf = self._quote(field_name)
            if use_regex:
                if self._db_type == "postgresql":
                    conditions.append(f"{qf} ~* %s")
                else:
                    conditions.append(f"{qf} REGEXP ?")
                values.append(value)
            elif self._match_mode == "exact":
                if self._db_type == "postgresql":
                    conditions.append(f"{qf} = %s")
                else:
                    conditions.append(f"{qf} = ?")
                values.append(value)
            else:
                # 模糊/通配符检索
                has_wildcards = any(c in value for c in ('*', '?', '%', '_'))
                if has_wildcards:
                    val_converted = value.replace('*', '%').replace('?', '_')
                else:
                    val_converted = f"%{value}%"

                if self._db_type == "postgresql":
                    conditions.append(f"{qf} ILIKE %s")
                else:
                    # SQLite: LIKE + PRAGMA case_insensitive_like
                    conditions.append(f"{qf} LIKE ? COLLATE NOCASE")
                values.append(val_converted)

        # 解析 params 中的 filters 条件并并入 WHERE 子句
        filters = params.get("filters") or {}
        for k, v in filters.items():
            base_field = k
            suffix = ""
            for sfx in ("_min", "_max", "_from", "_to"):
                if k.endswith(sfx):
                    base_field = k[:-len(sfx)]
                    suffix = sfx
                    break

            if self._search_fields and base_field not in self._search_fields:
                continue

            qf = self._quote(base_field)
            if suffix in ("_min", "_from"):
                if self._db_type == "postgresql":
                    conditions.append(f"{qf} >= %s")
                else:
                    conditions.append(f"{qf} >= ?")
                values.append(v)
            elif suffix in ("_max", "_to"):
                if self._db_type == "postgresql":
                    conditions.append(f"{qf} <= %s")
                else:
                    conditions.append(f"{qf} <= ?")
                values.append(v)
            else:
                # 精确匹配（下拉框、复选框等）
                if self._db_type == "postgresql":
                    conditions.append(f"{qf} = %s")
                else:
                    conditions.append(f"{qf} = ?")
                values.append(v)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        qt    = self._quote(self._table)

        if self._db_type == "postgresql":
            sql = f"SELECT {select_cols} FROM {qt} {where} LIMIT %s"
        else:
            sql = f"SELECT {select_cols} FROM {qt} {where} LIMIT ?"
        values.append(limit)

        t0 = time.perf_counter()
        try:
            cfg  = self._build_cfg()
            conn = _make_connection(cfg)
            try:
                if self._db_type == "postgresql":
                    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                else:
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()

                cur.execute(sql, values)
                rows = cur.fetchall()
                cur.close()
            finally:
                conn.close()
        except Exception as exc:
            logger.error(
                "sql_table 查询失败: %s | SQL: %s | VALUES: %s",
                exc, sql, values,
            )
            raise

        elapsed = round(time.perf_counter() - t0, 3)

        # 统一转换为普通 dict，处理特殊类型
        results: list[dict] = []
        for row in rows:
            r: dict = {}
            for k, v in dict(row).items():
                if v is None:
                    r[k] = ""
                elif hasattr(v, "isoformat"):
                    r[k] = v.isoformat(sep=" ", timespec="seconds")
                else:
                    r[k] = v
            results.append(r)

        logger.info(
            "sql_table 查询: table=%s 条件=%s 结果=%d 耗时=%.3fs",
            self._table, list(active.keys()), len(results), elapsed,
        )
        return results, elapsed

    # ── 状态与字段 ───────────────────────────────────────────────

    def get_status(self) -> str:
        return self._status

    def get_available_fields(self) -> list[tuple[str, str]]:
        """
        返回可用字段列表 [(field_name, display_name)]。
        优先返回 connect() 时发现的字段；未连接则返回 return_fields 配置。
        """
        if self._discovered_fields:
            return [(f, f) for f in self._discovered_fields]
        if self._return_fields:
            return [(f, f) for f in self._return_fields]
        return []

    # ── 私有辅助 ─────────────────────────────────────────────────

    def _quote(self, name: str) -> str:
        return _quote_ident(name, self._db_type)

    def _build_cfg(self) -> dict:
        """将实例属性打包为 _make_connection 可用的 dict。"""
        return {
            "db_type":  self._db_type,
            "host":     self._host,
            "port":     self._port,
            "dbname":   self._dbname,
            "user":     self._user,
            "password": self._password,
            "db_path":  self._db_path,
            "timeout":  self._timeout,
        }
