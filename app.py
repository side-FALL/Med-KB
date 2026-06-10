"""🏥 医学教材知识库 — v2"""
import streamlit as st
import os, json, numpy as np
from pathlib import Path

from openai import OpenAI

from llm_utils import COMPACT_SYSTEM_PROMPT, call_llm, call_llm_stream, build_user_message, rewrite_query
from __init__ import __version__

st.set_page_config(page_title="医学教材知识库", page_icon="🏥", layout="wide")

# ── 样式 ──────────────────────────────────────────
st.markdown("""
<style>
.stApp { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }
.main .block-container { max-width: 900px; padding: 2rem 1rem; }
.main-title { text-align:center; color:white; font-size:2.5rem; font-weight:800;
    margin-bottom:0.3rem; text-shadow:2px 2px 4px rgba(0,0,0,0.3); }
.main-subtitle { text-align:center; color:rgba(255,255,255,0.85); font-size:0.95rem; margin-bottom:1.5rem; }
.user-bubble { background:linear-gradient(135deg,#667eea,#764ba2); color:white;
    border-radius:20px 20px 5px 20px; padding:1rem 1.5rem; margin:0.5rem 0 0.5rem auto;
    max-width:80%; text-align:right; box-shadow:0 4px 15px rgba(102,126,234,0.4); }
.ai-bubble { background:white; color:#333; border-radius:20px 20px 20px 5px;
    padding:1rem 1.5rem; margin:0.5rem auto 0.5rem 0; max-width:90%;
    box-shadow:0 4px 15px rgba(0,0,0,0.1); border-left:4px solid #667eea; }
.source-card { background:rgba(255,255,255,0.95); border-radius:12px; padding:0.7rem 1rem;
    margin:0.4rem 0; border-left:4px solid #667eea; box-shadow:0 2px 10px rgba(0,0,0,0.08); }
.source-book { font-weight:700; color:#667eea; font-size:0.85rem; }
.source-score { background:linear-gradient(135deg,#667eea,#764ba2); color:white;
    padding:0.15rem 0.5rem; border-radius:10px; font-size:0.7rem; font-weight:600; }
.footer { text-align:center; color:rgba(255,255,255,0.7); font-size:0.8rem; margin-top:2rem; padding:1rem; }
/* multiselect 紧凑显示 */
.stMultiSelect > div[data-baseweb="select"] { max-height: 80px !important; overflow-y: auto !important; }
@media(max-width:768px){.main-title{font-size:1.8rem;}}
</style>
""", unsafe_allow_html=True)

# ── 数据加载 ──────────────────────────────────────
_APP = Path(__file__).resolve().parent
for _p in [_APP/"vectors.npz", Path("/workspace/vectors.npz"), Path("/home/user/app/vectors.npz")]:
    if _p.exists(): VPATH = _p; break
else:
    st.error("vectors.npz 未找到"); st.stop()

MPATH = VPATH.parent / "metadata.json"

@st.cache_resource
def load_data():
    data = np.load(VPATH)
    emb = data["embeddings"].astype(np.float32)
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    with open(MPATH, encoding="utf-8") as f:
        meta = json.load(f)
    return emb, meta["documents"], meta["metadatas"]

embeddings, documents, metadatas = load_data()
book_stats = {}
for m in metadatas:
    b = m.get("book","?"); book_stats[b] = book_stats.get(b,0)+1
ALL_BOOKS = sorted(book_stats.keys(), key=lambda x: -book_stats[x])
book_count = len(ALL_BOOKS)
total_chunks = len(documents)

# ── API ───────────────────────────────────────────
API_KEY = os.environ.get("CS_API_KEY","")
if not API_KEY: st.error("未配置 CS_API_KEY"); st.stop()
_embed = OpenAI(api_key=API_KEY, base_url="https://open.cherryin.net/v1")
MODELS = {"deepseek/deepseek-v4-flash(free)":"DeepSeek V4 Flash", "deepseek/deepseek-v3.2-250101(free)":"DeepSeek V3.2"}

# ── 检索 ──────────────────────────────────────────
def search(text, k=10, book_filter=None):
    r = _embed.embeddings.create(model="baai/bge-m3(free)", input=[text])
    qvec = np.array(r.data[0].embedding, dtype=np.float32)
    qvec = qvec / np.linalg.norm(qvec)
    scores = embeddings @ qvec
    if book_filter and len(book_filter) < len(ALL_BOOKS):
        mask = np.array([m.get("book","?") in book_filter for m in metadatas])
        scores = np.where(mask, scores, -np.inf)
    top = np.argsort(scores)[-k:][::-1]
    hits = []
    for idx in top:
        s = float(scores[int(idx)])
        if s == -np.inf: continue
        hits.append({"text":documents[int(idx)][:500], "book":metadatas[int(idx)].get("book","?"),
            "chapter":metadatas[int(idx)].get("chapter","?"), "similarity":round(s,4)})
    return hits

def synthesize(q, hits, model="deepseek/deepseek-v4-flash(free)", conv_context=""):
    user_msg = build_user_message(hits, q, conv_context)
    return call_llm(API_KEY, user_msg, model=model, system_prompt=COMPACT_SYSTEM_PROMPT)

# ── 侧边栏设置 ─────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ 设置")
    use_context = st.checkbox("💬 启用多轮对话",
        value=st.session_state.get("use_context", True),
        help="开启后AI会参考之前对话的上下文（如代词指代）")
    st.session_state.use_context = use_context
    
    turns = len(st.session_state.get("conversation_turns", []))
    if turns > 0:
        st.caption(f"📝 已进行 {turns} 轮对话")
    
    st.divider()
    if st.button("🗑️ 清空全部", use_container_width=True):
        st.session_state.hist = []
        st.session_state.conversation_turns = []
        st.rerun()
    
    st.divider()
    st.markdown(f"""
    **📊 知识库统计**
    - 教材数量: {book_count} 本
    - 文本块数: {total_chunks} 块
    - 嵌入模型: BGE-M3 (免费)
    """)

