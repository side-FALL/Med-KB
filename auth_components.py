"""医学教材知识库 — 用户认证界面组件

包含注册、登录、游客三种认证模式的 UI 与前端验证逻辑。
设计风格：医疗蓝主题，与项目整体视觉一致。
"""

import re
import time

import streamlit as st

from auth_logic import register_user, login_user, create_auth_manager
from database_models import USERNAME_PATTERN as _USERNAME_PATTERN, RESERVED_USERNAMES as _RESERVED_USERNAMES


# ── 常量 ─────────────────────────────────────────────────

_USERNAME_MIN = 3
_USERNAME_MAX = 20
_PASSWORD_MIN = 8

# 认证模式枚举
AUTH_MODE_HOME = "home"
AUTH_MODE_LOGIN = "login"
AUTH_MODE_REGISTER = "register"
AUTH_MODE_GUEST = "guest"


# ── 输入验证 ─────────────────────────────────────────────

def validate_username(username: str) -> str | None:
    """验证用户名，返回错误信息或 None。"""
    if not username or not username.strip():
        return "请输入用户名"
    username = username.strip()
    if len(username) < _USERNAME_MIN:
        return f"用户名至少 {_USERNAME_MIN} 个字符"
    if len(username) > _USERNAME_MAX:
        return f"用户名最多 {_USERNAME_MAX} 个字符"
    if not _USERNAME_PATTERN.match(username):
        return "用户名只能包含字母、数字和下划线"
    if username.lower() in _RESERVED_USERNAMES:
        return "该用户名为保留名称，请更换"
    return None


def validate_password(password: str) -> str | None:
    """验证密码，返回错误信息或 None。"""
    if not password:
        return "请输入密码"
    if len(password) < _PASSWORD_MIN:
        return f"密码至少 {_PASSWORD_MIN} 个字符"
    # 复杂度要求：至少包含大写字母、小写字母、数字中的两种
    categories = 0
    if re.search(r"[A-Z]", password):
        categories += 1
    if re.search(r"[a-z]", password):
        categories += 1
    if re.search(r"[0-9]", password):
        categories += 1
    if categories < 2:
        return "密码需包含大写字母、小写字母、数字中的至少两种"
    return None


def _calc_password_strength(password: str) -> int:
    """计算密码强度分数（0-5），用于 UI 指示器。"""
    if not password:
        return 0
    score = 0
    if len(password) >= _PASSWORD_MIN:
        score += 1
    if len(password) >= 10:
        score += 1
    if re.search(r"[A-Z]", password):
        score += 1
    if re.search(r"[0-9]", password):
        score += 1
    if re.search(r"[^a-zA-Z0-9]", password):
        score += 1
    return score


def validate_confirm_password(password: str, confirm: str) -> str | None:
    """验证确认密码，返回错误信息或 None。"""
    if not confirm:
        return "请再次输入密码"
    if password != confirm:
        return "两次密码不一致"
    return None


# ── 认证样式注入 ─────────────────────────────────────────

