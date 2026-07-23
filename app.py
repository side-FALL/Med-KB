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

# ── 侧边栏已移除，设置功能移至主界面设置标签页 ───────────
# 获取用户信息
username = get_auth_username()
auth_mode = get_auth_mode()

# 从session_state获取设置值（在设置页面中更新）
top_k = st.session_state.get("top_k", 10)
alpha = st.session_state.get("alpha", 0.7)
use_context = st.session_state.get("use_context", True)

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
# 所有用户可见设置标签页，管理员额外可见管理面板标签页
tabs_list = ["💬 智能问答", "📝 自测刷题", "🔄 对比学习", "🏥 病例分析", "🤖 智能体模式", "⚙️ 设置"]
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

# ── 模式六：设置 ────────────────────────────────────────
with mode[5]:
    st.markdown("### ⚙️ 个人设置")

    # 用户信息
    if auth_mode != "guest":
        user_data = st.session_state.get("user_data", {})
        profile = user_data.get("profile", {})

        # 用户信息卡片
        admin_badge = ""
        if is_admin():
            admin_badge = ' <span style="font-size:0.68rem; background:#0D6EFD; color:#FFFFFF; padding:2px 8px; border-radius:8px; font-weight:600;">管理员</span>'

        st.markdown(f"""
        <div style="background:linear-gradient(135deg, rgba(13,110,253,0.08) 0%, rgba(10,88,202,0.04) 100%);
                    border-radius:12px; padding:1rem; margin-bottom:1rem;
                    border:1px solid rgba(13,110,253,0.15);">
            <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.5rem;">
                <span style="font-size:1.5rem;">👤</span>
                <span style="font-weight:700; color:#0A58CA; font-size:1.1rem;">{profile.get('username', 'N/A')}</span>
                {admin_badge}
            </div>
            <div style="display:flex; gap:2rem; color:#6C757D; font-size:0.85rem;">
                <span>登录次数: {user_data.get('stats', {}).get('login_count', 0)}</span>
                <span>查询次数: {user_data.get('stats', {}).get('total_queries', 0)}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 退出登录按钮
        if st.button("🚪 退出登录", use_container_width=True, key="settings_logout"):
            logout()
            st.rerun()
    else:
        # 游客模式
        st.markdown("""
        <div style="background:#FFF8E1; border-radius:12px; padding:1rem; margin-bottom:1rem;
                    border:1px solid #FFE082;">
            <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.5rem;">
                <span style="font-size:1.5rem;">👤</span>
                <span style="font-weight:700; color:#F57F17; font-size:1.1rem;">游客模式</span>
            </div>
            <div style="color:#795548; font-size:0.85rem;">数据不会保存 · 功能受限</div>
        </div>
        """, unsafe_allow_html=True)

        if st.button("📝 注册/登录", use_container_width=True, key="settings_login"):
            logout()
            st.rerun()

    st.divider()

    # 检索设置
    st.markdown("#### 🔍 检索设置")
    top_k = st.slider("返回结果数", 3, 15, st.session_state.get("top_k", 10),
        help="每次检索返回的相关文本块数量")
    alpha = st.slider("向量权重 (α)", 0.0, 1.0, st.session_state.get("alpha", 0.7),
        help="1.0=纯向量检索，0.0=纯关键词检索")
    use_context = st.checkbox("启用多轮对话",
        value=st.session_state.get("use_context", True),
        help="开启后AI会参考之前对话的上下文")

    # 更新session_state
    st.session_state["top_k"] = top_k
    st.session_state["alpha"] = alpha
    st.session_state["use_context"] = use_context

    st.divider()

    # 搜索历史
    st.markdown("#### 📜 搜索历史")
    if st.session_state.hist:
        for i, item in enumerate(reversed(st.session_state.hist[-10:])):
            mode_icon = {"问答":"💬","刷题":"📝","对比":"🔄","病例":"🏥","智能体":"🤖"}.get(item.get("mode","问答"),"💬")
            st.markdown(f"{mode_icon} {item.get('q', '')[:50]}")
    else:
        st.info("暂无搜索历史")

    if st.button("🗑️ 清空全部记录", use_container_width=True):
        st.session_state.hist = []
        st.session_state.conversation_turns = []
        st.session_state.favorites = []
        st.rerun()

    st.divider()

    # 统计信息
    st.markdown("#### 📊 知识库统计")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("教材数量", f"{book_count} 本")
    with col2:
        st.metric("文本块数", f"{total_chunks:,} 块")
    with col3:
        st.metric("嵌入模型", "BGE-M3")

    st.divider()

    # 关于
    st.markdown("#### ℹ️ 关于")
    from __init__ import __version__
    st.markdown(f"**版本：** {__version__}")
    st.markdown("**项目：** 医学教材知识库")
    st.markdown("**说明：** AI 驱动的医学知识检索与学习平台")

# ── 模式七：管理面板（仅管理员可见）──────────────────────
if is_admin():
    with mode[6]:
        from admin_panel import render_admin_panel
        manager = get_data_manager()
        render_admin_panel(manager)