# ── 主界面 ────────────────────────────────────────
st.markdown(f'<h1 class="main-title">🏥 医学教材知识库 v{__version__}</h1>', unsafe_allow_html=True)
st.markdown(f'<p class="main-subtitle">{book_count} 本教材 · {total_chunks} 个知识点 · 全免费 · 24h在线</p>', unsafe_allow_html=True)

# ── 教材范围（紧凑版）─────────────────────────────
col_scope, col_model = st.columns([3, 1])
with col_scope:
    scope = st.selectbox("📚 教材范围", ["全部教材", "选择教材"], label_visibility="collapsed")
with col_model:
    selected_model = st.selectbox("🤖 AI模型", list(MODELS.keys()), format_func=lambda x: MODELS[x], label_visibility="collapsed")

selected_books = ALL_BOOKS
if scope == "选择教材":
    with st.expander("📖 选择要检索的教材", expanded=True):
        selected_books = st.multiselect(
            "选择教材", ALL_BOOKS,
            format_func=lambda x: f"{x} ({book_stats[x]}块)",
            label_visibility="collapsed"
        )
        if selected_books:
            n = sum(book_stats.get(b,0) for b in selected_books)
            st.caption(f"已选 {len(selected_books)}/{book_count} 本，{n} 个文本块")
        else:
            st.warning("请至少选择一本教材")

# ── 搜索框 ────────────────────────────────────────
col_q, col_btn = st.columns([6, 1])
with col_q:
    q = st.text_input("输入问题", value=st.session_state.get("q",""),
        placeholder="如：心衰的病理机制、股三角的构成、疟原虫的生活史…",
        label_visibility="collapsed")
with col_btn:
    search_btn = st.button("🔍 搜索", type="primary", use_container_width=True)

# ── 搜索处理 ──────────────────────────────────────
if (search_btn or (q and q.strip() != st.session_state.get("q",""))) and q.strip():
    st.session_state.q = q.strip()
    book_filter = selected_books if scope == "选择教材" and len(selected_books) < book_count else None
    
    # 构建对话上下文
    conv_context = ""
    if st.session_state.get("use_context", True) and st.session_state.get("conversation_turns"):
        recent = st.session_state.conversation_turns[-3:]
        lines = []
        for turn_q, turn_a in recent:
            lines.append(f"用户: {turn_q[:100]}")
            lines.append(f"助手: {turn_a[:200]}")
        conv_context = "\n".join(lines)
    
    # 查询重写
    search_query = q.strip()
    if st.session_state.get("use_context", True) and st.session_state.get("conversation_turns"):
        prev_queries = [t[0] for t in st.session_state.conversation_turns]
        rewritten = rewrite_query(search_query, prev_queries, api_key=API_KEY)
        if rewritten != search_query:
            search_query = rewritten
            st.info(f"🔄 结合上下文重写查询：{rewritten}")
    
    with st.status("🔍 正在检索…", expanded=True) as status:
        st.write("📝 文本向量化中…")
        hits = search(search_query, k=10, book_filter=book_filter)
        if hits:
            st.write(f"✅ 找到 {len(hits)} 条相关内容")
            status.update(label="✅ 检索完成，AI 正在回答…", state="complete")
            user_msg = build_user_message(hits, q.strip(), conv_context if st.session_state.get("use_context", True) else "")
            ans = st.write_stream(
                call_llm_stream(API_KEY, user_msg, model=selected_model, system_prompt=COMPACT_SYSTEM_PROMPT)
            )
        else:
            status.update(label="⚠️ 未找到相关内容", state="complete")
            ans = "未找到相关内容" if selected_books else "请先选择教材"
    if "hist" not in st.session_state: st.session_state.hist = []
    if "conversation_turns" not in st.session_state: st.session_state.conversation_turns = []
    if "use_context" not in st.session_state: st.session_state.use_context = True
    st.session_state.hist.append({"q":q.strip(),"hits":hits,"a":ans,"model":MODELS.get(selected_model,"")})
    if st.session_state.use_context:
        st.session_state.conversation_turns.append((q.strip(), ans))

# ── 显示结果 ──────────────────────────────────────
if st.session_state.get("hist"):
    st.markdown("---")
    for item in reversed(st.session_state.hist):
        st.markdown(f'<div class="user-bubble">❓ {item["q"]}</div>', unsafe_allow_html=True)
        if item["hits"]:
            st.markdown("**📚 参考来源：**")
            cols = st.columns(min(3, len(item["hits"][:5])))
            for i, h in enumerate(item["hits"][:5]):
                with cols[i%3]:
                    c = "🟢" if h["similarity"]>0.8 else "🟡" if h["similarity"]>0.6 else "🔴"
                    st.markdown(f'<div class="source-card"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;"><span class="source-book">{h["book"][:18]}</span><span class="source-score">{c} {h["similarity"]:.2f}</span></div><div style="color:#666;font-size:0.82rem;line-height:1.4;max-height:80px;overflow-y:auto;">{h["text"][:120]}…</div></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="ai-bubble"><strong>💡 {item.get("model","AI")}：</strong><br><br>{item["a"]}</div>', unsafe_allow_html=True)
else:
    st.markdown("""<div style="text-align:center;padding:3rem;color:rgba(255,255,255,0.8);">
        <h2>👋 输入任何医学问题，AI从教材中检索回答</h2>
        <p style="margin-top:1rem;opacity:0.8">试试：心衰的病理机制 | 股三角的构成 | 疟原虫的生活史</p>
    </div>""", unsafe_allow_html=True)


