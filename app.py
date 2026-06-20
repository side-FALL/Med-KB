"""🏥 医学教材知识库 — v2（多功能版）"""
import streamlit as st
import os, json, re
import numpy as np
from pathlib import Path

from openai import OpenAI


def fix_latex_formulas(text: str) -> str:
    """修复LLM输出的LaTeX公式格式，使其兼容Streamlit渲染。

    将 [ ... ] 格式的公式转换为 $$ ... $$
    同时清理 \\text{} 包裹，简化化学式表示。
    """
    # 将 \[ ... \] 转换为 $$ ... $$
    text = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', text, flags=re.DOTALL)

    # 将 [ ... ] 转换为 $$ ... $$ （当内容包含 LaTeX 命令时）
    # 匹配包含 \rightarrow, \text, _, ^ 等 LaTeX 特征的公式
    def replace_bracket_formula(match):
        content = match.group(1).strip()
        # 清理 \text{} 包裹
        content = re.sub(r'\\text\{([^}]*)\}', r'\1', content)
        return f'$${content}$$'

    # 匹配包含 LaTeX 命令的方括号公式（允许嵌套括号）
    # 模式：[ 开头，包含 \ 或 _ 或 ^，] 结尾
    pattern = r'\[([^\]]*(?:\\(?:rightarrow|leftarrow|text|frac|sum|int)|[_^]\{)[^\]]*)\]'
    text = re.sub(pattern, replace_bracket_formula, text)

    # 通用模式：匹配较长的方括号内容（可能是公式）
    pattern2 = r'\[([^\]]{30,})\]'
    text = re.sub(pattern2, lambda m: f'$${m.group(1).strip()}$$' if '\\' in m.group(1) or '_' in m.group(1) or '^' in m.group(1) else m.group(0), text)

    return text


from llm_utils import (
    SYSTEM_PROMPT, COMPACT_SYSTEM_PROMPT, EXAM_SYSTEM_PROMPT,
    QUIZ_SYSTEM_PROMPT, COMPARE_SYSTEM_PROMPT,
    CASE_SYSTEM_PROMPT, MINDMAP_SYSTEM_PROMPT,
    call_llm, call_llm_stream, rewrite_query,
    build_user_message, build_quiz_message, build_compare_message,
    build_case_message, build_mindmap_message,
)

# 优先使用轻量版 Agent（不依赖 LangChain）
try:
    from agent_lite import run_agent, run_agent_stream
except ImportError:
    try:
        from agent import run_agent
        run_agent_stream = None
    except ImportError:
        run_agent = None
        run_agent_stream = None

from __init__ import __version__

st.set_page_config(
    page_title="医学教材知识库", page_icon="🏥", layout="wide",
    initial_sidebar_state="expanded"
)

# ── 样式 ──────────────────────────────────────────
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

