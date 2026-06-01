"""🏥 医学教材知识库 — 优化版（教材选择在主界面）"""
import streamlit as st
import os, json, numpy as np
from pathlib import Path
import requests
from openai import OpenAI

st.set_page_config(page_title="医学教材知识库", page_icon="🏥", layout="wide")

# ── 自定义样式 ────────────────────────────────────
st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }
.main .block-container { max-width: 900px; padding: 2rem 1rem; }

.main-title { text-align: center; color: white; font-size: 2.5rem; font-weight: 800;
    margin-bottom: 0.5rem; text-shadow: 2px 2px 4px rgba(0,0,0,0.3); }
.main-subtitle { text-align: center; color: rgba(255,255,255,0.85); font-size: 1rem; margin-bottom: 1.5rem; }

/* 搜索框 */
.stTextInput > div > div > input { border: none; border-radius: 25px; padding: 1rem 1.5rem;
    font-size: 1.1rem; box-shadow: 0 4px 20px rgba(0,0,0,0.15); }
.stButton > button { border-radius: 25px; padding: 0.5rem 2rem; font-weight: 600;
    box-shadow: 0 4px 15px rgba(0,0,0,0.2); }

/* 教材选择卡片 */
.book-selector {
    background: rgba(255,255,255,0.95); border-radius: 16px; padding: 1.2rem 1.5rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.1); margin-bottom: 1.5rem;
}
.book-tag {
    display: inline-block; background: linear-gradient(135deg, #667eea, #764ba2);
    color: white; padding: 0.3rem 0.8rem; border-radius: 15px; margin: 0.2rem;
    font-size: 0.8rem; cursor: pointer; transition: all 0.2s; user-select: none;
}
.book-tag:hover { transform: scale(1.05); box-shadow: 0 2px 8px rgba(102,126,234,0.4); }
.book-tag-active { background: linear-gradient(135deg, #27ae60, #2ecc71); }
.book-tag-inactive { background: #bdc3c7; opacity: 0.6; }

/* 问答气泡 */
.user-bubble { background: linear-gradient(135deg, #667eea, #764ba2); color: white;
    border-radius: 20px 20px 5px 20px; padding: 1rem 1.5rem; margin: 0.5rem 0 0.5rem auto;
    max-width: 80%; text-align: right; box-shadow: 0 4px 15px rgba(102,126,234,0.4); }
.ai-bubble { background: white; color: #333; border-radius: 20px 20px 20px 5px;
    padding: 1rem 1.5rem; margin: 0.5rem auto 0.5rem 0; max-width: 90%;
    box-shadow: 0 4px 15px rgba(0,0,0,0.1); border-left: 4px solid #667eea; }

/* 来源卡片 */
.source-card { background: rgba(255,255,255,0.95); border-radius: 12px; padding: 0.8rem 1rem;
    margin: 0.5rem 0; border-left: 4px solid #667eea; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
.source-book { font-weight: 700; color: #667eea; font-size: 0.9rem; }
.source-score { background: linear-gradient(135deg, #667eea, #764ba2); color: white;
    padding: 0.2rem 0.6rem; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }
.source-text { color: #666; font-size: 0.85rem; line-height: 1.5; max-height: 100px; overflow-y: auto; }

.footer { text-align: center; color: rgba(255,255,255,0.7); font-size: 0.8rem; margin-top: 3rem; padding: 1rem; }
@media (max-width: 768px) { .main-title { font-size: 1.8rem; } }
</style>
""", unsafe_allow_html=True)

# ── 数据加载 ──────────────────────────────────────
_APP = Path(__file__).resolve().parent
_PATHS = [_APP / "vectors.npz", _APP / "workspace" / "vectors.npz",
    Path("/workspace/vectors.npz"), Path("/home/user/app/vectors.npz"),
    Path("/home/user/app/workspace/vectors.npz")]

VPATH = None
for p in _PATHS:
    if p.exists():
        VPATH = p
        break

if VPATH is None:
    st.error("vectors.npz 未找到")
    st.stop()

MPATH = VPATH.parent / "metadata.json"

@st.cache_resource
def load_data():
    data = np.load(VPATH)
    embeddings = data["embeddings"].astype(np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms
    with open(MPATH, encoding="utf-8") as f:
        meta = json.load(f)
    return embeddings, meta["documents"], meta["metadatas"]

embeddings, documents, metadatas = load_data()

# 提取教材列表
book_stats = {}
for m in metadatas:
    book = m.get("book", "?")
    book_stats[book] = book_stats.get(book, 0) + 1
ALL_BOOKS = sorted(book_stats.keys(), key=lambda x: -book_stats[x])
book_count = len(ALL_BOOKS)
total_chunks = len(documents)

# ── API 配置 ──────────────────────────────────────
API_KEY = os.environ.get("CS_API_KEY", "")
if not API_KEY:
    st.error("未配置 CS_API_KEY 环境变量，请在 Space Settings 中添加")
    st.stop()

_embed = OpenAI(api_key=API_KEY, base_url="https://open.cherryin.net/v1")

MODELS = {
    "deepseek/deepseek-v4-flash(free)": "DeepSeek V4 Flash",
    "deepseek/deepseek-v3.2-250101(free)": "DeepSeek V3.2",
}

# ── 检索函数 ──────────────────────────────────────
def search(text, k=10, book_filter=None):
    try:
        r = _embed.embeddings.create(model="baai/bge-m3(free)", input=[text])
        qvec = np.array(r.data[0].embedding, dtype=np.float32)
        qvec = qvec / np.linalg.norm(qvec)
        scores = embeddings @ qvec
        if book_filter and len(book_filter) < len(ALL_BOOKS):
            mask = np.array([m.get("book","?") in book_filter for m in metadatas])
            scores = np.where(mask, scores, -np.inf)
        top_k = np.argsort(scores)[-k:][::-1]
        hits = []
        for idx in top_k:
            s = scores[int(idx)]
            if s == -np.inf: continue
            hits.append({"text": documents[int(idx)][:500],
                "book": metadatas[int(idx)].get("book","?"),
                "chapter": metadatas[int(idx)].get("chapter","?"),
                "similarity": round(float(s), 4)})
        return hits
    except Exception as e:
        st.error(f"检索失败: {e}")
        return []

def synthesize(q, hits, model="deepseek/deepseek-v4-flash(free)"):
    ctx = "\n\n---\n\n".join(f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}" for i,h in enumerate(hits[:5]))
    try:
        r = requests.post("https://open.cherryin.net/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": model, "messages": [
                {"role": "system", "content": "你是医学知识助手。根据教材段落回答用户问题。要求：1.综合多个段落给出准确回答 2.标注来源 3.中文回答 4.列出参考来源"},
                {"role": "user", "content": f"教材段落:\n{ctx}\n\n问题: {q}"}],
            "temperature": 0.3, "max_tokens": 2000}, timeout=90)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"生成回答失败: {e}"

# ── 主界面 ────────────────────────────────────────
st.markdown(f'<h1 class="main-title">🏥 医学教材知识库</h1>', unsafe_allow_html=True)
st.markdown(f'<p class="main-subtitle">{book_count} 本教材 · {total_chunks} 个知识点 · 全免费 · 24h在线</p>', unsafe_allow_html=True)

# 初始化会话
if "hist" not in st.session_state: st.session_state.hist = []
if "q" not in st.session_state: st.session_state.q = ""
if "selected_books" not in st.session_state: st.session_state.selected_books = list(ALL_BOOKS)

# ── 教材范围选择（主界面） ──────────────────────────
st.markdown("**📚 选择教材范围：**")

col_all, col_none = st.columns([1, 1])
with col_all:
    if st.button("全选", key="select_all", use_container_width=True):
        st.session_state.selected_books = list(ALL_BOOKS)
with col_none:
    if st.button("清空", key="clear_all", use_container_width=True):
        st.session_state.selected_books = []

selected_books = st.multiselect(
    "选择教材",
    options=ALL_BOOKS,
    default=st.session_state.selected_books,
    format_func=lambda x: f"{x} ({book_stats[x]}块)",
    key="book_selector",
    label_visibility="collapsed"
)
st.session_state.selected_books = selected_books

selected_chunks = sum(book_stats.get(b, 0) for b in selected_books)
scope_text = f"已选 {len(selected_books)}/{book_count} 本教材，{selected_chunks} 个文本块"
if len(selected_books) == book_count:
    scope_text = f"全部 {book_count} 本教材，{total_chunks} 个文本块"
st.caption(scope_text)

# ── 模型选择 + 搜索框 ─────────────────────────────
col_m, col_q, col_btn = st.columns([2, 5, 1])
with col_m:
    selected_model = st.selectbox("AI模型", options=list(MODELS.keys()),
        format_func=lambda x: MODELS[x], index=0, label_visibility="collapsed")
with col_q:
    q = st.text_input("输入问题", value=st.session_state.q,
        placeholder="如：心衰的病理机制、股三角的构成、疟原虫的生活史…",
        label_visibility="collapsed", key="search_input")
with col_btn:
    search_btn = st.button("🔍 搜索", type="primary", use_container_width=True)

# ── 处理搜索 ──────────────────────────────────────
if (search_btn or q) and q.strip():
    st.session_state.q = q.strip()
    book_filter = selected_books if len(selected_books) < book_count else None
    with st.spinner("🔍 检索中…"):
        hits = search(q.strip(), k=10, book_filter=book_filter)
    if hits:
        with st.spinner("💡 AI 回答中…"):
            ans = synthesize(q.strip(), hits, model=selected_model)
    else:
        ans = "未找到相关内容" if selected_books else "请先选择教材"
    st.session_state.hist.append({"q": q.strip(), "hits": hits, "a": ans,
        "model": MODELS.get(selected_model, selected_model)})

# ── 显示对话历史 ──────────────────────────────────
if st.session_state.hist:
    st.markdown("---")
    for item in reversed(st.session_state.hist):
        st.markdown(f'<div class="user-bubble">❓ {item["q"]}</div>', unsafe_allow_html=True)
        if item["hits"]:
            st.markdown("**📚 参考来源：**")
            cols = st.columns(min(3, len(item["hits"][:5])))
            for i, h in enumerate(item["hits"][:5]):
                with cols[i % 3]:
                    c = "🟢" if h["similarity"] > 0.8 else "🟡" if h["similarity"] > 0.6 else "🔴"
                    st.markdown(f"""<div class="source-card"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                        <span class="source-book">{h['book'][:20]}</span>
                        <span class="source-score">{c} {h['similarity']:.2f}</span></div>
                        <div class="source-text">{h['text'][:120]}…</div></div>""", unsafe_allow_html=True)
        st.markdown(f'<div class="ai-bubble"><strong>💡 {item.get("model","AI")}：</strong><br><br>{item["a"]}</div>', unsafe_allow_html=True)
else:
    st.markdown("""<div style="text-align:center;padding:3rem;color:rgba(255,255,255,0.8);">
        <h2>👋 输入任何医学问题，AI从教材中检索回答</h2>
        <p style="margin-top:1rem;opacity:0.8">试试：心衰的病理机制 | 股三角的构成 | 疟原虫的生活史</p>
    </div>""", unsafe_allow_html=True)

st.markdown('<div class="footer">📚 医学教材知识库 · 嵌入: BGE-M3 | 回答: DeepSeek V4 Flash · 全免费 24h在线</div>', unsafe_allow_html=True)
