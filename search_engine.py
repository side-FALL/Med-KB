"""医学教材知识库 — 搜索引擎模块

包含混合检索（向量 + BM25）、Embedding 缓存、分词逻辑。
"""

import re

import numpy as np
import streamlit as st
from openai import OpenAI

from config import MODEL_PROVIDERS, get_api_key


# ── BM25 分词 ───────────────────────────────────────────

_STOPWORDS = set("的了是在不有我这个们他她它们和与或但而如果因为所以可以已经正在".replace(" ", ""))
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+|\d+")


def _tokenize(text: str) -> list[str]:
    """中文分词，用于 BM25 评分。"""
    text = re.sub(r"[的了是在不有我这个们]", " ", text)
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]
    chars = re.findall(r"[\u4e00-\u9fff]", text.lower())
    for i in range(len(chars) - 1):
        bg = chars[i] + chars[i + 1]
        if bg not in _STOPWORDS:
            tokens.append(bg)
    return tokens


# ── Embedding 客户端 ────────────────────────────────────

@st.cache_resource
def _get_embed_client() -> OpenAI:
    """获取 Embedding API 客户端（单例）。"""
    api_key = get_api_key("siliconflow")
    return OpenAI(
        api_key=api_key,
        base_url=MODEL_PROVIDERS["siliconflow"]["embed_url"],
        timeout=15.0,
    )


# ── Embedding 缓存 ──────────────────────────────────────

@st.cache_data(max_entries=500, ttl=3600 * 24)
def get_embedding(text: str) -> bytes:
    """获取 Embedding 向量，使用 Streamlit 缓存持久化。"""
    try:
        client = _get_embed_client()
        r = client.embeddings.create(model="BAAI/bge-m3", input=[text])
        vec = np.array(r.data[0].embedding, dtype=np.float32)
        return (vec / np.linalg.norm(vec)).tobytes()
    except Exception as e:
        st.error(f"向量化失败: {e}")
        return b""


# ── 混合检索 ────────────────────────────────────────────

def search(
    text: str,
    embeddings: np.ndarray,
    documents: list[str],
    metadatas: list[dict],
    k: int = 10,
    alpha: float = 0.7,
) -> list[dict]:
    """混合检索：向量相似度 + BM25 关键词匹配。

    Args:
        text: 查询文本
        embeddings: 归一化后的 embedding 矩阵
        documents: 文档文本列表
        metadatas: 文档元数据列表
        k: 返回结果数
        alpha: 向量权重 (1.0=纯向量, 0.0=纯关键词)

    Returns:
        按相关性排序的命中结果列表
    """
    # 获取 Embedding（自动缓存）
    embedding_bytes = get_embedding(text)
    if not embedding_bytes:
        return []
    qvec = np.frombuffer(embedding_bytes, dtype=np.float32)

    vec_scores = embeddings @ qvec

    # 向量 top-50 候选
    candidate_k = min(50, len(documents))
    top_candidates = np.argsort(vec_scores)[-candidate_k:][::-1]

    # BM25 只对候选计算
    query_tokens = set(_tokenize(text))
    bm25_scores = np.zeros(len(documents), dtype=np.float32)
    if query_tokens:
        q_len = len(query_tokens)
        for local_idx in top_candidates:
            doc_tokens = set(_tokenize(documents[local_idx]))
            bm25_scores[local_idx] = len(query_tokens & doc_tokens) / q_len

    hybrid_scores = alpha * vec_scores + (1 - alpha) * bm25_scores
    top = np.argsort(hybrid_scores)[-k:][::-1]
    hits = []
    for local_idx in top:
        s = float(hybrid_scores[local_idx])
        hits.append({
            "text": documents[local_idx][:500],
            "book": metadatas[local_idx].get("book", "?"),
            "chapter": metadatas[local_idx].get("chapter", "?"),
            "similarity": round(s, 4),
            "vector_sim": round(float(vec_scores[local_idx]), 4),
            "bm25_score": round(float(bm25_scores[local_idx]), 4),
        })
    return hits
