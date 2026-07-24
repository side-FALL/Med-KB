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
from ui_styles import inject_global_styles, inject_user_preferences
from ui_components import (
    load_manifest, get_book_stats, render_sidebar,
    render_scope_and_model_selector, render_update_announcement,
)
from settings_components import (
    t, ensure_pref_defaults, load_preferences_from_user_data,
    render_user_info_section, render_preferences_section,
    render_retrieval_settings_section,
    render_textbook_management_section, render_search_history_section,
    render_learning_stats_section, render_data_management_section,
    render_account_security_section, render_about_section,
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
    "alpha": 0.7, "top_k": 10, "q": "", "favorites": [], "mode": "💬 智能问答",
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

# ── 个人偏好：恢复持久化偏好并注入主题/字体样式 ──────────
# 必须先于页面内容渲染：widget 修改偏好触发 rerun 后，
# 下一次 run 在此处注入新样式，实现切换立即生效
ensure_pref_defaults()
load_preferences_from_user_data()
inject_user_preferences()

# 从session_state获取设置值（在设置页面中更新）
top_k = st.session_state.get("top_k", 10)
alpha = st.session_state.get("alpha", 0.7)
use_context = st.session_state.get("use_context", True)

# ── 主界面标题 ──────────────────────────────────────────
st.markdown(f"""
<div class="medical-brand fade-in-up">
    <span class="brand-icon">🏥</span>
    <h1 class="brand-title">{t('app_title')}</h1>
    <p class="brand-tagline">{t('app_tagline')}</p>
    <div class="brand-stats">
        <div class="stat-item">
            <span class="stat-icon">📚</span>
            <span>{book_count} {t('stat_books')}</span>
        </div>
        <div class="stat-item">
            <span class="stat-icon">📄</span>
            <span>{total_chunks:,} {t('stat_chunks')}</span>
        </div>
        <div class="stat-item">
            <span class="stat-icon">🆓</span>
            <span>{t('stat_free')}</span>
        </div>
        <div class="stat-item">
            <span class="stat-icon">🕐</span>
            <span>{t('stat_online')}</span>
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
    render_agent(ALL_BOOKS, book_count, top_k, alpha, scope, selected_model, selected_books)

# ── 模式六：设置 ────────────────────────────────────────
with mode[5]:
    st.markdown(f"### {t('settings_title')}")

    # 用户信息（登录用户显示详细信息卡片，游客显示提示卡片）
    render_user_info_section(username, auth_mode, is_admin())

    st.divider()

    # 个人偏好（主题 / 语言 / 字体大小 / 显示名称）
    render_preferences_section()

    st.divider()

    # 检索设置（教材范围 + 检索模型 + 返回结果数/向量权重/多轮对话）
    # 与主界面双向同步：通过 on_change 回调 + session_state 共享
    render_retrieval_settings_section(book_count, book_stats, ALL_BOOKS)

    st.divider()

    # 教材管理（已选教材列表、收藏、关键词搜索与学科筛选）
    render_textbook_management_section(book_stats, ALL_BOOKS)

    st.divider()

    # 搜索历史（重新搜索、删除单条、导出 CSV/JSON）
    render_search_history_section()

    st.divider()

    # 学习统计（查询趋势折线图、模式分布柱状图）
    render_learning_stats_section()

    st.divider()

    # 数据管理（导出学习记录、导出收藏、备份恢复）
    render_data_management_section(book_stats)

    st.divider()

    # 账号安全（修改密码）
    render_account_security_section()

    st.divider()

    # 关于（版本历史、帮助文档、反馈渠道）
    render_about_section()

# ── 模式七：管理面板（仅管理员可见）──────────────────────
if is_admin():
    with mode[6]:
        from admin_panel import render_admin_panel
        manager = get_data_manager()
        render_admin_panel(manager)
