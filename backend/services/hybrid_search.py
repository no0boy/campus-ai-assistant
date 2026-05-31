"""
混合检索：Dense（向量语义）+ Sparse（BM25 关键词）+ RRF 融合
- rank_bm25: 纯 Python 实现，零外部服务，~10MB 内存
- jieba: 中文分词
- RRF (Reciprocal Rank Fusion): 分数无关的排序融合算法
"""

import jieba
from rank_bm25 import BM25Okapi
import config

# 延迟导入，避免循环依赖
_collection = None

def _get_collection():
    global _collection
    if _collection is None:
        from services.rag_service import collection
        _collection = collection
    return _collection


# ========== 全局 BM25 索引 ==========
_bm25: BM25Okapi = None
_bm25_docs: list[dict] = []  # [{id, content, metadata}, ...]
_index_ready = False


def _tokenize(text: str) -> list[str]:
    """中文分词 + 去空"""
    words = jieba.cut(text)
    return [w.strip() for w in words if len(w.strip()) >= 1]


def build_index() -> int:
    """
    从 ChromaDB 读取全部文档片段，构建 BM25 索引
    返回索引文档数。文档变更（上传/删除）后需调用此函数重建
    """
    global _bm25, _bm25_docs, _index_ready
    try:
        results = _get_collection().get(include=["documents", "metadatas"])
        docs = results.get("documents") or []
        ids = results.get("ids") or []
        metas = results.get("metadatas") or []

        if not docs:
            _bm25 = None
            _bm25_docs = []
            _index_ready = False
            print("[BM25] 知识库为空，跳过索引构建")
            return 0

        _bm25_docs = []
        tokenized_corpus = []
        for i, doc in enumerate(docs):
            _bm25_docs.append({
                "id": ids[i] if i < len(ids) else f"doc_{i}",
                "content": doc,
                "metadata": metas[i] if i < len(metas) else {}
            })
            tokenized_corpus.append(_tokenize(doc))

        _bm25 = BM25Okapi(tokenized_corpus)
        _index_ready = True
        print(f"[BM25] 索引构建完成，共 {len(_bm25_docs)} 条文档片段")
        return len(_bm25_docs)
    except Exception as e:
        _bm25 = None
        _bm25_docs = []
        _index_ready = False
        print(f"[BM25] 索引构建失败: {e}")
        return 0


def bm25_search(question: str, top_k: int = None) -> list[dict]:
    """
    纯 BM25 关键词检索
    返回: [{content, title, score, source}, ...]
    """
    global _bm25, _bm25_docs

    if not _index_ready or not _bm25:
        return []

    top_k = top_k or config.BM25_TOP_K
    tokens = _tokenize(question)
    scores = _bm25.get_scores(tokens)

    # 取 Top-K
    indexed = [(i, s) for i, s in enumerate(scores) if s > 0]
    indexed.sort(key=lambda x: x[1], reverse=True)

    results = []
    for i, score in indexed[:top_k]:
        doc = _bm25_docs[i]
        # BM25 分数归一化到 [0,1]（除以最大值）
        max_score = indexed[0][1] if indexed else 1
        normalized = round(score / max_score, 3) if max_score > 0 else 0
        results.append({
            "content": doc["content"],
            "title": doc["metadata"].get("doc_title", "未知文档"),
            "score": normalized,
            "source": "bm25"
        })
    return results


def hybrid_search(question: str, top_k: int = None) -> list[dict]:
    """
    混合检索主入口
    1. Dense（向量检索）+ Sparse（BM25）双路召回
    2. RRF 融合排序
    3. 返回 Top-K 片段

    返回格式与 retrieve_context 一致: [{content, title, score}, ...]
    """
    if not config.HYBRID_SEARCH_ENABLED:
        from services.rag_service import retrieve_context
        return retrieve_context(question, top_k or config.FINAL_TOP_K)

    top_k = top_k or config.FINAL_TOP_K
    recall_k = top_k * 3  # 两路各召回 3 倍数量用于融合

    # 双路检索
    try:
        from services.rag_service import retrieve_context
        dense_results = retrieve_context(question, recall_k)
    except Exception:
        dense_results = []

    try:
        sparse_results = bm25_search(question, recall_k)
    except Exception:
        sparse_results = []

    # 如果 BM25 不可用，退回纯向量检索
    if not sparse_results:
        return dense_results[:top_k]

    # 如果向量检索不可用，退回纯 BM25
    if not dense_results:
        return sparse_results[:top_k]

    # ====== RRF 融合 ======
    rrf_scores = {}
    content_map = {}

    for rank, item in enumerate(dense_results):
        key = item["content"][:120]  # 用前 120 字符做去重 key
        rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (config.RRF_K + rank + 1)
        content_map[key] = {**item, "source": item.get("source", "dense")}

    for rank, item in enumerate(sparse_results):
        key = item["content"][:120]
        rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (config.RRF_K + rank + 1)
        if key not in content_map:
            content_map[key] = {**item, "source": item.get("source", "bm25")}
        else:
            # 双路命中：标记为 hybrid
            content_map[key]["source"] = "hybrid"

    # 按 RRF 分数排序
    sorted_keys = sorted(rrf_scores, key=rrf_scores.get, reverse=True)
    results = []
    # 归一化：RRF 分数映射到 [0, 1]
    max_score = max(rrf_scores.values()) if rrf_scores else 1.0
    for i, key in enumerate(sorted_keys[:top_k]):
        item = content_map[key]
        normalized = round(rrf_scores[key] / max_score, 3) if max_score > 0 else 0
        results.append({
            "content": item["content"],
            "title": item["title"],
            "score": normalized if normalized <= 1.0 else round(1.0 / (1.0 + 1.0 / normalized), 3),
            "source": item.get("source", "hybrid"),
        })

    return results


def get_index_status() -> dict:
    """获取 BM25 索引状态"""
    return {
        "enabled": config.HYBRID_SEARCH_ENABLED,
        "index_ready": _index_ready,
        "doc_count": len(_bm25_docs),
        "rrf_k": config.RRF_K,
    }
