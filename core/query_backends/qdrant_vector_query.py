"""
core/query_backends/qdrant_vector_query.py

Qdrant 向量数据库直连查询后端插件。
直接调用 BGE-M3 嵌入 API + Qdrant 向量检索，无需中间服务。

嵌入 API 格式（Infinity / 自部署 BGE-M3）：
    POST {embed_base_url}/embeddings
    Body: {"input": ["文本"], "return_dense": true, "return_sparse": true}
    Response: {"data": [{"index": 0, "embedding": [...], "sparse": {"indices":[], "values":[]}}]}

Qdrant 连接：使用 qdrant-client，支持 gRPC（prefer_grpc=True）或 HTTP。
    gRPC 端口一般为 6334，HTTP 端口一般为 6333。

支持的查询模式：
    dense   — 仅稠密向量（HNSW COSINE）
    sparse  — 仅稀疏向量（BM42/SPLADE）
    hybrid  — 稠密 + 稀疏，通过 RRF 融合（推荐）

config.yaml 配置示例：
    query_modules:
      my_qdrant:
        type: qdrant_vector
        enabled: true
        name: 物料向量查询
        qdrant_host: 127.0.0.1
        qdrant_port: 6334
        qdrant_collection: materials
        qdrant_api_key: ""
        qdrant_prefer_grpc: true
        embed_base_url: http://127.0.0.1:7997
        embed_api_key: ""
        embed_model: BAAI/bge-m3
        embed_timeout: 120
        search_mode: hybrid
        score_threshold: 0.0
        limit: 20
        timeout: 30
        return_fields: []          # 空列表=返回全部字段
        valid_status_field: material_status
        valid_status_value: 有效

作为插件自动加载，注册类型名 "qdrant_vector"。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests as _requests

from core.query_base import QueryBase, register_backend
from core.i18n import _t

logger = logging.getLogger(__name__)


def _try_import_qdrant():
    """懒导入 qdrant_client，给出友好错误。"""
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            SparseVector, Prefetch, FusionQuery, Fusion,
            Filter, FieldCondition, MatchValue, Range,
        )
        return QdrantClient, SparseVector, Prefetch, FusionQuery, Fusion, Filter, FieldCondition, MatchValue, Range
    except ImportError:
        raise ImportError(
            "缺少 qdrant-client 包。\n"
            "请运行: pip install qdrant-client"
        )


@register_backend("qdrant_vector")
class QdrantVectorQuery(QueryBase):
    """
    直连 Qdrant + BGE-M3 嵌入服务的向量查询后端。

    每个实例对应一个独立的 Qdrant collection，可建立多个查询模块。
    """

    def __init__(
        self,
        # Qdrant 连接参数
        qdrant_host: str,
        qdrant_port: int,
        qdrant_collection: str,
        qdrant_api_key: str = "",
        qdrant_prefer_grpc: bool = True,
        # BGE-M3 嵌入服务参数
        embed_base_url: str = "",
        embed_api_key: str = "",
        embed_model: str = "BAAI/bge-m3",
        embed_timeout: int = 120,
        # 查询行为参数
        search_mode: str = "hybrid",        # dense / sparse / hybrid
        score_threshold: float = 0.0,
        limit: int = 20,
        timeout: int = 30,
        # 字段配置
        return_fields: list[str] | None = None,   # None 或空列表 = 返回全部
    ):
        self.qdrant_host       = qdrant_host
        self.qdrant_port       = qdrant_port
        self.qdrant_collection = qdrant_collection
        self.qdrant_api_key    = qdrant_api_key
        self.qdrant_prefer_grpc = qdrant_prefer_grpc

        self.embed_base_url = embed_base_url.rstrip("/")
        self.embed_api_key  = embed_api_key
        self.embed_model    = embed_model
        self.embed_timeout  = embed_timeout

        self.search_mode      = search_mode
        self.score_threshold  = score_threshold
        self.default_limit    = limit
        self.timeout          = timeout

        self.return_fields        = return_fields or []

        self._status       = "connecting"
        self._client       = None   # QdrantClient，连接后赋值
        self._cached_fields: list[tuple[str, str]] = []  # 自动发现的字段列表

        # Embedding HTTP session（Keep-Alive 复用）
        self._embed_session: _requests.Session | None = None

    # ── 工厂方法 ────────────────────────────────────────────────────────
    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "QdrantVectorQuery":
        return cls(
            qdrant_host        = config.get("qdrant_host", "127.0.0.1"),
            qdrant_port        = int(config.get("qdrant_port", 6334)),
            qdrant_collection  = config.get("qdrant_collection", ""),
            qdrant_api_key     = config.get("qdrant_api_key", ""),
            qdrant_prefer_grpc = bool(config.get("qdrant_prefer_grpc", True)),
            embed_base_url     = config.get("embed_base_url", ""),
            embed_api_key      = config.get("embed_api_key", ""),
            embed_model        = config.get("embed_model", "BAAI/bge-m3"),
            embed_timeout      = int(config.get("embed_timeout", 120)),
            search_mode        = config.get("search_mode", "hybrid"),
            score_threshold    = float(config.get("score_threshold", 0.0)),
            limit              = int(config.get("limit", 20)),
            timeout            = int(config.get("timeout", 30)),
            return_fields      = config.get("return_fields") or [],
        )

    # ── 内部工具 ─────────────────────────────────────────────────────────

    def _get_embed_session(self) -> _requests.Session:
        if self._embed_session is None:
            self._embed_session = _requests.Session()
            headers = {"Content-Type": "application/json"}
            if self.embed_api_key:
                headers["Authorization"] = f"Bearer {self.embed_api_key}"
            self._embed_session.headers.update(headers)
            adapter = _requests.adapters.HTTPAdapter(
                pool_connections=1, pool_maxsize=4
            )
            self._embed_session.mount("http://", adapter)
            self._embed_session.mount("https://", adapter)
        return self._embed_session

    def _embed(self, text: str) -> dict[str, Any]:
        """
        调用 BGE-M3 嵌入 API，返回 {"dense": [...], "sparse": {token_id: weight}}。

        API 接口（Infinity 格式）：
            POST {embed_base_url}/embeddings
            {"input": ["文本"], "return_dense": true, "return_sparse": true}
        """
        safe_text = text.strip() if text.strip() else "无"
        sess = self._get_embed_session()
        resp = sess.post(
            f"{self.embed_base_url}/embeddings",
            json={
                "input": [safe_text],
                "return_dense": True,
                "return_sparse": True,
            },
            timeout=self.embed_timeout,
        )
        resp.raise_for_status()
        item = resp.json()["data"][0]
        sparse_raw = item.get("sparse") or {}
        indices = sparse_raw.get("indices", [])
        values  = sparse_raw.get("values",  [])
        return {
            "dense":  item["embedding"],
            "sparse": dict(zip(indices, values)),
        }

    def _build_qdrant_filter(self, filters: dict) -> Any:
        """
        将简单键值对字典转换为 Qdrant Filter。

        支持格式：
            {"material_status": "有效"}       → MatchValue 精确匹配
            {"latest_price_min": 100}          → Range(gte=100)
            {"latest_price_max": 500}          → Range(lte=500)
            {"create_date_from": "2024-01-01"} → DatetimeRange(gte=...)
            {"create_date_to":   "2024-12-31"} → DatetimeRange(lte=...)
        """
        _, _, _, _, _, Filter, FieldCondition, MatchValue, Range = _try_import_qdrant()
        try:
            from qdrant_client.models import DatetimeRange
        except ImportError:
            DatetimeRange = None

        must = []
        for field, value in filters.items():
            if value is None or value == "":
                continue
            if field.endswith("_min"):
                base_field = field[:-4]
                must.append(FieldCondition(key=base_field, range=Range(gte=float(value))))
            elif field.endswith("_max"):
                base_field = field[:-4]
                must.append(FieldCondition(key=base_field, range=Range(lte=float(value))))
            elif field == "create_date_from" and DatetimeRange:
                must.append(FieldCondition(key="create_date", range=DatetimeRange(gte=value)))
            elif field == "create_date_to" and DatetimeRange:
                must.append(FieldCondition(key="create_date", range=DatetimeRange(lte=value)))
            else:
                must.append(FieldCondition(key=field, match=MatchValue(value=str(value))))

        if not must:
            return None
        return Filter(must=must)

    def _discover_fields(self) -> list[tuple[str, str]]:
        """
        从 Qdrant collection 中自动发现 payload 字段。
        通过 scroll 取少量点，收集所有 payload key。
        返回 [(field_name, display_name), ...]。
        始终在最前面添加 score 字段。
        """
        if self._client is None:
            return [("score", "相似度")]
        try:
            results, _ = self._client.scroll(
                collection_name=self.qdrant_collection,
                limit=5,
                with_payload=True,
                with_vectors=False,
            )
            field_set: set[str] = set()
            for point in results:
                if point.payload:
                    field_set.update(point.payload.keys())

            # score 始终排第一
            fields = [("score", "相似度")]
            for f in sorted(field_set):
                fields.append((f, f))  # 显示名默认等于字段名
            return fields
        except Exception as exc:
            logger.warning("自动发现字段失败: %s", exc)
            return [("score", "相似度")]

    # ── 连接 ─────────────────────────────────────────────────────────────
    def connect(self) -> bool:
        """
        1. 连接 Qdrant
        2. 测试 BGE-M3 嵌入服务
        3. 自动发现 collection 字段
        """
        QdrantClient, *_ = _try_import_qdrant()

        # 连接 Qdrant
        try:
            self._client = QdrantClient(
                host=self.qdrant_host,
                port=self.qdrant_port,
                api_key=self.qdrant_api_key or None,
                https=False,
                prefer_grpc=self.qdrant_prefer_grpc,
                timeout=self.timeout,
            )
            self._client.get_collections()
            logger.info(
                "已连接到 Qdrant %s:%s  collection=%s",
                self.qdrant_host, self.qdrant_port, self.qdrant_collection,
            )
        except Exception as exc:
            self._status = "error"
            logger.error("Qdrant 连接失败: %s", exc)
            return False

        # 测试嵌入服务（发送热身请求）
        if self.embed_base_url:
            try:
                sess = self._get_embed_session()
                resp = sess.post(
                    f"{self.embed_base_url}/embeddings",
                    json={"input": ["热身"], "return_dense": True, "return_sparse": False},
                    timeout=15,
                )
                resp.raise_for_status()
                logger.info("BGE-M3 嵌入服务已就绪: %s", self.embed_base_url)
            except Exception as exc:
                self._status = "error"
                logger.error("BGE-M3 嵌入服务连接失败: %s", exc)
                return False

        # 自动发现字段
        self._cached_fields = self._discover_fields()
        logger.info(
            "已发现 %d 个 payload 字段: %s",
            len(self._cached_fields),
            [f[0] for f in self._cached_fields],
        )

        self._status = "ready"
        return True

    # ── 查询 ─────────────────────────────────────────────────────────────
    def search(
        self,
        fields: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[list[dict], float]:
        """
        执行向量检索。

        Parameters
        ----------
        fields : dict
            查询字段。应包含 "query" 键（查询文本）。
        params : dict
            额外参数：
                limit (int)       : 最大返回数量
                valid_only (bool) : 是否前置过滤有效状态
                score_threshold (float) : 相似度阈值（覆盖默认值）
                mode (str)        : dense / sparse / hybrid（覆盖默认值）
                filters (dict)    : 任意键值对过滤（追加到 valid_only 之后）

        Returns
        -------
        (results, elapsed)
        """
        query_text = fields.get("query", "").strip()
        if not query_text:
            return [], 0.0

        if self._status != "ready" or self._client is None:
            raise RuntimeError(_t("查询后端未就绪（状态: {status}）").format(status=self._status))

        _, SparseVector, Prefetch, FusionQuery, Fusion, *_ = _try_import_qdrant()

        limit           = int(params.get("limit", self.default_limit))
        mode            = params.get("mode", self.search_mode)
        score_threshold = float(params.get("score_threshold", self.score_threshold))

        # ── 构建过滤条件 ──────────────────────────────────────────────
        filter_dict: dict = {}
        extra_filters = params.get("filters", {})
        if isinstance(extra_filters, dict):
            filter_dict.update(extra_filters)
        qdrant_filter = self._build_qdrant_filter(filter_dict) if filter_dict else None

        # ── 确定需要返回的 payload 字段 ───────────────────────────────
        with_payload: bool | list[str]
        if self.return_fields:
            # 过滤掉 score（它是向量相似度，不来自 payload）
            payload_fields = [f for f in self.return_fields if f != "score"]
            with_payload = payload_fields if payload_fields else True
        else:
            with_payload = True   # 返回所有字段

        t0 = time.perf_counter()
        try:
            # ── 获取向量 ─────────────────────────────────────────────
            emb = self._embed(query_text)
            dense_vec   = emb["dense"]
            sparse_dict = emb["sparse"]

            # ── 执行查询 ─────────────────────────────────────────────
            if mode == "dense" or not sparse_dict:
                results = self._client.query_points(
                    collection_name=self.qdrant_collection,
                    query=dense_vec,
                    using="dense",
                    limit=limit,
                    query_filter=qdrant_filter,
                    score_threshold=score_threshold if score_threshold > 0 else None,
                    with_payload=with_payload,
                )
            elif mode == "sparse":
                sv = SparseVector(
                    indices=list(sparse_dict.keys()),
                    values=list(sparse_dict.values()),
                )
                results = self._client.query_points(
                    collection_name=self.qdrant_collection,
                    query=sv,
                    using="sparse",
                    limit=limit,
                    query_filter=qdrant_filter,
                    with_payload=with_payload,
                )
            else:
                # hybrid（默认）：RRF 融合
                sv = SparseVector(
                    indices=list(sparse_dict.keys()),
                    values=list(sparse_dict.values()),
                )
                results = self._client.query_points(
                    collection_name=self.qdrant_collection,
                    prefetch=[
                        Prefetch(query=dense_vec, using="dense",  limit=limit * 2,
                                 filter=qdrant_filter),
                        Prefetch(query=sv,        using="sparse", limit=limit * 2,
                                 filter=qdrant_filter),
                    ],
                    query=FusionQuery(fusion=Fusion.RRF),
                    limit=limit,
                    query_filter=qdrant_filter,
                    with_payload=with_payload,
                )

        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 3)
            logger.error("Qdrant 向量查询失败（耗时 %.3fs）: %s", elapsed, exc)
            raise

        elapsed = round(time.perf_counter() - t0, 3)

        # ── 展平结果：score + payload 所有字段 ───────────────────────
        hits: list[dict] = []
        for point in results.points:
            row: dict = {"score": round(point.score, 4)}
            payload = point.payload or {}
            if isinstance(with_payload, list):
                # 只包含请求的字段
                for f in with_payload:
                    row[f] = payload.get(f, "")
            else:
                row.update(payload)
            hits.append(row)

        logger.info(
            "Qdrant 查询完成: query=%r  mode=%s  hits=%d  elapsed=%.3fs",
            query_text[:40], mode, len(hits), elapsed,
        )
        return hits, elapsed

    # ── 元信息 ───────────────────────────────────────────────────────────
    def get_status(self) -> str:
        return self._status

    def get_available_fields(self) -> list[tuple[str, str]]:
        """
        返回 collection 中发现的字段列表（在 connect() 时自动获取）。
        若尚未连接，尝试实时拉取；否则返回缓存。
        """
        if not self._cached_fields and self._client is not None:
            self._cached_fields = self._discover_fields()
        # 未连接时返回空列表，让调用方走 config 兜底
        return self._cached_fields.copy()

    def refresh_fields(self) -> list[tuple[str, str]]:
        """
        强制重新从 Qdrant collection 获取字段列表（供 UI 「刷新字段」按钮调用）。
        """
        self._cached_fields = self._discover_fields()
        return self._cached_fields.copy()
