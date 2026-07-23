"""🏥 医学教材知识库 — v2（多功能版）

入口文件：页面配置、认证流程、DOS 防护和主流程编排。
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
from config import MODELS, get_api_key, check_env_security

# 启动时检查 .env 安全状态
check_env_security()
from ui_styles import inject_global_styles
from ui_components import (
    load_manifest, get_book_stats, render_sidebar,
    render_scope_and_model_selector, render_update_announcement,
)
from dos_protection import check_rate_limit, render_rate_limit_banner
from auth_components import (
    render_auth_page, is_authenticated, get_auth_username,
    get_auth_mode, logout, set_auth_success, is_admin, get_data_manager,
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
    # 认证状态默认值
    "authenticated": False, "auth_username": "游客",
    "auth_mode_type": "guest", "auth_mode": "home",
    # 主界面视图：main（主界面）/ admin（管理面板）
    "view": "main",
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── DOS 防护：限流检查（最先执行，保护所有后续操作）────
if render_rate_limit_banner():
    st.stop()

allowed, rate_msg = check_rate_limit()
if not allowed:
    st.error(rate_msg)
    st.stop()

# ── 认证检查 ─────────────────────────────────────────────
# 检查用户是否已认证；未认证则显示认证页面（注册/登录/游客）
if not is_authenticated():
    def on_auth_submit(action: str, username: str | None, password: str | None):
        """认证提交回调：游客模式直接通过。"""
        if action == "guest":
            set_auth_success("游客", "guest")

    render_auth_page(on_submit_callback=on_auth_submit)
    st.stop()

# ── 数据加载（按需加载教材，仅认证后执行）──────────────────
manifest = load_manifest()
if manifest is None:
    st.error("books/ 目录缺失，请先运行 split_books.py"); st.stop()

ALL_BOOKS, book_count, total_chunks, book_stats = get_book_stats(manifest)

# ── 检查 API Key ────────────────────────────────────────
CS_API_KEY = get_api_key("cherryin")
if not CS_API_KEY:
    st.error("未配置 CS_API_KEY"); st.stop()

# ── 侧边栏 ─────────────────────────────────────────────
# 用户信息区域（已登录用户显示）
with st.sidebar:
    username = get_auth_username()
    auth_mode = get_auth_mode()

    if auth_mode != "guest":
        # 已注册用户
        # 管理员徽章（仅 admin 角色显示）
        admin_badge = ""
        status_text = "已登录 · 数据已同步"
        if is_admin():
            admin_badge = (
                ' <span style="font-size:0.68rem; background:#0D6EFD; color:#FFFFFF; '
                'padding:2px 8px; border-radius:8px; font-weight:600;">管理员</span>'
            )
            status_text = "已登录 · 管理员"

        st.markdown(f"""
        <div style="background:linear-gradient(135deg, rgba(13,110,253,0.08) 0%, rgba(10,88,202,0.04) 100%);
                    border-radius:12px; padding:0.8rem 1rem; margin-bottom:0.8rem;
                    border:1px solid rgba(13,110,253,0.15);">
            <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
                <span style="font-size:1.3rem;">👤</span>
                <span style="font-weight:700; color:#0A58CA; font-size:0.95rem;">{username}</span>
                {admin_badge}
            </div>
            <div style="font-size:0.78rem; color:#6C757D;">
                {status_text}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 用户快捷操作
        col_logout, col_prefs = st.columns(2)
        with col_logout:
            if st.button("🚪 退出登录", use_container_width=True, key="sidebar_logout"):
                logout()
                st.rerun()
        with col_prefs:
            if st.button("⚙️ 设置", use_container_width=True, key="sidebar_prefs"):
                st.info("个人设置功能开发中...")

        # 管理员入口（仅 admin 角色可见，普通用户和游客完全看不到）
        if is_admin():
            if st.button("🔧 管理面板", use_container_width=True, key="sidebar_admin"):
                st.session_state["view"] = "admin"
                st.rerun()
    else:
        # 游客模式
        st.markdown(f"""
        <div style="background:#FFF8E1; border-radius:12px; padding:0.8rem 1rem; margin-bottom:0.8rem;
                    border:1px solid #FFE082;">
            <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
                <span style="font-size:1.3rem;">👤</span>
                <span style="font-weight:700; color:#F57F17; font-size:0.95rem;">游客模式</span>
            </div>
            <div style="font-size:0.78rem; color:#795548;">
                数据不会保存 · 功能受限
            </div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("📝 注册/登录", use_container_width=True, key="sidebar_login_btn"):
            logout()
            st.rerun()

    st.divider()

# 渲染主侧边栏（检索设置、对话设置、搜索历史等）
top_k, alpha, use_context = render_sidebar(book_count, total_chunks)

# ── 管理面板视图切换 ──────────────────────────────────
# 安全守卫：非 admin 用户不应停留在管理面板视图
if st.session_state.get("view") == "admin" and not is_admin():
    st.session_state["view"] = "main"

if st.session_state.get("view") == "admin" and is_admin():
    from admin_panel import render_admin_panel
    manager = get_data_manager()
    render_admin_panel(manager)
    st.stop()

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
# 管理员可见管理面板标签页
tabs_list = ["💬 智能问答", "📝 自测刷题", "🔄 对比学习", "🏥 病例分析", "🤖 智能体模式"]
if is_admin():
    tabs_list.append("🔧 管理面板")
mode = st.tabs(tabs_list)

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

# ── 模式六：管理面板（仅管理员可见）──────────────────────
if is_admin():
    with mode[5]:
        from admin_panel import render_admin_panel
        manager = get_data_manager()
        render_admin_panel(manager)
