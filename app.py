"""🏥 医学教材知识库 — 优化版（全功能）"""
import streamlit as st
import os, json, numpy as np
from pathlib import Path
import requests
from openai import OpenAI

st.set_page_config(
    page_title="医学教材知识库",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── 自定义样式 ────────────────────────────────────
st.markdown("""
<style>
/* 全局背景 */
.stApp {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
}

/* 主容器 */
.main .block-container {
    max-width: 900px;
    padding: 2rem 1rem;
}

/* 标题 */
.main-title {
    text-align: center;
    color: white;
    font-size: 2.5rem;
    font-weight: 800;
    margin-bottom: 0.5rem;
    text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
}

.main-subtitle {
    text-align: center;
    color: rgba(255,255,255,0.85);
    font-size: 1rem;
    margin-bottom: 2rem;
}

/* 搜索框 */
.stTextInput > div > div > input {
    border: none;
    border-radius: 25px;
    padding: 1rem 1.5rem;
    font-size: 1.1rem;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}

/* 按钮 */
.stButton > button {
    border-radius: 25px;
    padding: 0.5rem 2rem;
    font-weight: 600;
    box-shadow: 0 4px 15px rgba(0,0,0,0.2);
}

/* 问答气泡 */
.user-bubble {
    background: linear-gradient(135deg, #667eea, #764ba2);
    color: white;
    border-radius: 20px 20px 5px 20px;
    padding: 1rem 1.5rem;
    margin: 0.5rem 0 0.5rem auto;
    max-width: 80%;
    display: block;
    text-align: right;
    box-shadow: 0 4px 15px rgba(102,126,234,0.4);
}

.ai-bubble {
    background: white;
    color: #333;
    border-radius: 20px 20px 20px 5px;
    padding: 1rem 1.5rem;
    margin: 0.5rem auto 0.5rem 0;
    max-width: 90%;
    box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    border-left: 4px solid #667eea;
}

/* 来源卡片 */
.source-card {
    background: rgba(255,255,255,0.95);
    border-radius: 12px;
    padding: 0.8rem 1rem;
    margin: 0.5rem 0;
    border-left: 4px solid #667eea;
    box-shadow: 0 2px 10px rgba(0,0,0,0.08);
    transition: transform 0.2s;
}

.source-card:hover {
    transform: translateX(5px);
}

.source-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
}

.source-book {
    font-weight: 700;
    color: #667eea;
    font-size: 0.9rem;
}

.source-score {
    background: linear-gradient(135deg, #667eea, #764ba2);
    color: white;
    padding: 0.2rem 0.6rem;
    border-radius: 10px;
    font-size: 0.75rem;
    font-weight: 600;
}

.source-text {
    color: #666;
    font-size: 0.85rem;
    line-height: 1.5;
    max-height: 100px;
    overflow-y: auto;
}

/* 侧边栏 */
.sidebar .sidebar-content {
    background: linear-gradient(180deg, #f8f9fa, #e9ecef);
}

/* 历史记录 */
.history-item {
    background: rgba(255,255,255,0.9);
    border-radius: 10px;
    padding: 0.8rem;
    margin: 0.5rem 0;
    cursor: pointer;
    transition: all 0.2s;
}

.history-item:hover {
    background: white;
    box-shadow: 0 4px 15px rgba(0,0,0,0.1);
}

/* 底部信息 */
.footer {
    text-align: center;
    color: rgba(255,255,255,0.7);
    font-size: 0.8rem;
    margin-top: 3rem;
    padding: 1rem;
}

/* 加载动画 */
@keyframes pulse {
    0% { opacity: 0.6; }
    50% { opacity: 1; }
    100% { opacity: 0.6; }
}

.loading-text {
    animation: pulse 1.5s infinite;
    color: #667eea;
    font-weight: 600;
}

/* 响应式 */
@media (max-width: 768px) {
    .main-title { font-size: 1.8rem; }
    .user-bubble, .ai-bubble { max-width: 95%; }
    .source-card { padding: 0.6rem; }
}

/* 隐藏Streamlit默认元素 */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# ── 数据加载 ──────────────────────────────────────
_APP = Path(__file__).resolve().parent
_PATHS = [
    _APP / "vectors.npz",
    _APP / "workspace" / "vectors.npz",
    Path("/workspace/vectors.npz"),
    Path("/home/user/app/vectors.npz"),
    Path("/home/user/app/workspace/vectors.npz"),
]

VPATH = None
for p in _PATHS:
    if p.exists():
        VPATH = p
        break

if VPATH is None:
    import glob
    found = list(Path("/").glob("**/vectors.npz"))[:5] if os.name != "nt" else []
    st.error(f"⚠️ vectors.npz 未找到\n\n搜索路径: {_PATHS[:3]}\n当前目录: {_APP}")
    if found:
        st.info(f"找到: {found}")
    st.stop()

MPATH = VPATH.parent / "metadata.json"

@st.cache_resource
def load_data():
    """加载向量和元数据（带缓存）"""
    if not MPATH.exists():
        st.error(f"⚠️ metadata.json 未找到: {MPATH}")
        st.stop()
    
    data = np.load(VPATH)
    embeddings = data["embeddings"].astype(np.float32)
    
    # 预归一化向量（加速检索）
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms
    
    with open(MPATH, encoding="utf-8") as f:
        meta = json.load(f)
    
    return embeddings, meta["documents"], meta["metadatas"]

# 加载数据
embeddings, documents, metadatas = load_data()

# 提取所有教材名称和统计
book_stats = {}
for m in metadatas:
    book = m.get("book", "?")
    book_stats[book] = book_stats.get(book, 0) + 1

# 按块数排序的教材列表
ALL_BOOKS = sorted(book_stats.keys(), key=lambda x: -book_stats[x])
book_count = len(ALL_BOOKS)
total_chunks = len(documents)

# ── API 配置 ──────────────────────────────────────
API_KEY = os.environ.get("CS_API_KEY", "")
if not API_KEY:
    st.error("⚠️ 未配置 CS_API_KEY 环境变量\n\n请在 Space Settings 中添加")
    st.stop()

_embed = OpenAI(api_key=API_KEY, base_url="https://open.cherryin.net/v1")

# 可用模型
MODELS = {
    "deepseek/deepseek-v4-flash(free)": "DeepSeek V4 Flash",
    "deepseek/deepseek-v3.2-250101(free)": "DeepSeek V3.2",
}

# ── 检索函数 ──────────────────────────────────────
def search(text, k=10, book_filter=None):
    """向量检索，支持教材过滤
    
    Args:
        text: 查询文本
        k: 返回结果数
        book_filter: 教材名称列表，None表示全部
    """
    try:
        r = _embed.embeddings.create(model="baai/bge-m3(free)", input=[text])
        qvec = np.array(r.data[0].embedding, dtype=np.float32)
        qvec = qvec / np.linalg.norm(qvec)  # 归一化
        
        # 余弦相似度（向量已归一化，点积即余弦相似度）
        scores = embeddings @ qvec
        
        # 如果指定了教材范围，过滤分数
        if book_filter and len(book_filter) < len(ALL_BOOKS):
            # 创建掩码：只保留选中教材的分数
            mask = np.zeros(len(scores), dtype=bool)
            for i, m in enumerate(metadatas):
                if m.get("book", "?") in book_filter:
                    mask[i] = True
            # 将未选中的教材分数设为-inf
            scores = np.where(mask, scores, -np.inf)
        
        # 获取top-k
        top_k = np.argsort(scores)[-k:][::-1]
        
        hits = []
        for idx in top_k:
            score = scores[int(idx)]
            if score == -np.inf:  # 跳过过滤掉的
                continue
            hits.append({
                "text": documents[int(idx)][:500],  # 限制长度
                "book": metadatas[int(idx)].get("book","?"),
                "chapter": metadatas[int(idx)].get("chapter","?"),
                "similarity": round(float(score), 4),
            })
        return hits
    except Exception as e:
        st.error(f"检索失败: {e}")
        return []

def synthesize(q, hits, model="deepseek/deepseek-v4-flash(free)"):
    """AI回答生成"""
    ctx = "\n\n---\n\n".join(
        f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}" 
        for i, h in enumerate(hits[:5])  # 只用前5个结果
    )
    
    system_prompt = """你是医学知识助手。根据教材段落回答用户问题。

要求：
1. 综合多个段落信息，给出准确、有条理的回答
2. 标注信息来源（如[1][2]）
3. 如果检索结果不足，诚实说明
4. 用中文回答，专业术语可附英文
5. 回答末尾列出参考来源"""
    
    try:
        r = requests.post(
            "https://open.cherryin.net/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"教材段落:\n{ctx}\n\n问题: {q}"}
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=90
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ 生成回答失败: {e}"

# ── 侧边栏 ────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ 设置")
    
    # 模型选择
    selected_model = st.selectbox(
        "AI 模型",
        options=list(MODELS.keys()),
        format_func=lambda x: MODELS[x],
        index=0
    )
    
    # 返回结果数
    top_k = st.slider("返回结果数", 3, 15, 8)
    
    st.divider()
    
    # 教材范围选择
    st.markdown("### 📚 教材范围")
    
    # 选择模式
    book_mode = st.radio(
        "检索范围",
        ["全部教材", "选择教材"],
        index=0,
        horizontal=True
    )
    
    selected_books = ALL_BOOKS  # 默认全部
    
    if book_mode == "选择教材":
        # 快捷选择
        col1, col2 = st.columns(2)
        with col1:
            if st.button("全选", use_container_width=True):
                st.session_state.selected_books = ALL_BOOKS
        with col2:
            if st.button("清空", use_container_width=True):
                st.session_state.selected_books = []
        
        # 初始化
        if "selected_books" not in st.session_state:
            st.session_state.selected_books = ALL_BOOKS
        
        # 教材多选框
        selected_books = st.multiselect(
            "选择教材",
            options=ALL_BOOKS,
            default=st.session_state.selected_books,
            format_func=lambda x: f"{x} ({book_stats[x]}块)",
            key="book_selector"
        )
        st.session_state.selected_books = selected_books
        
        # 显示选中数量
        selected_chunks = sum(book_stats.get(b, 0) for b in selected_books)
        st.caption(f"已选 {len(selected_books)} 本，{selected_chunks} 个文本块")
    
    st.divider()
    
    # 搜索历史
    st.markdown("### 📜 搜索历史")
    if "hist" in st.session_state and st.session_state.hist:
        for i, item in enumerate(reversed(st.session_state.hist[-10:])):
            if st.button(f"🔍 {item['q'][:30]}...", key=f"hist_{i}"):
                st.session_state.q = item['q']
                st.rerun()
    else:
        st.caption("暂无搜索历史")
    
    st.divider()
    
    # 清空历史
    if st.button("🗑️ 清空历史", use_container_width=True):
        st.session_state.hist = []
        st.rerun()
    
    st.divider()
    
    # 信息
    st.markdown(f"""
    **📊 知识库统计**
    - 教材数量: {book_count} 本
    - 文本块数: {total_chunks} 块
    - 嵌入模型: BGE-M3 (免费)
    - 回答模型: {MODELS.get(selected_model, selected_model)}
    """)

# ── 主界面 ────────────────────────────────────────
# 标题
st.markdown('<h1 class="main-title">🏥 医学教材知识库</h1>', unsafe_allow_html=True)
st.markdown(f'<p class="main-subtitle">{book_count} 本教材 · {len(documents)} 个知识点 · 全免费 · 24h在线</p>', unsafe_allow_html=True)

# 初始化会话状态
if "hist" not in st.session_state:
    st.session_state.hist = []
if "q" not in st.session_state:
    st.session_state.q = ""

# 搜索框
col1, col2 = st.columns([5, 1])
with col1:
    q = st.text_input(
        "🔍 输入医学问题",
        value=st.session_state.q,
        placeholder="如：心衰的病理机制、股三角的构成、疟原虫的生活史…",
        label_visibility="collapsed",
        key="search_input"
    )
with col2:
    search_btn = st.button("搜索", type="primary", use_container_width=True)

# 处理搜索
if (search_btn or q) and q.strip():
    st.session_state.q = q.strip()
    
    # 获取选中的教材范围
    if book_mode == "全部教材":
        book_filter = None
    else:
        book_filter = selected_books if selected_books else ALL_BOOKS
    
    # 检索
    with st.spinner("🔍 正在检索相关教材段落..."):
        hits = search(q.strip(), k=top_k, book_filter=book_filter)
    
    if hits:
        # 生成回答
        with st.spinner("💡 正在生成回答..."):
            ans = synthesize(q.strip(), hits, model=selected_model)
    else:
        if book_mode == "选择教材" and not selected_books:
            ans = "⚠️ 请先选择要检索的教材"
        else:
            ans = "⚠️ 未找到相关内容，请尝试换个问法"
    
    # 保存到历史
    st.session_state.hist.append({
        "q": q.strip(),
        "hits": hits,
        "a": ans,
        "model": MODELS.get(selected_model, selected_model),
        "scope": book_mode
    })

# 显示对话历史
if st.session_state.hist:
    st.markdown("---")
    
    for item in reversed(st.session_state.hist):
        # 用户问题
        st.markdown(f'<div class="user-bubble">❓ {item["q"]}</div>', unsafe_allow_html=True)
        
        # 来源卡片
        if item["hits"]:
            st.markdown("**📚 参考来源：**")
            cols = st.columns(min(3, len(item["hits"][:5])))
            for i, h in enumerate(item["hits"][:5]):
                with cols[i % 3]:
                    score_color = "🟢" if h["similarity"] > 0.8 else "🟡" if h["similarity"] > 0.6 else "🔴"
                    st.markdown(f"""
                    <div class="source-card">
                        <div class="source-header">
                            <span class="source-book">{h['book'][:15]}...</span>
                            <span class="source-score">{score_color} {h['similarity']:.2f}</span>
                        </div>
                        <div class="source-text">{h['text'][:150]}...</div>
                    </div>
                    """, unsafe_allow_html=True)
        
        # AI回答
        st.markdown(f"""
        <div class="ai-bubble">
            <strong>💡 {item.get('model', 'AI')} 回答：</strong><br><br>
            {item['a']}
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)

else:
    # 空状态提示
    st.markdown("""
    <div style="text-align:center; padding:3rem; color:rgba(255,255,255,0.8);">
        <h2>👋 欢迎使用医学教材知识库</h2>
        <p style="font-size:1.1rem; margin-top:1rem;">
            输入任何医学问题，AI会从教材中检索相关内容并生成回答
        </p>
        <p style="font-size:0.9rem; margin-top:0.5rem; opacity:0.8;">
            试试：心衰的病理机制 | 股三角的构成 | 疟原虫的生活史
        </p>
    </div>
    """, unsafe_allow_html=True)

# 底部信息
st.markdown("""
<div class="footer">
    <p>📚 医学教材知识库 · 全免费模型 · 24h在线</p>
    <p>嵌入: CherryIN BGE-M3 | 回答: DeepSeek V4 Flash</p>
</div>
""", unsafe_allow_html=True)