_AUTH_CSS = """
<style>
/* ── 认证页面整体布局 ─────────────────────────────────── */
.auth-container {
    max-width: 440px;
    margin: 2rem auto;
    padding: 0 1rem;
}

/* ── 品牌头部 ─────────────────────────────────────────── */
.auth-brand {
    text-align: center;
    margin-bottom: 2rem;
    padding: 2rem 1.5rem 1.5rem;
    background: linear-gradient(135deg, rgba(13, 110, 253, 0.06) 0%, rgba(10, 88, 202, 0.03) 100%);
    border-radius: 20px;
    border: 1px solid rgba(13, 110, 253, 0.1);
}

.auth-brand-icon {
    font-size: 3.5rem;
    display: block;
    margin-bottom: 0.6rem;
    filter: drop-shadow(0 4px 12px rgba(13, 110, 253, 0.2));
}

.auth-brand-title {
    font-size: 1.8rem;
    font-weight: 800;
    color: #0A58CA;
    margin: 0 0 0.3rem 0;
    letter-spacing: -0.01em;
}

.auth-brand-subtitle {
    font-size: 0.95rem;
    color: #6C757D;
    margin: 0;
    line-height: 1.5;
}

/* ── 表单卡片 ─────────────────────────────────────────── */
.auth-form-card {
    background: white;
    border-radius: 16px;
    padding: 2rem 1.8rem;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.06);
    border: 1px solid #E9ECEF;
    animation: fadeInUp 0.35s ease-out;
}

.auth-form-header {
    text-align: center;
    margin-bottom: 1.5rem;
}

.auth-form-icon {
    font-size: 2.5rem;
    display: block;
    margin-bottom: 0.5rem;
}

.auth-form-title {
    font-size: 1.4rem;
    font-weight: 700;
    color: #0A58CA;
    margin: 0 0 0.3rem 0;
}

.auth-form-subtitle {
    font-size: 0.85rem;
    color: #6C757D;
    margin: 0;
}

/* ── 游客模式说明 ─────────────────────────────────────── */
.auth-guest-info {
    background: #FFF8E1;
    border: 1px solid #FFE082;
    border-radius: 12px;
    padding: 1rem 1.2rem;
    margin-bottom: 1.2rem;
}

.auth-guest-info-title {
    font-weight: 700;
    color: #F57F17;
    font-size: 0.9rem;
    margin: 0 0 0.4rem 0;
    display: flex;
    align-items: center;
    gap: 0.4rem;
}

.auth-guest-info-text {
    color: #795548;
    font-size: 0.82rem;
    line-height: 1.6;
    margin: 0;
}

.auth-guest-limits {
    list-style: none;
    padding: 0;
    margin: 0.6rem 0 0 0;
}

.auth-guest-limits li {
    color: #795548;
    font-size: 0.82rem;
    padding: 0.2rem 0;
    display: flex;
    align-items: center;
    gap: 0.4rem;
}

.auth-guest-limits li::before {
    content: "•";
    color: #F57F17;
    font-weight: 700;
}

/* ── 验证提示 ─────────────────────────────────────────── */
.auth-hint {
    font-size: 0.78rem;
    margin: 0.2rem 0 0 0;
    padding-left: 0.1rem;
}

.auth-hint.error {
    color: #C62828;
}

.auth-hint.success {
    color: #2E7D32;
}

.auth-hint.info {
    color: #6C757D;
}

/* ── 加载动画 ─────────────────────────────────────────── */
.auth-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.6rem;
    padding: 1rem;
    color: #0A58CA;
    font-weight: 600;
    font-size: 0.95rem;
}

.auth-loading-dots {
    display: flex;
    gap: 4px;
}

.auth-loading-dots span {
    width: 6px;
    height: 6px;
    background: #0D6EFD;
    border-radius: 50%;
    animation: authDotPulse 1.2s ease-in-out infinite;
}

.auth-loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.auth-loading-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes authDotPulse {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
    40% { opacity: 1; transform: scale(1.1); }
}

/* ── 底部链接 ─────────────────────────────────────────── */
.auth-footer {
    text-align: center;
    margin-top: 1.2rem;
    font-size: 0.85rem;
    color: #6C757D;
}

.auth-link {
    color: #0D6EFD;
    cursor: pointer;
    font-weight: 600;
    text-decoration: none;
    transition: color 0.2s;
}

.auth-link:hover {
    color: #0A58CA;
    text-decoration: underline;
}

/* ── 密码强度指示条 ───────────────────────────────────── */
.auth-pwd-strength {
    height: 4px;
    border-radius: 2px;
    margin: 0.4rem 0 0.2rem;
    background: #E9ECEF;
    overflow: hidden;
}

.auth-pwd-strength-bar {
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease, background 0.3s ease;
}

/* ── 响应式 ───────────────────────────────────────────── */
@media (max-width: 768px) {
    .auth-container {
        max-width: 100%;
        margin: 1rem auto;
        padding: 0 0.6rem;
    }

    .auth-brand {
        padding: 1.5rem 1rem 1rem;
        margin-bottom: 1.2rem;
    }

    .auth-brand-icon {
        font-size: 2.8rem;
    }

    .auth-brand-title {
        font-size: 1.4rem;
    }

    .auth-form-card {
        padding: 1.5rem 1.2rem;
    }
}

@media (max-width: 480px) {
    .auth-form-card {
        padding: 1.2rem 1rem;
        border-radius: 12px;
    }
}

/* ── 首页卡片按钮样式 ──────────────────────────────────── */
.auth-options-area [data-testid="stHorizontalBlock"] > [data-testid="stVerticalBlock"] {
    padding: 0 4px;
}

.auth-options-area .stButton {
    width: 100%;
}

.auth-options-area .stButton > button {
    background: white !important;
    border: 2px solid #E9ECEF !important;
    border-radius: 14px !important;
    padding: 1.2rem 1.4rem !important;
    cursor: pointer !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
    width: 100% !important;
    text-align: left !important;
    white-space: normal !important;
    height: auto !important;
    min-height: 80px !important;
}

/* primary 按钮高亮边框 */
.auth-options-area button[data-testid="stBaseButton-primary"] {
    border-color: #0D6EFD !important;
    background: linear-gradient(135deg, rgba(13, 110, 253, 0.04) 0%, rgba(10, 88, 202, 0.02) 100%) !important;
}

/* hover 效果 */
.auth-options-area .stButton > button:hover {
    border-color: #0D6EFD !important;
    box-shadow: 0 6px 20px rgba(13, 110, 253, 0.12) !important;
    transform: translateY(-2px);
}

/* 箭头伪元素 */
.auth-options-area .stButton > button::after {
    content: "→";
    font-size: 1.2rem;
    color: #ADB5BD;
    flex-shrink: 0;
    margin-left: auto;
    padding-left: 0.6rem;
    transition: all 0.2s ease;
}

.auth-options-area .stButton > button:hover::after {
    transform: translateX(3px);
    color: #0D6EFD;
}

/* 移动端（≤768px）卡片纵向堆叠，宽度100% */
@media (max-width: 768px) {
    .auth-options-area [data-testid="stHorizontalBlock"] {
        flex-direction: column !important;
    }

    .auth-options-area [data-testid="stHorizontalBlock"] > [data-testid="stVerticalBlock"] {
        width: 100% !important;
        flex: none !important;
    }
}

/* ── 隐藏 Streamlit 默认元素（认证页面专用）────────────── */
.auth-page [data-testid="stSidebar"] {
    display: none;
}

.auth-page header[data-testid="stHeader"] {
    display: none;
}
</style>
"""