.stApp { background: linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 50%, #E1F5FE 100%); min-height: 100vh; }
.main .block-container { max-width: 900px; padding: 2rem 1rem; }
.main-title { text-align:center; color:#1565C0; font-size:2.5rem; font-weight:800;
    margin-bottom:0.3rem; text-shadow:1px 1px 2px rgba(0,0,0,0.1); }
.main-subtitle { text-align:center; color:#42A5F5; font-size:0.95rem; margin-bottom:1.5rem; }
.user-bubble { background:linear-gradient(135deg,#1565C0,#42A5F5); color:white;
    border-radius:20px 20px 5px 20px; padding:1rem 1.5rem; margin:0.5rem 0 0.5rem auto;
    max-width:80%; text-align:right; box-shadow:0 4px 15px rgba(21,101,192,0.4); }
.ai-bubble { background:white; color:#333; border-radius:20px 20px 20px 5px;
    padding:1rem 1.5rem; margin:0.5rem auto 0.5rem 0; max-width:90%;
    box-shadow:0 4px 15px rgba(0,0,0,0.1); border-left:4px solid #1565C0; }
.source-card { background:rgba(255,255,255,0.95); border-radius:12px; padding:0.7rem 1rem;
    margin:0.4rem 0; border-left:4px solid #1565C0; box-shadow:0 2px 10px rgba(0,0,0,0.08);
    transition: transform 0.2s; }
.source-card:hover { transform: translateX(5px); }
.source-book { font-weight:700; color:#1565C0; font-size:0.85rem; }
.source-score { background:linear-gradient(135deg,#1565C0,#42A5F5); color:white;
    padding:0.15rem 0.5rem; border-radius:10px; font-size:0.7rem; font-weight:600; }
.stMultiSelect > div[data-baseweb="select"] { max-height: 80px !important; overflow-y: auto !important; }
@media(max-width:768px){
    .main-title{font-size:1.8rem;}
    .user-bubble,.ai-bubble{max-width:95%;}
    .source-card{padding:0.6rem;}
}
</style>
""", unsafe_allow_html=True)

# ── 初始化会话状态 ──────────────────────────────
_defaults = {
    "hist": [], "conversation_turns": [], "use_context": True,
    "alpha": 0.7, "q": "", "favorites": [], "mode": "💬 智能问答",
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── 数据加载（按需加载教材）──────────────────────────
_APP = Path(__file__).resolve().parent
BOOKS_DIR = _APP / "books"

# 启动时只加载清单（轻量）
_MANIFEST = BOOKS_DIR / "manifest.json"
if not _MANIFEST.exists():
    st.error("books/ 目录缺失，请先运行 split_books.py"); st.stop()
BOOK_MANIFEST = json.loads(_MANIFEST.read_text(encoding="utf-8"))
ALL_BOOKS = sorted(BOOK_MANIFEST.keys(), key=lambda x: -BOOK_MANIFEST[x]["chunks"])
book_count = len(ALL_BOOKS)
total_chunks = sum(v["chunks"] for v in BOOK_MANIFEST.values())
book_stats = {name: info["chunks"] for name, info in BOOK_MANIFEST.items()}

# 按需加载单本教材（带缓存）
@st.cache_resource
def load_book(book_name: str):
    info = BOOK_MANIFEST[book_name]
    fname = info["file"]
    emb_data = np.load(BOOKS_DIR / f"{fname}.npz")
    emb = emb_data["embeddings"].astype(np.float32)
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    with open(BOOKS_DIR / f"{fname}.json", encoding="utf-8") as f:
        meta = json.load(f)
    return emb, meta["documents"], meta["metadatas"]

# 加载选中的教材数据
def load_selected_books(book_names: list[str]):
    all_emb, all_docs, all_metas = [], [], []
    for name in book_names:
        if name in BOOK_MANIFEST:
            emb, docs, metas = load_book(name)
            all_emb.append(emb)
            all_docs.extend(docs)
            all_metas.extend(metas)
    if not all_emb:
        return None, [], []
    return np.vstack(all_emb), all_docs, all_metas

# ── API ───────────────────────────────────────────
API_KEY = os.environ.get("CS_API_KEY","")
if not API_KEY: st.error("未配置 CS_API_KEY"); st.stop()
_embed = OpenAI(api_key=API_KEY, base_url="https://open.cherryin.net/v1")
MODELS = {
    "deepseek/deepseek-v4-flash(free)": "DeepSeek V4 Flash",
    "deepseek/deepseek-v3.2-250101(free)": "DeepSeek V3.2",
}

# ── BM25 关键词检索 ──────────────────────────────────
_STOPWORDS = set("的了是在不有我这个们他她它们和与或但而如果因为所以可以已经正在".replace(" ",""))
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+|\d+")

def _tokenize(text: str) -> list[str]:
    text = re.sub(r"[的了是在不有我这个们]", " ", text)
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]
    chars = re.findall(r"[\u4e00-\u9fff]", text.lower())
    for i in range(len(chars) - 1):
        bg = chars[i] + chars[i+1]
        if bg not in _STOPWORDS:
            tokens.append(bg)
    return tokens

# ── 混合检索 ──────────────────────────────────────
def search(text, embeddings, documents, metadatas, k=10, alpha=0.7):
    r = _embed.embeddings.create(model="baai/bge-m3(free)", input=[text])
    qvec = np.array(r.data[0].embedding, dtype=np.float32)
    qvec = qvec / np.linalg.norm(qvec)
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
            "book": metadatas[local_idx].get("book","?"),
            "chapter": metadatas[local_idx].get("chapter","?"),
            "similarity": round(s, 4),
            "vector_sim": round(float(vec_scores[local_idx]), 4),
            "bm25_score": round(float(bm25_scores[local_idx]), 4),
        })
    return hits

# ── 侧边栏 ─────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ 设置")

    top_k = st.slider("返回结果数", 3, 15, 10)
    alpha = st.slider("向量权重 (α)", 0.0, 1.0, 0.7,
        help="1.0=纯向量检索，0.0=纯关键词检索", key="alpha_slider")

    use_context = st.checkbox("💬 启用多轮对话",
        value=st.session_state.get("use_context", True),
        help="开启后AI会参考之前对话的上下文", key="use_context_cb")

    turns = len(st.session_state.conversation_turns)
    if turns > 0:
        st.caption(f"📝 已进行 {turns} 轮对话")

    st.divider()
    st.markdown("### 📜 搜索历史")
    if st.session_state.hist:
        for i, item in enumerate(reversed(st.session_state.hist[-10:])):
            mode_icon = {"问答":"💬","刷题":"📝","对比":"🔄","病例":"🏥"}.get(item.get("mode","问答"),"💬")
            st.caption(f"{mode_icon} {item['q'][:35]}")
    else:
        st.caption("暂无搜索历史")

    st.divider()
    if st.button("🗑️ 清空全部", use_container_width=True):
        st.session_state.hist = []
        st.session_state.conversation_turns = []
        st.session_state.favorites = []
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

# ── 更新公告提示 ──────────────────────────────────────
st.markdown("""
<style>
.tooltip-container {
    position: relative;
    display: inline-block;
    cursor: help;
}
.tooltip-icon {
    display: inline-block;
    width: 20px;
    height: 20px;
    line-height: 20px;
    text-align: center;
    background: rgba(255,255,255,0.3);
    color: white;
    border-radius: 50%;
    font-size: 14px;
    font-weight: bold;
    margin-left: 8px;
    vertical-align: middle;
}
.tooltip-content {
    visibility: hidden;
    width: 420px;
    background: white;
    color: #333;
    border-radius: 12px;
    padding: 1.2rem;
    position: absolute;
    z-index: 100;
    left: 50%;
    transform: translateX(-50%);
    top: 125%;
    box-shadow: 0 8px 24px rgba(0,0,0,0.2);
    font-size: 0.85rem;
    line-height: 1.6;
    text-align: left;
}
.tooltip-content h4 {
    margin: 0 0 0.8rem 0;
    color: #667eea;
    font-size: 1rem;
}
.tooltip-content ul {
    margin: 0;
    padding-left: 1.2rem;
}
.tooltip-content li {
    margin-bottom: 0.3rem;
}
.tooltip-container:hover .tooltip-content {
    visibility: visible;
}
</style>

<div style="text-align:center; margin-bottom:1rem;">
    <span class="tooltip-container">
        <span class="tooltip-icon">?</span>
        <div class="tooltip-content">
            <h4>v2.2.1 更新公告</h4>
            <p><strong>智能体对话式改造 + 性能优化：</strong></p>
            <ul>
                <li>💬 智能体对话：支持多轮对话，自动保留上下文</li>
                <li>🛡️ 置信度评估：低置信度回答会显示警告</li>
                <li>⚡ 缓存优化：重复查询瞬间返回</li>
                <li>🎨 浅蓝白UI：更清爽的界面风格</li>
            </ul>
            <p style="margin-top:0.8rem; color:#666; font-size:0.8rem;">
                智能体可结合前3轮对话理解代词（如"那这个药呢"）
            </p>
        </div>
    </span>
</div>
""", unsafe_allow_html=True)

# ── 教材范围 + 模型选择 ──────────────────────────────
col_scope, col_model = st.columns([3, 1])
with col_scope:
    scope = st.selectbox("📚 教材范围", ["全部教材", "选择教材"], label_visibility="collapsed")
with col_model:
    selected_model = st.selectbox("🤖 AI模型", list(MODELS.keys()),
        format_func=lambda x: MODELS[x], label_visibility="collapsed")

selected_books = ALL_BOOKS
if scope == "选择教材":
    with st.expander("📖 选择要检索的教材", expanded=True):
        c1, c2 = st.columns([4, 1])
        with c2:
            if st.button("全选", use_container_width=True, key="sel_all"):
                st.session_state.selected_books = ALL_BOOKS
            if st.button("清空", use_container_width=True, key="sel_clr"):
                st.session_state.selected_books = []
        if "selected_books" not in st.session_state:
            st.session_state.selected_books = ALL_BOOKS
        selected_books = st.multiselect("选择教材", ALL_BOOKS,
            default=st.session_state.selected_books,
            format_func=lambda x: f"{x} ({book_stats[x]}块)",
            label_visibility="collapsed")
        st.session_state.selected_books = selected_books
        if selected_books:
            n = sum(book_stats.get(b,0) for b in selected_books)
            st.caption(f"已选 {len(selected_books)}/{book_count} 本，{n} 个文本块")
        else:
            st.warning("请至少选择一本教材")

# ── 模式切换 ──────────────────────────────────────
mode = st.tabs(["💬 智能问答", "📝 自测刷题", "🔄 对比学习", "🏥 病例分析", "🤖 智能体模式"])

# ══════════════════════════════════════════════════
# 模式一：智能问答
# ══════════════════════════════════════════════════
with mode[0]:
    col_q, col_btn = st.columns([6, 1])
    with col_q:
        q = st.text_input("输入问题", value=st.session_state.get("q",""),
            placeholder="如：心衰的病理机制、股三角的构成、疟原虫的生活史…",
            label_visibility="collapsed", key="q")
    with col_btn:
        search_btn = st.button("🔍 搜索", type="primary", use_container_width=True, key="qa_btn")

    exam_toggle = st.checkbox("⭐ 考点标注模式", value=False,
        help="开启后回答中标注高频考点、核心知识点、易混淆点",
        key="exam_toggle")
    prompt_to_use = EXAM_SYSTEM_PROMPT if exam_toggle else COMPACT_SYSTEM_PROMPT

    if (search_btn or q.strip()) and q.strip():
        # 加载选中教材的数据
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        with st.status("📚 加载教材数据…", expanded=False) as status:
            embeddings, documents, metadatas = load_selected_books(books_to_load)
            if embeddings is None:
                st.error("未加载到教材数据"); st.stop()
            status.update(label=f"✅ 已加载 {len(books_to_load)} 本教材", state="complete")

        conv_context = ""
        if use_context and st.session_state.conversation_turns:
            recent = st.session_state.conversation_turns[-3:]
            lines = []
            for turn_q, turn_a in recent:
                lines.append(f"用户: {turn_q[:100]}")
                lines.append(f"助手: {turn_a[:200]}")
            conv_context = "\n".join(lines)

        search_query = q.strip()
        if use_context and st.session_state.conversation_turns:
            prev_queries = [t[0] for t in st.session_state.conversation_turns]
            rewritten = rewrite_query(search_query, prev_queries, api_key=API_KEY)
            if rewritten != search_query:
                search_query = rewritten
                st.info(f"🔄 结合上下文重写查询：{rewritten}")

        with st.status("🔍 正在检索…", expanded=True) as status:
            st.write("📝 文本向量化中…")
            hits = search(search_query, embeddings, documents, metadatas, k=top_k, alpha=alpha)
            if hits:
                st.write(f"✅ 找到 {len(hits)} 条相关内容")
                status.update(label="✅ 检索完成，AI 正在回答…", state="complete")
                user_msg = build_user_message(hits, q.strip(), conv_context if use_context else "")
                # 收集流式输出
                ans = ""
                for chunk in call_llm_stream(API_KEY, user_msg, model=selected_model, system_prompt=prompt_to_use):
                    ans += chunk
                # 修复 LaTeX 公式后显示
                fixed_ans = fix_latex_formulas(ans)
                st.markdown(fixed_ans)
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete")
                ans = "未找到相关内容"
        st.session_state.hist.append({"q":q.strip(),"hits":hits,"a":ans,"model":MODELS.get(selected_model,""),"mode":"问答"})
        if use_context:
            st.session_state.conversation_turns.append((q.strip(), ans))

    # 显示问答历史（包含旧记录，兼容无 mode 字段的条目）
    qa_items = [h for h in st.session_state.hist if h.get("mode", "问答") == "问答"]
    if qa_items:
        st.markdown("---")
        for item in reversed(qa_items):
            st.markdown(f'<div class="user-bubble">❓ {item["q"]}</div>', unsafe_allow_html=True)
            if item["hits"]:
                st.markdown("**📚 参考来源：**")
                cols = st.columns(min(3, len(item["hits"][:5])))
                for i, h in enumerate(item["hits"][:5]):
                    with cols[i%3]:
                        c = "🟢" if h.get("similarity",0)>0.8 else "🟡" if h.get("similarity",0)>0.6 else "🔴"
                        st.markdown(f'<div class="source-card"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;"><span class="source-book">{h["book"][:18]}</span><span class="source-score">{c} {h["similarity"]:.2f}</span></div><div style="color:#666;font-size:0.82rem;line-height:1.4;max-height:80px;overflow-y:auto;">{h["text"][:120]}…</div></div>', unsafe_allow_html=True)
            # 修复 LaTeX 公式后显示
            fixed_answer = fix_latex_formulas(item["a"])
            st.markdown(f'<div class="ai-bubble"><strong>💡 {item.get("model","AI")}：</strong></div>', unsafe_allow_html=True)
            st.markdown(fixed_answer)
    else:
        st.markdown("""<div style="text-align:center;padding:3rem;color:rgba(255,255,255,0.8);">
            <h2>👋 输入任何医学问题，AI从教材中检索回答</h2>
            <p style="margin-top:1rem;opacity:0.8">试试：心衰的病理机制 | 股三角的构成 | 疟原虫的生活史</p>
        </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════
# 模式二：自测刷题
# ══════════════════════════════════════════════════
with mode[1]:
    st.markdown("输入知识点主题，系统从教材中检索相关内容并生成5道题。")

    col_topic, col_btn = st.columns([4, 1])
    with col_topic:
        quiz_topic = st.text_input("知识点主题",
            placeholder="如：心力衰竭、抗生素分类、疟原虫生活史…",
            label_visibility="collapsed", key="quiz_topic")
    with col_btn:
        quiz_btn = st.button("📝 出题", type="primary", use_container_width=True, key="quiz_btn")

    if quiz_btn and quiz_topic.strip():
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        embeddings, documents, metadatas = load_selected_books(books_to_load)
        if embeddings is None:
            st.error("未加载到教材数据"); st.stop()
        with st.status("🔍 检索教材并出题…", expanded=True) as status:
            hits = search(quiz_topic.strip(), embeddings, documents, metadatas, k=top_k, alpha=alpha)
            if hits:
                user_msg = build_quiz_message(hits, quiz_topic.strip())
                status.update(label="✅ 检索完成，正在生成5道题目…", state="complete")
                quiz_raw = ""
                for chunk in call_llm_stream(API_KEY, user_msg, model=selected_model, system_prompt=QUIZ_SYSTEM_PROMPT):
                    quiz_raw += chunk
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete")
                quiz_raw = ""
                st.warning("未找到相关教材内容，请换个主题试试。")

        if quiz_raw:
            # 解析题目和答案
            import re as _re
            questions, answers = [], []
            parts = _re.split(r'===题目\d+===', quiz_raw)
            ans_parts = _re.split(r'===答案\d+===', quiz_raw)
            for i in range(1, len(ans_parts)):
                answers.append(ans_parts[i].strip())
            for i in range(1, len(parts)):
                q_text = parts[i].split('===答案')[0].strip() if '===答案' in parts[i] else parts[i].strip()
                questions.append(q_text)

            st.session_state.quiz_questions = questions
            st.session_state.quiz_answers = answers
            st.session_state.quiz_topic_display = quiz_topic.strip()
            st.session_state.quiz_revealed = [False] * len(questions)

    # 显示题目
    if "quiz_questions" in st.session_state and st.session_state.quiz_questions:
        st.markdown(f"### 📝 {st.session_state.quiz_topic_display}")
        for i, q in enumerate(st.session_state.quiz_questions):
            # 修复 LaTeX 公式后显示题目
            fixed_q = fix_latex_formulas(q)
            st.markdown(f'<div class="ai-bubble"><strong>第 {i+1} 题</strong></div>', unsafe_allow_html=True)
            st.markdown(fixed_q)
            if st.session_state.quiz_revealed[i]:
                if i < len(st.session_state.quiz_answers):
                    # 修复 LaTeX 公式后显示答案
                    fixed_ans = fix_latex_formulas(st.session_state.quiz_answers[i])
                    st.markdown(f'<div style="background:#e8f5e9;border-radius:10px;padding:1rem;margin:0.5rem 0;border-left:4px solid #4caf50;"></div>', unsafe_allow_html=True)
                    st.markdown(fixed_ans)
            else:
                if st.button(f"👁️ 显示第 {i+1} 题答案", key=f"reveal_{i}"):
                    st.session_state.quiz_revealed[i] = True
                    st.rerun()
        if not all(st.session_state.quiz_revealed):
            if st.button("👁️ 显示全部答案", key="reveal_all"):
                st.session_state.quiz_revealed = [True] * len(st.session_state.quiz_questions)
                st.rerun()
    else:
        st.markdown("""<div style="text-align:center;padding:3rem;color:rgba(255,255,255,0.8);">
            <h2>📝 输入知识点主题开始刷题</h2>
            <p style="margin-top:1rem;opacity:0.8">试试：心力衰竭 | 抗生素药理 | 糖尿病分型 | 股三角解剖</p>
        </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════
# 模式三：对比学习
# ══════════════════════════════════════════════════
with mode[2]:
    st.markdown("输入两个需要对比的医学概念，系统检索后生成结构化对比表。")

    col_a, col_b = st.columns(2)
    with col_a:
        concept_a = st.text_input("概念A", placeholder="如：青霉素", label_visibility="collapsed", key="cmp_a")
    with col_b:
        concept_b = st.text_input("概念B", placeholder="如：头孢菌素", label_visibility="collapsed", key="cmp_b")

    cmp_btn = st.button("🔄 开始对比", type="primary", use_container_width=True, key="cmp_btn")

    if cmp_btn and concept_a.strip() and concept_b.strip():
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        embeddings, documents, metadatas = load_selected_books(books_to_load)
        if embeddings is None:
            st.error("未加载到教材数据"); st.stop()
        with st.status("🔍 分别检索两个概念…", expanded=True) as status:
            hits_a = search(concept_a.strip(), embeddings, documents, metadatas, k=top_k, alpha=alpha)
            hits_b = search(concept_b.strip(), embeddings, documents, metadatas, k=top_k, alpha=alpha)
            if hits_a or hits_b:
                st.write(f"✅ {concept_a} 找到 {len(hits_a)} 条，{concept_b} 找到 {len(hits_b)} 条")
                status.update(label="✅ 检索完成，正在生成对比…", state="complete")
                user_msg = build_compare_message(hits_a, hits_b, concept_a.strip(), concept_b.strip())
                # 收集流式输出
                cmp_ans = ""
                for chunk in call_llm_stream(API_KEY, user_msg, model=selected_model, system_prompt=COMPARE_SYSTEM_PROMPT):
                    cmp_ans += chunk
                # 修复 LaTeX 公式后显示
                fixed_cmp_ans = fix_latex_formulas(cmp_ans)
                st.markdown(fixed_cmp_ans)
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete")
                cmp_ans = "未找到相关教材内容，请换个概念试试。"
        all_hits = (hits_a or []) + (hits_b or [])
        st.session_state.hist.append({"q":f"[对比] {concept_a} vs {concept_b}","hits":all_hits,"a":cmp_ans,"model":MODELS.get(selected_model,""),"mode":"对比"})

    # 显示对比历史
    cmp_items = [h for h in st.session_state.hist if h.get("mode") == "对比"]
    if cmp_items:
        st.markdown("---")
        for item in reversed(cmp_items):
            st.markdown(f'<div class="user-bubble">🔄 {item["q"]}</div>', unsafe_allow_html=True)
            # 修复 LaTeX 公式后显示
            fixed_cmp = fix_latex_formulas(item["a"])
            st.markdown(f'<div class="ai-bubble"></div>', unsafe_allow_html=True)
            st.markdown(fixed_cmp)
    else:
        st.markdown("""<div style="text-align:center;padding:3rem;color:rgba(255,255,255,0.8);">
            <h2>🔄 输入两个概念开始对比学习</h2>
            <p style="margin-top:1rem;opacity:0.8">试试：1型糖尿病 vs 2型糖尿病 | 青霉素 vs 头孢菌素 | 良性肿瘤 vs 恶性肿瘤</p>
        </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════
# 模式四：病例分析
# ══════════════════════════════════════════════════
with mode[3]:
    st.markdown("输入病例描述，系统按临床推理流程分步分析。")

    case_desc = st.text_area("病例描述",
        placeholder="如：患者男，65岁，反复胸闷气促2年，加重伴双下肢水肿1周…",
        height=100, label_visibility="collapsed", key="case_desc")
    case_btn = st.button("🏥 开始分析", type="primary", use_container_width=True, key="case_btn")

    if case_btn and case_desc.strip():
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        embeddings, documents, metadatas = load_selected_books(books_to_load)
        if embeddings is None:
            st.error("未加载到教材数据"); st.stop()
        with st.status("🔍 检索相关教材…", expanded=True) as status:
            hits = search(case_desc.strip(), embeddings, documents, metadatas, k=top_k, alpha=alpha)
            if hits:
                st.write(f"✅ 找到 {len(hits)} 条相关内容")
                status.update(label="✅ 检索完成，正在分析病例…", state="complete")
                user_msg = build_case_message(hits, case_desc.strip())
                # 收集流式输出
                case_ans = ""
                for chunk in call_llm_stream(API_KEY, user_msg, model=selected_model, system_prompt=CASE_SYSTEM_PROMPT):
                    case_ans += chunk
                # 修复 LaTeX 公式后显示
                fixed_case_ans = fix_latex_formulas(case_ans)
                st.markdown(fixed_case_ans)
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete")
                case_ans = "未找到相关教材内容，请补充更多病例信息。"
        st.session_state.hist.append({"q":f"[病例] {case_desc.strip()[:50]}…","hits":hits,"a":case_ans,"model":MODELS.get(selected_model,""),"mode":"病例"})

    # 显示病例历史
    case_items = [h for h in st.session_state.hist if h.get("mode") == "病例"]
    if case_items:
        st.markdown("---")
        for item in reversed(case_items):
            st.markdown(f'<div class="user-bubble">🏥 {item["q"]}</div>', unsafe_allow_html=True)
            if item["hits"]:
                with st.expander("📚 查看参考来源", expanded=False):
                    for h in item["hits"][:3]:
                        st.markdown(f"- **{h['book']}**·{h['chapter'][:20]}")
            # 修复 LaTeX 公式后显示
            fixed_case = fix_latex_formulas(item["a"])
            st.markdown(f'<div class="ai-bubble"></div>', unsafe_allow_html=True)
            st.markdown(fixed_case)
    else:
        st.markdown("""<div style="text-align:center;padding:3rem;color:rgba(255,255,255,0.8);">
            <h2>🏥 输入病例描述开始分析</h2>
            <p style="margin-top:1rem;opacity:0.8">试试：患者男，65岁，反复胸闷气促2年，加重伴双下肢水肿1周</p>
        </div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════
# 模式五：智能体模式（ReAct Agent - 对话式）
# ══════════════════════════════════════════════════
with mode[4]:
    st.markdown("🤖 **智能体模式**：AI会自动选择工具（搜索教材、计算剂量、查询正常值、对比概念、病例分析）来回答你的问题。支持多轮对话。")

    if run_agent is None:
        st.error("⚠️ 智能体模块加载失败，请检查依赖是否安装正确。")
    else:
        # 初始化对话历史
        if "agent_turns" not in st.session_state:
            st.session_state.agent_turns = []  # [(query, answer, steps)]

        # 清空对话按钮
        col_agent_header, col_agent_clear = st.columns([4, 1])
        with col_agent_header:
            turns_count = len(st.session_state.agent_turns)
            if turns_count > 0:
                st.caption(f"💬 已进行 {turns_count} 轮对话")
        with col_agent_clear:
            if st.button("🗑️ 清空对话", use_container_width=True, key="clear_agent"):
                st.session_state.agent_turns = []
                st.rerun()

        # 显示历史对话
        for i, (q, a, steps) in enumerate(st.session_state.agent_turns):
            st.markdown(f'<div class="user-bubble">❓ {q}</div>', unsafe_allow_html=True)
            if steps:
                with st.expander(f"🔍 查看思考过程（{len(steps)} 步）", expanded=False):
                    for j, step in enumerate(steps):
                        st.markdown(f"**步骤 {j+1}:** `{step['tool']}` → {step['output'][:100]}...")
            fixed_a = fix_latex_formulas(a)
            st.markdown(f'<div class="ai-bubble"></div>', unsafe_allow_html=True)
            st.markdown(fixed_a)

        # 输入区域
        col_agent_q, col_agent_btn = st.columns([6, 1])
        with col_agent_q:
            agent_query = st.text_input("输入问题",
                placeholder="如：心衰患者用呋塞米的剂量是多少？白细胞正常值是多少？",
                label_visibility="collapsed", key="agent_query")
        with col_agent_btn:
            agent_btn = st.button("🤖 发送", type="primary", use_container_width=True, key="agent_btn")

        if agent_btn and agent_query.strip():
            query_text = agent_query.strip()

            with st.status("🤖 智能体思考中…", expanded=True) as status:
                st.write("🧠 正在分析问题并选择工具…")

                if run_agent_stream is not None:
                    # 流式输出模式
                    steps = []
                    final_answer = ""
                    has_error = False

                    # 传入对话历史
                    for event in run_agent_stream(
                        query_text, API_KEY, model=selected_model,
                        conversation_history=[(q, a) for q, a, _ in st.session_state.agent_turns]
                    ):
                        if event["type"] == "step":
                            steps.append(event["data"])
                            st.write(f"🔧 使用工具: `{event['data']['tool']}`")
                        elif event["type"] == "token":
                            final_answer += event["data"]
                        elif event["type"] == "error":
                            status.update(label="❌ 出错了", state="error")
                            st.error(f"智能体执行出错：{event['data']}")
                            has_error = True
                            break

                    if not has_error:
                        status.update(label="✅ 智能体完成", state="complete")

                        # 显示思考过程
                        if steps:
                            with st.expander(f"🔍 查看思考过程（{len(steps)} 步）", expanded=False):
                                for i, step in enumerate(steps):
                                    st.markdown(f"**步骤 {i+1}:** `{step['tool']}`")
                                    st.markdown(f"输入: `{step['input']}`")
                                    st.markdown(f"输出:\n```\n{step['output'][:300]}\n```")

                        # 显示最终回答
                        if final_answer:
                            fixed_output = fix_latex_formulas(final_answer)
                            st.markdown(fixed_output)

                        # 保存到对话历史
                        st.session_state.agent_turns.append((query_text, final_answer, steps))

                        # 同时保存到全局历史（兼容搜索历史显示）
                        st.session_state.hist.append({
                            "q": f"[智能体] {query_text}",
                            "hits": [],
                            "a": final_answer,
                            "model": MODELS.get(selected_model, ""),
                            "mode": "智能体"
                        })
                else:
                    # 降级到非流式模式
                    result = run_agent(query_text, API_KEY, model=selected_model)

                    if result["error"]:
                        status.update(label="❌ 出错了", state="error")
                        st.error(f"智能体执行出错：{result['error']}")
                    else:
                        status.update(label="✅ 智能体完成", state="complete")

                        if result["steps"]:
                            with st.expander(f"🔍 查看思考过程（{len(result['steps'])} 步）", expanded=False):
                                for i, step in enumerate(result["steps"]):
                                    st.markdown(f"**步骤 {i+1}:** `{step['tool']}`")
                                    st.markdown(f"输入: `{step['input']}`")
                                    st.markdown(f"输出:\n```\n{step['output'][:300]}\n```")

                        fixed_output = fix_latex_formulas(result["output"])
                        st.markdown(fixed_output)

                    st.session_state.agent_turns.append((query_text, result.get("output", ""), result.get("steps", [])))
                    st.session_state.hist.append({
                        "q": f"[智能体] {query_text}",
                        "hits": [],
                        "a": result.get("output", ""),
                        "model": MODELS.get(selected_model, ""),
                        "mode": "智能体"
                    })

            # 清空输入框并刷新
            st.session_state.agent_query = ""
            st.rerun()

        # 空对话提示
        if not st.session_state.agent_turns:
            st.markdown("""<div style="text-align:center;padding:2rem;color:rgba(0,0,0,0.5);">
                <h3>🤖 输入问题开始智能体对话</h3>
                <p style="margin-top:0.5rem;">试试：阿莫西林70kg成人用量 | 肌酐正常值 | 对比青霉素和头孢菌素</p>
            </div>""", unsafe_allow_html=True)
