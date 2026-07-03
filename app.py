"""🏥 医学教材知识库 — v2（多功能版）

入口文件：页面配置、模块导入和主流程编排。
性能优化：模式模块延迟导入，减少首次可交互时间（TTI）。
"""

import streamlit as st

# 加载 .env 文件
try:
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

from __init__ import __version__
from config import MODELS, get_api_key
from ui_styles import inject_global_styles
from ui_components import (
    load_manifest, get_book_stats, render_sidebar,
    render_scope_and_model_selector, render_update_announcement,
)

# ── 页面配置 ────────────────────────────────────────────
st.set_page_config(
    page_title="医学教材知识库", page_icon="🏥", layout="wide",
    initial_sidebar_state="expanded"
)

# ── 样式注入（从外部 CSS 文件加载）────────────────────
inject_global_styles()

# ── 初始化会话状态 ──────────────────────────────────────
_defaults = {
    "hist": [], "conversation_turns": [], "use_context": True,
    "alpha": 0.7, "q": "", "favorites": [], "mode": "💬 智能问答",
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── 数据加载（按需加载教材）──────────────────────────────
manifest = load_manifest()
if manifest is None:
    st.error("books/ 目录缺失，请先运行 split_books.py"); st.stop()

ALL_BOOKS, book_count, total_chunks, book_stats = get_book_stats(manifest)

# ── 检查 API Key ────────────────────────────────────────
CS_API_KEY = get_api_key("cherryin")
if not CS_API_KEY:
    st.error("未配置 CS_API_KEY"); st.stop()

# ── 侧边栏 ─────────────────────────────────────────────
top_k, alpha, use_context = render_sidebar(book_count, total_chunks)

# ── 主界面标题 ──────────────────────────────────────────
st.markdown(f"""
<div class="medical-brand fade-in-up">
    <span class="brand-icon">🏥</span>
    <h1 class="brand-title">医学教材知识库</h1>
    <p class="brand-tagline">AI 驱动的医学知识检索与学习平台</p>
    <div class="brand-stats">
        <div class="stat-item">
            <span class="stat-icon">📚</span>
            <span>{book_count} 本教材</span>
        </div>
        <div class="stat-item">
            <span class="stat-icon">📄</span>
            <span>{total_chunks:,} 个知识点</span>
        </div>
        <div class="stat-item">
            <span class="stat-icon">🆓</span>
            <span>完全免费</span>
        </div>
        <div class="stat-item">
            <span class="stat-icon">🕐</span>
            <span>24h 在线</span>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ── 更新公告 ────────────────────────────────────────────
render_update_announcement()

# ── 教材范围 + 模型选择 ────────────────────────────────
scope, selected_model, selected_books = render_scope_and_model_selector(
    book_count, book_stats, ALL_BOOKS
)

# ── 模式切换 ────────────────────────────────────────────
mode = st.tabs(["💬 智能问答", "📝 自测刷题", "🔄 对比学习", "🏥 病例分析", "🤖 智能体模式"])

# ── 模式一：智能问答（延迟导入）────────────────────────
with mode[0]:
    from modes.qa import render as render_qa
    render_qa(manifest, ALL_BOOKS, book_count, top_k, alpha, use_context,
              scope, selected_model, selected_books)

# ── 模式二：自测刷题（延迟导入）────────────────────────
with mode[1]:
    from modes.quiz import render as render_quiz
    render_quiz(manifest, ALL_BOOKS, book_count, top_k, alpha,
                scope, selected_model, selected_books)

# ── 模式三：对比学习（延迟导入）────────────────────────
with mode[2]:
    from modes.compare import render as render_compare
    render_compare(manifest, ALL_BOOKS, book_count, top_k, alpha,
                   scope, selected_model, selected_books)

# ── 模式四：病例分析（延迟导入）────────────────────────
with mode[3]:
    from modes.case import render as render_case
    render_case(manifest, ALL_BOOKS, book_count, top_k, alpha,
                scope, selected_model, selected_books)

# ── 模式五：智能体模式（延迟导入）──────────────────────
with mode[4]:
    from modes.agent import render as render_agent
    render_agent(ALL_BOOKS, book_count, scope, selected_model, selected_books)