def _inject_auth_styles():
    """注入认证页面专用 CSS。"""
    st.markdown(_AUTH_CSS, unsafe_allow_html=True)


# ── 渲染函数 ─────────────────────────────────────────────

def _render_brand():
    """渲染认证页面品牌头部。"""
    st.markdown("""
    <div class="auth-brand fade-in-up">
        <span class="auth-brand-icon">🏥</span>
        <h1 class="auth-brand-title">医学教材知识库</h1>
        <p class="auth-brand-subtitle">AI 驱动的医学知识检索与学习平台</p>
    </div>
    """, unsafe_allow_html=True)


def _render_home_options():
    """渲染首页三选项：注册、登录、游客。

    每个选项使用 st.button 直接渲染为卡片样式的可点击按钮，
    CSS 通过 .auth-options-area 作用域控制卡片外观（白色背景、圆角、hover 效果）。
    """
    st.markdown('<div class="auth-options-area">', unsafe_allow_html=True)

    options = [
        ("📝", "注册新账号", "创建您的专属学习账号", AUTH_MODE_REGISTER, True),
        ("🔑", "登录已有账号", "欢迎回来，继续学习之旅", AUTH_MODE_LOGIN, False),
        ("👤", "游客模式", "无需注册，立即开始探索", AUTH_MODE_GUEST, False),
    ]

    cols = st.columns(3)
    for i, (icon, title, desc, mode, is_primary) in enumerate(options):
        with cols[i]:
            label = f"{icon} **{title}**\n\n{desc}"
            if st.button(label, use_container_width=True,
                         type="primary" if is_primary else "secondary",
                         key=f"btn_{mode}"):
                st.session_state["auth_mode"] = mode
                st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


def _render_password_strength(password: str) -> int:
    """渲染密码强度指示条，返回强度分数。"""
    if not password:
        return 0
    score = _calc_password_strength(password)

    levels = [
        (1, "#C62828", "弱"),
        (2, "#F57F17", "一般"),
        (3, "#F9A825", "中等"),
        (4, "#2E7D32", "较强"),
        (5, "#1B5E20", "强"),
    ]

    pct = min(score * 20, 100)
    color = "#C62828"
    label = "弱"
    for s, c, l in levels:
        if score <= s:
            color, label = c, l
            break

    st.markdown(f"""
    <div class="auth-pwd-strength">
        <div class="auth-pwd-strength-bar" style="width:{pct}%; background:{color};"></div>
    </div>
    <div class="auth-hint info" style="text-align:right;">密码强度：{label}</div>
    """, unsafe_allow_html=True)
    return score


def _render_register_form(on_submit_callback=None):
    """渲染注册表单。"""
    st.markdown("""
    <div class="auth-form-card">
        <div class="auth-form-header">
            <span class="auth-form-icon">📝</span>
            <h2 class="auth-form-title">注册新账号</h2>
            <p class="auth-form-subtitle">创建您的专属学习账号</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 显示注册错误信息（来自上一次提交失败）
    if st.session_state.get("register_error"):
        st.error(f"❌ {st.session_state.pop('register_error')}")

    # 显示注册成功信息
    if st.session_state.get("register_success"):
        st.session_state.pop("register_success", None)
        reg_username = st.session_state.pop("register_username", "")
        st.success(f"✅ 注册成功！欢迎 {reg_username}，正在跳转...")

    with st.form("register_form", clear_on_submit=False):
        username = st.text_input(
            "用户名",
            placeholder="3-20位，支持字母、数字、下划线、中文",
            key="reg_username",
            max_chars=_USERNAME_MAX,
        )
        # 实时验证用户名
        if username:
            err = validate_username(username)
            if err:
                st.markdown(f'<p class="auth-hint error">⚠️ {err}</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="auth-hint success">✅ 用户名格式正确</p>', unsafe_allow_html=True)

        password = st.text_input(
            "密码",
            type="password",
            placeholder=f"至少 {_PASSWORD_MIN} 个字符",
            key="reg_password",
        )
        # 密码强度指示
        pwd_strength = _render_password_strength(password)
        if password and pwd_strength <= 1:
            st.markdown(
                '<p class="auth-hint error">⚠️ 密码强度过弱，请使用更复杂的密码（至少包含大写字母、小写字母、数字中的两种）</p>',
                unsafe_allow_html=True,
            )

        confirm_password = st.text_input(
            "确认密码",
            type="password",
            placeholder="再次输入密码",
            key="reg_confirm",
        )
        # 实时验证确认密码
        if confirm_password and password:
            err = validate_confirm_password(password, confirm_password)
            if err:
                st.markdown(f'<p class="auth-hint error">⚠️ {err}</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="auth-hint success">✅ 密码一致</p>', unsafe_allow_html=True)

        submitted = st.form_submit_button("注册", use_container_width=True, type="primary")

        if submitted:
            # 完整验证
            errors = []
            username_err = validate_username(username)
            if username_err:
                errors.append(username_err)
            password_err = validate_password(password)
            if password_err:
                errors.append(password_err)
            confirm_err = validate_confirm_password(password, confirm_password)
            if confirm_err:
                errors.append(confirm_err)

            # 阻止弱密码注册（强度分数 <= 1 视为弱）
            if password and _calc_password_strength(password) <= 1:
                errors.append("密码强度过弱，请使用更复杂的密码")

            if errors:
                for err in errors:
                    st.error(f"❌ {err}")
            else:
                # 调用注册逻辑
                _handle_register(username.strip(), password, on_submit_callback)

    # 底部链接
    st.markdown("""
    <div class="auth-footer">
        已有账号？<span class="auth-link">返回登录</span>
    </div>
    """, unsafe_allow_html=True)

    if st.button("← 返回登录", key="back_to_login_from_reg"):
        st.session_state["auth_mode"] = AUTH_MODE_LOGIN
        st.rerun()


def _render_login_form(on_submit_callback=None):
    """渲染登录表单。"""
    st.markdown("""
    <div class="auth-form-card">
        <div class="auth-form-header">
            <span class="auth-form-icon">🔑</span>
            <h2 class="auth-form-title">登录</h2>
            <p class="auth-form-subtitle">欢迎回来，继续您的学习之旅</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 显示登录错误信息（来自上一次提交失败）
    if st.session_state.get("login_error"):
        st.error(f"❌ {st.session_state.pop('login_error')}")

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input(
            "用户名",
            placeholder="请输入用户名",
            key="login_username",
            max_chars=_USERNAME_MAX,
        )
        # 实时验证用户名格式
        if username:
            err = validate_username(username)
            if err:
                st.markdown(f'<p class="auth-hint error">⚠️ {err}</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="auth-hint success">✅ 用户名格式正确</p>', unsafe_allow_html=True)

        password = st.text_input(
            "密码",
            type="password",
            placeholder="请输入密码",
            key="login_password",
        )

        submitted = st.form_submit_button("登录", use_container_width=True, type="primary")

        if submitted:
            errors = []
            # 验证用户名格式
            username_err = validate_username(username)
            if username_err:
                errors.append(username_err)
            # 验证密码
            if not password:
                errors.append("请输入密码")

            if errors:
                for err in errors:
                    st.error(f"❌ {err}")
            else:
                # 调用登录逻辑
                _handle_login(username.strip(), password, on_submit_callback)

    # 忘记密码提示（当前不支持，预留入口）
    st.markdown("""
    <div class="auth-footer">
        <span class="auth-link" style="opacity:0.5; cursor:default;" title="功能开发中">忘记密码？</span>
        <span style="margin: 0 0.5rem; color:#DEE2E6;">|</span>
        还没有账号？
    </div>
    """, unsafe_allow_html=True)

    col_back, col_reg = st.columns(2)
    with col_back:
        if st.button("← 返回首页", key="back_home_from_login"):
            st.session_state["auth_mode"] = AUTH_MODE_HOME
            st.rerun()
    with col_reg:
        if st.button("注册新账号", key="go_register_from_login"):
            st.session_state["auth_mode"] = AUTH_MODE_REGISTER
            st.rerun()


def _render_guest_info(on_confirm_callback=None):
    """渲染游客模式说明。"""
    st.markdown("""
    <div class="auth-form-card">
        <div class="auth-form-header">
            <span class="auth-form-icon">👤</span>
            <h2 class="auth-form-title">游客模式</h2>
            <p class="auth-form-subtitle">无需注册，立即开始探索</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="auth-guest-info fade-in-up">
        <div class="auth-guest-info-title">⚠️ 游客模式限制</div>
        <p class="auth-guest-info-text">
            游客模式下您的学习数据不会被保存。以下功能将受限：
        </p>
        <ul class="auth-guest-limits">
            <li>学习记录不会保存，退出后丢失</li>
            <li>无法使用收藏夹功能</li>
            <li>对话历史不会持久化</li>
            <li>个性化设置无法保存</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#E8F5E9; border-radius:12px; padding:1rem 1.2rem; margin-bottom:1.2rem;">
        <div style="font-weight:700; color:#2E7D32; font-size:0.9rem; margin-bottom:0.3rem;">
            ✅ 游客模式可以使用的功能
        </div>
        <div style="color:#495057; font-size:0.82rem; line-height:1.6;">
            智能问答、自测刷题、对比学习、病例分析、智能体模式等核心检索功能均可正常使用。
        </div>
    </div>
    """, unsafe_allow_html=True)

    col_confirm, col_back = st.columns(2)
    with col_confirm:
        if st.button("👤 确认进入游客模式", use_container_width=True, type="primary", key="confirm_guest"):
            if on_confirm_callback:
                on_confirm_callback("guest", None, None)
    with col_back:
        if st.button("← 返回首页", use_container_width=True, key="back_home_from_guest"):
            st.session_state["auth_mode"] = AUTH_MODE_HOME
            st.rerun()


def _render_loading(message: str):
    """渲染加载状态。"""
    st.markdown(f"""
    <div class="auth-loading fade-in-up">
        <div class="auth-loading-dots">
            <span></span><span></span><span></span>
        </div>
        <span>{message}</span>
    </div>
    """, unsafe_allow_html=True)


# ── 注册/登录处理 ────────────────────────────────────────

def _get_or_create_manager() -> "UserDataManager":
    """获取或创建 UserDataManager 单例（存储在 session_state 中）。"""
    if "auth_data_manager" not in st.session_state:
        st.session_state["auth_data_manager"] = create_auth_manager()
    return st.session_state["auth_data_manager"]


def _handle_register(username: str, password: str, on_submit_callback=None):
    """处理注册提交。

    流程：
    1. 显示加载状态（st.spinner 提供可见的加载指示）
    2. 调用 auth_logic.register_user
    3. 成功 → 自动登录并跳转
    4. 失败 → 显示错误提示
    """
    # 获取管理器
    manager = _get_or_create_manager()

    # 使用 st.spinner 提供可见的加载状态提示
    with st.spinner("正在注册，请稍候..."):
        # 调用注册逻辑
        user_data, error_msg = register_user(username, password, manager)

    if error_msg:
        # 注册失败
        st.session_state["register_error"] = error_msg
        st.rerun()
    else:
        # 注册成功 → 自动登录
        st.session_state["register_success"] = True
        st.session_state["register_username"] = username

        # 先存储用户数据，再设置认证状态（set_auth_success 需读取 user_data 恢复 model_authed）
        st.session_state["user_data"] = user_data
        set_auth_success(username, "register")

        # 跳转
        st.rerun()


def _handle_login(username: str, password: str, on_submit_callback=None):
    """处理登录提交。

    流程：
    1. 显示加载状态（st.spinner 提供可见的加载指示）
    2. 调用 auth_logic.login_user
    3. 成功 → 设置认证状态并跳转
    4. 失败 → 显示错误提示
    """
    # 获取管理器
    manager = _get_or_create_manager()

    # 使用 st.spinner 提供可见的加载状态提示
    with st.spinner("正在登录，请稍候..."):
        # 调用登录逻辑
        user_data, error_msg = login_user(username, password, manager)

    if error_msg:
        # 登录失败
        st.session_state["login_error"] = error_msg
        st.rerun()
    else:
        # 登录成功
        st.session_state["login_success"] = True

        # 先存储用户数据，再设置认证状态（set_auth_success 需读取 user_data 恢复 model_authed）
        st.session_state["user_data"] = user_data
        set_auth_success(username, "login")

        # 跳转
        st.rerun()


# ── 主入口 ───────────────────────────────────────────────

def render_auth_page(on_submit_callback=None):
    """渲染完整的认证页面。

    根据 session_state["auth_mode"] 显示不同视图：
    - home: 首页三选项（注册 / 登录 / 游客）
    - register: 注册表单
    - login: 登录表单
    - guest: 游客模式说明

    Args:
        on_submit_callback: 提交回调函数，签名 (action: str, username: str|None, password: str|None)
            action 为 "register" / "login" / "guest"
    """
    _inject_auth_styles()

    # 初始化认证模式
    if "auth_mode" not in st.session_state:
        st.session_state["auth_mode"] = AUTH_MODE_HOME

    mode = st.session_state["auth_mode"]

    # 检查是否正在加载
    if st.session_state.get("auth_loading"):
        loading_msg = st.session_state.get("auth_loading_message", "处理中...")
        _render_loading(loading_msg)
        return

    # 渲染品牌头部（所有模式共用）
    _render_brand()

    # 根据模式渲染不同视图
    if mode == AUTH_MODE_HOME:
        _render_home_options()
    elif mode == AUTH_MODE_REGISTER:
        _render_register_form(on_submit_callback)
    elif mode == AUTH_MODE_LOGIN:
        _render_login_form(on_submit_callback)
    elif mode == AUTH_MODE_GUEST:
        _render_guest_info(on_submit_callback)
    else:
        # 未知模式，回退到首页
        st.session_state["auth_mode"] = AUTH_MODE_HOME
        _render_home_options()


def show_auth_loading(message: str = "处理中..."):
    """设置认证加载状态。"""
    st.session_state["auth_loading"] = True
    st.session_state["auth_loading_message"] = message


def hide_auth_loading():
    """取消认证加载状态。"""
    st.session_state["auth_loading"] = False
    st.session_state.pop("auth_loading_message", None)


def set_auth_success(username: str, mode: str):
    """设置认证成功状态。

    对于已注册用户（login/register），自动从用户数据中恢复
    model_authed 持久化状态，实现付费模型密码缓存。

    Args:
        username: 用户名（游客模式为 "游客"）
        mode: "register" / "login" / "guest"
    """
    st.session_state["authenticated"] = True
    st.session_state["auth_username"] = username
    st.session_state["auth_mode_type"] = mode

    # 已注册用户：从持久化的用户数据中恢复 model_authed 状态
    if mode in ("login", "register"):
        user_data = st.session_state.get("user_data")
        if user_data and user_data.get("extra_data", {}).get("model_authed", False):
            st.session_state["model_authed"] = True

    hide_auth_loading()


def is_authenticated() -> bool:
    """检查用户是否已认证。"""
    return st.session_state.get("authenticated", False)


def get_auth_username() -> str:
    """获取当前认证用户名。"""
    return st.session_state.get("auth_username", "游客")


def get_auth_mode() -> str:
    """获取当前认证模式类型。"""
    return st.session_state.get("auth_mode_type", "guest")


def logout():
    """退出登录，重置认证状态。

    清除 session 中的 model_authed，但不清除用户数据（JSONBin）中的持久化状态，
    下次登录时仍可自动恢复付费模型认证。
    """
    for key in [
        "authenticated", "auth_username", "auth_mode_type", "auth_mode",
        "auth_loading", "auth_loading_message",
        "register_error", "register_success", "register_username",
        "login_error", "login_success",
        "user_data",
        "model_authed",  # 仅清除 session 缓存，持久化状态保留在 JSONBin 中
    ]:
        st.session_state.pop(key, None)


def show_auth_error(message: str):
    """显示认证错误信息。"""
    st.error(f"❌ {message}")


def show_auth_success(message: str):
    """显示认证成功信息。"""
    st.success(f"✅ {message}")


def show_auth_warning(message: str):
    """显示认证警告信息。"""
    st.warning(f"⚠️ {message}")


def reset_auth_mode():
    """重置认证模式到首页。"""
    st.session_state["auth_mode"] = AUTH_MODE_HOME
