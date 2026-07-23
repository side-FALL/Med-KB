"""医学教材知识库 — 设置页组件（用户信息增强 + 个人偏好）

职责：
- 用户信息卡片（用户名、显示名称、邮箱、角色、注册时间、最后登录时间）
- 个人偏好设置（主题切换、语言选择、字体大小、显示名称）
- 轻量级 i18n（中文 / English），供设置页与主标题使用
- 偏好读写：session_state 实时生效，登录用户持久化到 UserDataManager

设计注意（来自 lessons-learned）：
- Widget 全部使用 explicit key，避免隐式 key 在复杂布局中状态漂移
- 偏好通过 on_change 回调保存，按钮/滑块点击已触发 rerun，不做额外 st.rerun()
- 主题/字体的 CSS 注入在 app.py 脚本顶部完成（inject_user_preferences），
  保证 widget 修改后的下一次 run 立即生效
"""

from __future__ import annotations

import csv
import html
import io
import json
import logging
from datetime import datetime, timezone

import streamlit as st

logger = logging.getLogger(__name__)

# ── 偏好默认值 ─────────────────────────────────────────────

PREF_DEFAULTS = {
    "pref_theme": "light",
    "pref_language": "zh-CN",
    "pref_font_size": 14,
    "pref_display_name": "",
}

# ── i18n 字典 ──────────────────────────────────────────────

TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh-CN": {
        # 主标题
        "app_title": "医学教材知识库",
        "app_tagline": "AI 驱动的医学知识检索与学习平台",
        "stat_books": "本教材",
        "stat_chunks": "个知识点",
        "stat_free": "完全免费",
        "stat_online": "24h 在线",
        # 设置页
        "settings_title": "⚙️ 个人设置",
        # 用户信息卡片
        "display_name": "显示名称",
        "username_label": "用户名",
        "email_label": "邮箱",
        "email_not_set": "未绑定",
        "role_label": "角色",
        "role_admin": "管理员",
        "role_user": "普通用户",
        "registered_at": "注册时间",
        "last_login_at": "最后登录",
        "login_count": "登录次数",
        "query_count": "查询次数",
        "guest_title": "游客模式",
        "guest_desc": "数据不会保存 · 功能受限 · 偏好仅本次会话有效",
        "logout_btn": "🚪 退出登录",
        "login_btn": "📝 注册/登录",
        # 个人偏好
        "prefs_title": "🎨 个人偏好",
        "theme_label": "界面主题",
        "theme_light": "☀️ 亮色",
        "theme_dark": "🌙 暗色",
        "language_label": "界面语言",
        "font_size_label": "字体大小",
        "font_size_help": "调整页面正文字体大小（12-20px）",
        "display_name_help": "留空则显示用户名",
        "pref_saved": "✅ 偏好已保存",
        "pref_save_failed": "⚠️ 偏好保存失败，仅本次会话生效",
        # 检索设置
        "retrieval_title": "🔍 检索设置",
        "scope_label": "教材范围",
        "scope_all": "全部教材",
        "scope_select": "选择教材",
        "scope_select_hint": "已切换为「选择教材」模式，请在下方「教材管理」或主界面中选择具体教材",
        "scope_selected_summary": "已选 {n}/{total} 本 · {chunks:,} 个文本块",
        "model_label": "检索模型",
        "model_free_hint": "🆓 免费模型 · 请求过多会限制，如遇报错请切换其他模型",
        "model_paid_hint": "🔒 付费模型 · 需在主界面验证密码后使用",
        "top_k_label": "返回结果数",
        "top_k_help": "每次检索返回的相关文本块数量",
        "alpha_label": "向量权重 (α)",
        "alpha_help": "1.0=纯向量检索，0.0=纯关键词检索",
        "use_context_label": "启用多轮对话",
        "use_context_help": "开启后AI会参考之前对话的上下文",
        # 教材管理
        "tm_title": "📚 教材管理",
        "tm_summary": "已选 {n}/{total} 本 · {chunks:,} 个文本块 · 已收藏 {f} 本",
        "tm_selected_none": "未选择教材：请在主界面“教材范围”中挑选，或切换为“全部教材”。",
        "tm_search_label": "搜索教材",
        "tm_search_ph": "输入教材名称关键词…",
        "tm_subject_label": "学科筛选",
        "tm_subject_all": "全部学科",
        "tm_scope_label": "显示范围",
        "tm_scope_selected": "已选",
        "tm_scope_all": "全部",
        "tm_scope_fav": "收藏",
        "tm_chunks_unit": "块",
        "tm_selected_badge": "已选",
        "tm_empty": "没有符合条件的教材",
        "tm_fav_empty": "暂无收藏教材，点击教材前的 ☆ 即可收藏",
        "tm_fav_add": "收藏该教材",
        "tm_fav_remove": "取消收藏",
        # 搜索历史
        "history_title": "📜 搜索历史",
        "history_empty": "暂无搜索历史",
        "history_clear": "🗑️ 清空全部记录",
        "history_resend": "🔄 重新搜索",
        "history_delete": "删除该条",
        "history_export_csv": "📥 导出 CSV",
        "history_export_json": "📥 导出 JSON",
        "history_time": "搜索时间",
        "history_mode_col": "模式",
        "history_query": "查询内容",
        "history_model_col": "模型",
        "history_resend_hint": "已触发重新搜索，请切换到「{mode}」标签页查看结果",
        # 学习统计
        "ls_title": "📊 学习统计",
        "ls_empty": "暂无学习数据，开始使用后这里会展示统计图表",
        "ls_total": "总查询",
        "ls_today": "今日",
        "ls_modes": "使用模式",
        "ls_trend": "查询趋势",
        "ls_trend_help": "按天统计的查询次数变化",
        "ls_dist": "模式分布",
        "ls_dist_help": "各功能模式的使用占比",
        "ls_records": "学习记录",
        "ls_favorites": "收藏教材",
        # 数据管理
        "dm_title": "💾 数据管理",
        "dm_export_records": "导出学习记录",
        "dm_export_records_csv": "📥 学习记录 CSV",
        "dm_export_records_json": "📥 学习记录 JSON",
        "dm_export_fav": "导出收藏教材",
        "dm_export_fav_csv": "📥 收藏教材 CSV",
        "dm_export_fav_json": "📥 收藏教材 JSON",
        "dm_backup": "数据备份",
        "dm_backup_btn": "📤 导出全部数据",
        "dm_backup_help": "导出你的所有设置和数据为 JSON 文件，可用于备份或迁移",
        "dm_restore": "数据恢复",
        "dm_restore_help": "导入之前导出的备份文件，恢复偏好设置和收藏教材",
        "dm_restore_success": "✅ 数据恢复成功，偏好设置已更新",
        "dm_restore_fail": "⚠️ 数据恢复失败：{error}",
        "dm_restore_invalid": "⚠️ 文件格式不正确，请上传有效的备份文件",
        "dm_guest_hint": "游客模式数据仅本次会话有效，导出的备份文件可在登录后恢复",
        "dm_book_col": "教材名称",
        "dm_added_at_col": "收藏时间",
        # 统计
        "kb_stats_title": "📊 知识库统计",
        "kb_books": "教材数量",
        "kb_chunks": "文本块数",
        "kb_embed": "嵌入模型",
        # 关于
        "about_title": "ℹ️ 关于",
        "about_version": "**版本：**",
        "about_project": "**项目：**",
        "about_desc": "**说明：**",
        "about_desc_value": "AI 驱动的医学知识检索与学习平台",
    },
    "en-US": {
        # Header
        "app_title": "Medical Textbook Knowledge Base",
        "app_tagline": "AI-Powered Medical Knowledge Retrieval & Learning Platform",
        "stat_books": "textbooks",
        "stat_chunks": "knowledge chunks",
        "stat_free": "completely free",
        "stat_online": "24h online",
        # Settings page
        "settings_title": "⚙️ Settings",
        # User info card
        "display_name": "Display Name",
        "username_label": "Username",
        "email_label": "Email",
        "email_not_set": "Not linked",
        "role_label": "Role",
        "role_admin": "Admin",
        "role_user": "User",
        "registered_at": "Registered",
        "last_login_at": "Last Login",
        "login_count": "Logins",
        "query_count": "Queries",
        "guest_title": "Guest Mode",
        "guest_desc": "Data not saved · limited features · preferences last this session only",
        "logout_btn": "🚪 Log Out",
        "login_btn": "📝 Register / Log In",
        # Preferences
        "prefs_title": "🎨 Preferences",
        "theme_label": "Theme",
        "theme_light": "☀️ Light",
        "theme_dark": "🌙 Dark",
        "language_label": "Language",
        "font_size_label": "Font Size",
        "font_size_help": "Adjust body text size (12-20px)",
        "display_name_help": "Leave empty to use username",
        "pref_saved": "✅ Preferences saved",
        "pref_save_failed": "⚠️ Save failed; effective for this session only",
        # Retrieval
        "retrieval_title": "🔍 Retrieval Settings",
        "scope_label": "Textbook Scope",
        "scope_all": "All textbooks",
        "scope_select": "Select textbooks",
        "scope_select_hint": "Switched to \"Select textbooks\" mode - pick specific books in \"Textbook Management\" below or on the main page",
        "scope_selected_summary": "{n}/{total} books selected · {chunks:,} chunks",
        "model_label": "Retrieval Model",
        "model_free_hint": "🆓 Free model · rate-limited, switch models if errors occur",
        "model_paid_hint": "🔒 Paid model · password verification required on the main page",
        "top_k_label": "Results Count",
        "top_k_help": "Number of relevant text chunks returned per retrieval",
        "alpha_label": "Vector Weight (α)",
        "alpha_help": "1.0 = pure vector search, 0.0 = pure keyword search",
        "use_context_label": "Enable Multi-turn Context",
        "use_context_help": "When on, AI considers previous conversation context",
        # Textbook management
        "tm_title": "📚 Textbook Management",
        "tm_summary": "{n}/{total} books selected · {chunks:,} chunks · {f} favorited",
        "tm_selected_none": "No books selected: pick books via the scope selector on the main page, or switch to \"All textbooks\".",
        "tm_search_label": "Search textbooks",
        "tm_search_ph": "Type book name keywords…",
        "tm_subject_label": "Subject filter",
        "tm_subject_all": "All subjects",
        "tm_scope_label": "Show",
        "tm_scope_selected": "Selected",
        "tm_scope_all": "All",
        "tm_scope_fav": "Favorites",
        "tm_chunks_unit": "chunks",
        "tm_selected_badge": "Selected",
        "tm_empty": "No textbooks match the filters",
        "tm_fav_empty": "No favorites yet — click ☆ before a book to favorite it",
        "tm_fav_add": "Add to favorites",
        "tm_fav_remove": "Remove from favorites",
        # History
        "history_title": "📜 Search History",
        "history_empty": "No search history yet",
        "history_clear": "🗑️ Clear All Records",
        "history_resend": "🔄 Search Again",
        "history_delete": "Delete this entry",
        "history_export_csv": "📥 Export CSV",
        "history_export_json": "📥 Export JSON",
        "history_time": "Time",
        "history_mode_col": "Mode",
        "history_query": "Query",
        "history_model_col": "Model",
        "history_resend_hint": "Search triggered — switch to the \"{mode}\" tab to see results",
        # Learning stats
        "ls_title": "📊 Learning Statistics",
        "ls_empty": "No learning data yet. Charts will appear here after you start using the app.",
        "ls_total": "Total queries",
        "ls_today": "Today",
        "ls_modes": "Modes used",
        "ls_trend": "Query Trend",
        "ls_trend_help": "Daily query count over time",
        "ls_dist": "Mode Distribution",
        "ls_dist_help": "Usage breakdown by feature mode",
        "ls_records": "Learning Records",
        "ls_favorites": "Favorites",
        # Data management
        "dm_title": "💾 Data Management",
        "dm_export_records": "Export Learning Records",
        "dm_export_records_csv": "📥 Records CSV",
        "dm_export_records_json": "📥 Records JSON",
        "dm_export_fav": "Export Favorites",
        "dm_export_fav_csv": "📥 Favorites CSV",
        "dm_export_fav_json": "📥 Favorites JSON",
        "dm_backup": "Data Backup",
        "dm_backup_btn": "📤 Export All Data",
        "dm_backup_help": "Export all your settings and data as a JSON file for backup or migration",
        "dm_restore": "Data Restore",
        "dm_restore_help": "Import a previously exported backup file to restore preferences and favorites",
        "dm_restore_success": "✅ Data restored successfully, preferences updated",
        "dm_restore_fail": "⚠️ Data restore failed: {error}",
        "dm_restore_invalid": "⚠️ Invalid file format, please upload a valid backup file",
        "dm_guest_hint": "Guest data is session-only; exported backups can be restored after logging in",
        "dm_book_col": "Textbook Name",
        "dm_added_at_col": "Added",
        # Stats
        "kb_stats_title": "📊 Knowledge Base Stats",
        "kb_books": "Textbooks",
        "kb_chunks": "Chunks",
        "kb_embed": "Embedding Model",
        # About
        "about_title": "ℹ️ About",
        "about_version": "**Version:**",
        "about_project": "**Project:**",
        "about_desc": "**Description:**",
        "about_desc_value": "AI-powered medical knowledge retrieval & learning platform",
    },
}


def t(key: str) -> str:
    """按当前语言偏好返回翻译文本，缺失时回退中文，再回退 key 本身。"""
    lang = st.session_state.get("pref_language", "zh-CN")
    table = TRANSLATIONS.get(lang, TRANSLATIONS["zh-CN"])
    return table.get(key) or TRANSLATIONS["zh-CN"].get(key, key)


# ── 教材学科分类 ─────────────────────────────────────────
# manifest.json 无学科字段，按教材名称关键词规则归类（43 本全量核对）。
# 规则按优先级匹配，命中即返回；未命中默认临床医学（内外妇儿等占多数）。

SUBJECTS = ["基础医学", "临床医学", "诊断与影像", "药学", "预防与公卫", "人文与全科", "中医学"]

SUBJECT_I18N: dict[str, dict[str, str]] = {
    "en-US": {
        "基础医学": "Basic Sciences",
        "临床医学": "Clinical Medicine",
        "诊断与影像": "Diagnostics & Imaging",
        "药学": "Pharmacology",
        "预防与公卫": "Preventive & Public Health",
        "人文与全科": "Humanities & GP",
        "中医学": "Chinese Medicine",
    },
}

_SUBJECT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("中医学", ("中医",)),
    ("药学", ("药理",)),  # 药理学 / 临床药理学，须先于默认临床
    ("基础医学", ("生理", "生物", "病理", "微生物", "寄生虫", "免疫", "遗传", "物理", "组织学", "解剖", "法医")),
    ("诊断与影像", ("诊断", "影像")),
    ("预防与公卫", ("预防", "流行病学", "循证", "营养")),
    ("人文与全科", ("心理", "统计", "医患", "全科")),
)


def classify_subject(book_name: str) -> str:
    """按教材名称关键词归类学科，返回 canonical 中文学科名。"""
    for subject, keywords in _SUBJECT_RULES:
        if any(kw in book_name for kw in keywords):
            return subject
    return "临床医学"


def subject_label(subject: str) -> str:
    """学科名按当前语言偏好翻译（canonical 为中文）。"""
    lang = st.session_state.get("pref_language", "zh-CN")
    return SUBJECT_I18N.get(lang, {}).get(subject, subject)


# ── 偏好读写 ───────────────────────────────────────────────

def ensure_pref_defaults() -> None:
    """确保偏好相关 session_state 键存在（幂等）。"""
    for k, v in PREF_DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _clear_pref_widget_keys() -> None:
    """清除偏好 widget 的 session_state 键，使其从规范化偏好值重新初始化。"""
    for widget_key in (
        "pref_theme_radio", "pref_lang_radio",
        "pref_font_slider", "pref_display_name_input",
    ):
        st.session_state.pop(widget_key, None)


def load_preferences_from_user_data() -> None:
    """登录成功后，从 user_data.preferences 恢复偏好到 session_state。

    按用户名标记恢复状态（prefs_loaded_for），同一用户每会话只恢复一次，
    避免覆盖用户当次会话的修改；切换账号（含退出后换号登录）会重新恢复。
    游客模式恢复为默认值（游客无持久化数据）。
    """
    if st.session_state.get("auth_mode_type") == "guest":
        if st.session_state.get("prefs_loaded_for") != "guest":
            st.session_state["prefs_loaded_for"] = "guest"
            for k, v in PREF_DEFAULTS.items():
                st.session_state[k] = v
            st.session_state["favorites"] = []
            _clear_pref_widget_keys()
        return

    username = st.session_state.get("auth_username", "")
    if st.session_state.get("prefs_loaded_for") == username:
        return
    st.session_state["prefs_loaded_for"] = username
    # 清除可能残留的 widget 状态（如游客阶段渲染过设置页），
    # 使其在下一次渲染时从恢复后的偏好值重新初始化
    _clear_pref_widget_keys()

    user_data = st.session_state.get("user_data") or {}
    prefs = user_data.get("preferences") or {}
    if not prefs:
        return

    if prefs.get("theme") in ("light", "dark"):
        st.session_state["pref_theme"] = prefs["theme"]
    if prefs.get("language") in TRANSLATIONS:
        st.session_state["pref_language"] = prefs["language"]
    font_size = prefs.get("font_size")
    if isinstance(font_size, int) and 10 <= font_size <= 32:
        st.session_state["pref_font_size"] = font_size
    display_name = prefs.get("display_name", "")
    if isinstance(display_name, str):
        st.session_state["pref_display_name"] = display_name
    fav_books = prefs.get("favorite_books")
    if isinstance(fav_books, list):
        st.session_state["favorites"] = [b for b in fav_books if isinstance(b, str)]


def _persist_preferences() -> None:
    """on_change 回调：把 session_state 中的偏好持久化到用户数据。

    游客模式跳过持久化（session_state 已更新，本次会话内生效）。
    合并写入（保留 default_model 等其他偏好键），避免全量覆盖丢字段。
    """
    ensure_pref_defaults()

    prefs_patch = {
        "theme": st.session_state["pref_theme"],
        "language": st.session_state["pref_language"],
        "font_size": st.session_state["pref_font_size"],
        "display_name": st.session_state["pref_display_name"].strip(),
        "favorite_books": list(st.session_state.get("favorites") or []),
    }

    # 同步更新 session 内的 user_data 快照，保证本 run 后续读取一致
    user_data = st.session_state.get("user_data")
    if isinstance(user_data, dict):
        merged = dict(user_data.get("preferences") or {})
        merged.update(prefs_patch)
        user_data["preferences"] = merged

    if st.session_state.get("auth_mode_type") == "guest":
        return

    username = st.session_state.get("auth_username")
    if not username or username == "游客":
        return

    try:
        from auth_components import get_data_manager
        manager = get_data_manager()
        merged = dict((user_data or {}).get("preferences") or {})
        merged.update(prefs_patch)
        updated = manager.update_preferences(username, merged)
        st.session_state["user_data"] = updated
        st.session_state["pref_save_status"] = "ok"
    except Exception as exc:  # 存储失败不阻塞 UI，偏好仍在 session 中生效
        logger.warning("偏好持久化失败: %s", exc)
        st.session_state["pref_save_status"] = "error"


def _on_pref_change() -> None:
    """widget on_change 入口（回调中不可渲染 UI 元素）。"""
    _persist_preferences()


# ── 工具函数 ───────────────────────────────────────────────

def _fmt_dt(iso_str: str) -> str:
    """ISO 8601 时间格式化为可读字符串，非法输入回退原值或 N/A。"""
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        local = dt.astimezone(timezone.utc).astimezone()
        return local.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso_str)[:16]


def _esc(value) -> str:
    """HTML 转义，防止用户名/显示名注入。"""
    return html.escape(str(value)) if value else ""


# ── 用户信息卡片 ───────────────────────────────────────────

def render_user_info_section(username: str, auth_mode: str, admin: bool) -> None:
    """渲染用户信息区域（登录用户显示详细信息卡片，游客显示提示卡片）。

    包含退出登录 / 注册登录按钮。登录用户信息来自 session_state["user_data"]。
    """
    if auth_mode == "guest":
        st.markdown(f"""
        <div class="settings-user-card settings-guest-card">
            <div class="settings-user-head">
                <span class="settings-user-avatar">👤</span>
                <span class="settings-user-name guest">{t('guest_title')}</span>
            </div>
            <div class="settings-user-meta">{t('guest_desc')}</div>
        </div>
        """, unsafe_allow_html=True)

        if st.button(t("login_btn"), use_container_width=True, key="settings_login"):
            from auth_components import logout
            logout()
            st.rerun()
        return

    user_data = st.session_state.get("user_data", {}) or {}
    profile = user_data.get("profile", {}) or {}
    stats = user_data.get("stats", {}) or {}
    prefs = user_data.get("preferences", {}) or {}

    display_name = (
        st.session_state.get("pref_display_name", "").strip()
        or prefs.get("display_name", "").strip()
        or profile.get("username", username)
    )

    role = profile.get("role", "user")
    if role == "admin":
        role_badge = (
            '<span class="settings-role-badge admin">'
            + _esc(t("role_admin")) + "</span>"
        )
    else:
        role_badge = (
            '<span class="settings-role-badge">'
            + _esc(t("role_user")) + "</span>"
        )

    email = profile.get("email", "") or t("email_not_set")

    st.markdown(f"""
    <div class="settings-user-card">
        <div class="settings-user-head">
            <span class="settings-user-avatar">👤</span>
            <span class="settings-user-name">{_esc(display_name)}</span>
            {role_badge}
        </div>
        <div class="settings-user-grid">
            <div class="settings-user-field">
                <span class="settings-field-label">{t('username_label')}</span>
                <span class="settings-field-value">{_esc(profile.get('username', username))}</span>
            </div>
            <div class="settings-user-field">
                <span class="settings-field-label">{t('email_label')}</span>
                <span class="settings-field-value">{_esc(email)}</span>
            </div>
            <div class="settings-user-field">
                <span class="settings-field-label">{t('registered_at')}</span>
                <span class="settings-field-value">{_esc(_fmt_dt(profile.get('created_at', '')))}</span>
            </div>
            <div class="settings-user-field">
                <span class="settings-field-label">{t('last_login_at')}</span>
                <span class="settings-field-value">{_esc(_fmt_dt(profile.get('last_active_at', '')))}</span>
            </div>
        </div>
        <div class="settings-user-meta">
            <span>{t('login_count')}: {int(stats.get('login_count', 0))}</span>
            <span>{t('query_count')}: {int(stats.get('total_queries', 0))}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button(t("logout_btn"), use_container_width=True, key="settings_logout"):
        from auth_components import logout
        logout()
        st.rerun()


# ── 个人偏好设置 ───────────────────────────────────────────

def render_preferences_section() -> None:
    """渲染个人偏好设置（主题、语言、字体大小、显示名称）。

    所有 widget 使用 explicit key + on_change 回调持久化，
    修改后下一次 run 由 app.py 顶部的 inject_user_preferences 应用样式。
    """
    ensure_pref_defaults()
    # 规范化值 → widget key 初始化（仅在 widget key 不存在时同步，
    # 避免同时传 index/value 与 session_state 已有 widget 值产生警告）
    _widget_key_map = {
        "pref_theme_radio": "pref_theme",
        "pref_lang_radio": "pref_language",
        "pref_font_slider": "pref_font_size",
        "pref_display_name_input": "pref_display_name",
    }
    for widget_key, pref_key in _widget_key_map.items():
        if widget_key not in st.session_state:
            st.session_state[widget_key] = st.session_state[pref_key]

    st.markdown(f"#### {t('prefs_title')}")

    # 主题切换
    theme_labels = {"light": t("theme_light"), "dark": t("theme_dark")}
    st.radio(
        t("theme_label"),
        options=["light", "dark"],
        format_func=lambda v: theme_labels[v],
        horizontal=True,
        key="pref_theme_radio",
        on_change=_on_theme_change,
    )

    # 语言选择
    lang_labels = {"zh-CN": "中文", "en-US": "English"}
    st.radio(
        t("language_label"),
        options=["zh-CN", "en-US"],
        format_func=lambda v: lang_labels[v],
        horizontal=True,
        key="pref_lang_radio",
        on_change=_on_lang_change,
    )

    # 字体大小
    st.slider(
        t("font_size_label"),
        min_value=12,
        max_value=20,
        help=t("font_size_help"),
        key="pref_font_slider",
        on_change=_on_font_change,
    )

    # 显示名称（游客不显示，因无持久化意义且卡片使用用户名兜底）
    if st.session_state.get("auth_mode_type") != "guest":
        st.text_input(
            t("display_name"),
            max_chars=30,
            help=t("display_name_help"),
            key="pref_display_name_input",
            on_change=_on_display_name_change,
        )

    # 保存状态反馈（由 on_change 回调写入，本 run 渲染一次后清除）
    status = st.session_state.pop("pref_save_status", None)
    if status == "ok":
        st.caption(t("pref_saved"))
    elif status == "error":
        st.caption(t("pref_save_failed"))


# on_change 回调：先同步 widget 值到规范键，再持久化
def _on_theme_change() -> None:
    st.session_state["pref_theme"] = st.session_state["pref_theme_radio"]
    _persist_preferences()


def _on_lang_change() -> None:
    st.session_state["pref_language"] = st.session_state["pref_lang_radio"]
    _persist_preferences()


def _on_font_change() -> None:
    st.session_state["pref_font_size"] = int(st.session_state["pref_font_slider"])
    _persist_preferences()


def _on_display_name_change() -> None:
    st.session_state["pref_display_name"] = st.session_state["pref_display_name_input"]
    _persist_preferences()


# ── 检索设置（教材范围 + 检索模型 + 检索参数）──────────────

def _on_settings_scope_change() -> None:
    """设置页教材范围变更回调：同步到主界面 canonical key。"""
    st.session_state["scope_selector"] = st.session_state["settings_scope_sel"]


def _on_settings_model_change() -> None:
    """设置页检索模型变更回调：同步到主界面 canonical key。"""
    st.session_state["model_selector"] = st.session_state["settings_model_sel"]


def render_retrieval_settings_section(book_count: int, book_stats: dict, ALL_BOOKS: list[str]) -> None:
    """渲染检索设置：教材范围、检索模型、返回结果数、向量权重、多轮对话。

    教材范围和检索模型与主界面双向同步：
    - 设置页 widget（settings_scope_sel / settings_model_sel）通过 on_change 回调
      写入主界面的 canonical key（scope_selector / model_selector）
    - 主界面 widget 的 on_change 回调反向同步到设置页 widget key
    - session_state 共享保证两端始终一致

    Widget 全部使用 explicit key，与现有模式一致。
    """
    from config import MODELS, is_model_free

    st.markdown(f"#### {t('retrieval_title')}")

    # 初始化 settings widget key（从 canonical state 恢复，仅首次）
    if "settings_scope_sel" not in st.session_state:
        st.session_state["settings_scope_sel"] = st.session_state.get(
            "scope_selector", "全部教材"
        )
    if "settings_model_sel" not in st.session_state:
        st.session_state["settings_model_sel"] = st.session_state.get(
            "model_selector", list(MODELS.keys())[0]
        )

    # 教材范围 + 检索模型
    col_scope, col_model = st.columns([3, 2])
    with col_scope:
        scope_labels = {"全部教材": t("scope_all"), "选择教材": t("scope_select")}
        st.selectbox(
            t("scope_label"),
            ["全部教材", "选择教材"],
            format_func=lambda v: scope_labels[v],
            key="settings_scope_sel",
            on_change=_on_settings_scope_change,
        )
    with col_model:
        model_keys = list(MODELS.keys())
        st.selectbox(
            t("model_label"),
            model_keys,
            format_func=lambda x: f"{MODELS[x]['name']} {'🆓' if is_model_free(x) else '🔒'}",
            key="settings_model_sel",
            on_change=_on_settings_model_change,
        )

    # 模型信息提示
    _sel_model = st.session_state.get("settings_model_sel", model_keys[0])
    if is_model_free(_sel_model):
        st.caption(t("model_free_hint"))
    else:
        st.caption(t("model_paid_hint"))

    # 选择教材模式：显示已选状态或提示
    if st.session_state.get("settings_scope_sel") == "选择教材":
        selected = [
            b for b in (st.session_state.get("selected_books") or [])
            if b in book_stats
        ]
        if selected:
            n = sum(book_stats.get(b, 0) for b in selected)
            st.caption(
                t("scope_selected_summary").format(
                    n=len(selected), total=book_count, chunks=n
                )
            )
        else:
            st.warning(t("scope_select_hint"))

    # 检索参数（返回结果数 / 向量权重 / 多轮对话）
    st.slider(
        t("top_k_label"), 3, 15, st.session_state.get("top_k", 10),
        help=t("top_k_help"), key="top_k_slider",
    )
    st.slider(
        t("alpha_label"), 0.0, 1.0, st.session_state.get("alpha", 0.7),
        help=t("alpha_help"), key="alpha_slider",
    )
    st.checkbox(
        t("use_context_label"),
        value=st.session_state.get("use_context", True),
        help=t("use_context_help"), key="use_context_cb",
    )

    # 同步到 canonical session_state（供脚本顶部和各模式读取）
    st.session_state["top_k"] = st.session_state["top_k_slider"]
    st.session_state["alpha"] = st.session_state["alpha_slider"]
    st.session_state["use_context"] = st.session_state["use_context_cb"]


# ── 教材管理 ─────────────────────────────────────────────

def _get_favorites() -> list:
    """读取收藏教材列表（容忍异常类型，始终返回 list）。"""
    favs = st.session_state.get("favorites")
    return favs if isinstance(favs, list) else []


def _toggle_favorite(book: str) -> None:
    """收藏/取消收藏回调：更新 session_state 并随偏好持久化（登录用户）。

    按钮点击本身已触发 rerun，回调内不做额外 st.rerun()。
    """
    favs = _get_favorites()
    if book in favs:
        favs.remove(book)
    else:
        favs.append(book)
    st.session_state["favorites"] = favs
    _persist_preferences()


def render_textbook_management_section(book_stats: dict, ALL_BOOKS: list[str]) -> None:
    """渲染教材管理：已选教材列表、收藏、关键词搜索与学科筛选。

    - 已选集合与主界面"教材范围"联动（全部教材 / selected_books）
    - 收藏存 session_state["favorites"]，登录用户随偏好持久化到用户数据
    - 单一列表 + 三种显示范围（已选/全部/收藏），避免与主界面选择器重复
    """
    favorites = _get_favorites()
    scope_mode = st.session_state.get("scope_selector", "全部教材")
    if scope_mode == "选择教材":
        selected = [b for b in st.session_state.get("selected_books") or [] if b in book_stats]
    else:
        selected = list(ALL_BOOKS)

    selected_set = set(selected)
    fav_set = set(favorites)
    sel_chunks = sum(book_stats.get(b, 0) for b in selected)
    valid_fav_count = sum(1 for b in favorites if b in book_stats)

    st.markdown(f"#### {t('tm_title')}")
    st.caption(t("tm_summary").format(
        n=len(selected), total=len(ALL_BOOKS), chunks=sel_chunks, f=valid_fav_count
    ))
    if scope_mode == "选择教材" and not selected:
        st.warning(t("tm_selected_none"))

    # 筛选行：关键词 + 学科 + 显示范围
    f1, f2 = st.columns([3, 2])
    with f1:
        keyword = st.text_input(
            t("tm_search_label"),
            placeholder=t("tm_search_ph"),
            key="tm_search",
        )
    with f2:
        subject_sel = st.selectbox(
            t("tm_subject_label"),
            ["__all__"] + SUBJECTS,
            format_func=lambda s: t("tm_subject_all") if s == "__all__" else subject_label(s),
            key="tm_subject",
        )
    view_labels = {
        "selected": t("tm_scope_selected"),
        "all": t("tm_scope_all"),
        "fav": t("tm_scope_fav"),
    }
    view = st.radio(
        t("tm_scope_label"),
        ["selected", "all", "fav"],
        format_func=lambda v: view_labels[v],
        horizontal=True,
        key="tm_view",
    )

    # 按显示范围取基础集合，再叠加关键词与学科筛选
    if view == "fav":
        base = [b for b in ALL_BOOKS if b in fav_set]
    elif view == "selected":
        base = selected
    else:
        base = list(ALL_BOOKS)

    kw = (keyword or "").strip().lower()
    shown = [
        b for b in base
        if (not kw or kw in b.lower())
        and (subject_sel == "__all__" or classify_subject(b) == subject_sel)
    ]

    if not shown:
        if view == "fav" and not kw and subject_sel == "__all__":
            st.info(t("tm_fav_empty"))
        else:
            st.info(t("tm_empty"))
        return

    for b in shown:
        idx = ALL_BOOKS.index(b)
        c_star, c_name, c_subj, c_chunks, c_badge = st.columns([0.6, 5, 2.2, 1.6, 1.4])
        with c_star:
            starred = b in fav_set
            st.button(
                "⭐" if starred else "☆",
                key=f"tm_fav_{idx}",
                help=t("tm_fav_remove") if starred else t("tm_fav_add"),
                on_click=_toggle_favorite,
                args=(b,),
            )
        with c_name:
            st.markdown(f"<span class='tm-book-name'>{_esc(b)}</span>", unsafe_allow_html=True)
        with c_subj:
            st.markdown(
                f"<span class='tm-subject-badge'>{_esc(subject_label(classify_subject(b)))}</span>",
                unsafe_allow_html=True,
            )
        with c_chunks:
            st.markdown(
                f"<span class='tm-chunks'>{book_stats.get(b, 0):,} {t('tm_chunks_unit')}</span>",
                unsafe_allow_html=True,
            )
        with c_badge:
            if b in selected_set:
                st.markdown(
                    f"<span class='tm-selected-badge'>{t('tm_selected_badge')}</span>",
                    unsafe_allow_html=True,
                )


# ── 搜索历史 ─────────────────────────────────────────────

_MODE_ICONS = {"问答": "💬", "刷题": "📝", "对比": "🔄", "病例": "🏥", "智能体": "🤖"}


def consume_pending_search(mode: str) -> str | None:
    """检查并消费 pending_search（匹配模式则返回查询内容并清除标志）。

    在各模式 render 开头调用。不匹配的 pending_search 会被放回 session_state，
    供后续模式检测。由于 Streamlit tabs 中所有内容都会执行，
    遍历顺序（qa→…→agent）保证了匹配的模式能正确消费。
    """
    pending = st.session_state.pop("pending_search", None)
    if pending and pending.get("mode") == mode:
        return pending.get("query", "")
    # 不匹配则放回，供后续模式检测
    if pending:
        st.session_state["pending_search"] = pending
    return None


def _delete_history_item(orig_idx: int) -> None:
    """删除单条历史记录（on_click 回调，按钮点击已触发 rerun，无需额外操作）。"""
    hist = st.session_state.get("hist") or []
    if 0 <= orig_idx < len(hist):
        hist.pop(orig_idx)
        st.session_state.hist = hist


def _resend_search(orig_idx: int) -> None:
    """设置待搜索标志（on_click 回调），各模式 render 时检测并触发搜索。"""
    hist = st.session_state.get("hist") or []
    if 0 <= orig_idx < len(hist):
        item = hist[orig_idx]
        st.session_state["pending_search"] = {
            "query": item.get("q", ""),
            "mode": item.get("mode", "问答"),
        }


def _build_history_csv(hist: list) -> str:
    """将搜索历史构建为 CSV 字符串（UTF-8 BOM 头确保 Excel 正确显示中文）。"""
    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow([
        t("history_time"), t("history_mode_col"),
        t("history_query"), t("history_model_col"),
    ])
    for item in hist:
        writer.writerow([
            item.get("time", ""),
            item.get("mode", ""),
            item.get("q", ""),
            item.get("model", ""),
        ])
    return output.getvalue()


def render_search_history_section() -> None:
    """渲染搜索历史增强模块。

    - 历史记录按时间倒序排列
    - 每条显示：搜索时间、查询内容、模式
    - 点击「🔄 重新搜索」可自动填入对应模式并触发搜索
    - 每条有删除按钮，点击后该条记录立即移除
    - 支持导出 CSV / JSON 格式文件
    """
    st.markdown(f"#### {t('history_title')}")

    hist = st.session_state.get("hist") or []

    if not hist:
        st.info(t("history_empty"))
        return

    # 工具栏：导出 CSV / 导出 JSON / 清空全部
    col1, col2, col3 = st.columns(3)
    with col1:
        st.download_button(
            t("history_export_csv"),
            data=_build_history_csv(hist),
            file_name="search_history.csv",
            mime="text/csv",
            key="hist_export_csv",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            t("history_export_json"),
            data=json.dumps(hist, ensure_ascii=False, indent=2),
            file_name="search_history.json",
            mime="application/json",
            key="hist_export_json",
            use_container_width=True,
        )
    with col3:
        if st.button(t("history_clear"), key="settings_clear_hist",
                      use_container_width=True):
            st.session_state.hist = []
            st.session_state.conversation_turns = []
            st.session_state.favorites = []
            st.rerun()

    # 按时间倒序显示历史记录（保留原始索引用于回调）
    indexed_hist = list(enumerate(hist))
    sorted_hist = sorted(
        indexed_hist,
        key=lambda x: x[1].get("time", ""),
        reverse=True,
    )

    for orig_idx, item in sorted_hist:
        mode = item.get("mode", "问答")
        mode_icon = _MODE_ICONS.get(mode, "💬")
        time_str = item.get("time", "")
        query = item.get("q", "")

        col_time, col_mode, col_query, col_act = st.columns([2, 1.5, 5, 1.5])
        with col_time:
            st.caption(time_str)
        with col_mode:
            st.caption(f"{mode_icon} {mode}")
        with col_query:
            st.markdown(
                f"<span class='hist-query'>{_esc(query[:80])}</span>",
                unsafe_allow_html=True,
            )
        with col_act:
            c_resend, c_del = st.columns(2)
            with c_resend:
                st.button(
                    "🔄", key=f"hist_resend_{orig_idx}",
                    help=t("history_resend"),
                    on_click=_resend_search, args=(orig_idx,),
                )
            with c_del:
                st.button(
                    "🗑️", key=f"hist_del_{orig_idx}",
                    help=t("history_delete"),
                    on_click=_delete_history_item, args=(orig_idx,),
                )

    # 重新搜索提示（pending_search 未被对应模式消费时显示并清除，防重复触发）
    pending = st.session_state.pop("pending_search", None)
    if pending:
        pending_mode = pending.get("mode", "问答")
        st.info(t("history_resend_hint").format(mode=pending_mode))


# ── 学习统计 ─────────────────────────────────────────────

def _parse_hist_date(time_str: str) -> str | None:
    """从 hist 的 time 字段提取日期（YYYY-MM-DD），解析失败返回 None。

    hist 中 time 格式为 "2026-07-23 15:30:00"（来自各 mode 的 datetime.now().strftime）。
    """
    if not time_str:
        return None
    try:
        # hist time 格式："YYYY-MM-DD HH:MM:SS"
        return datetime.strptime(time_str[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def render_learning_stats_section() -> None:
    """渲染学习统计模块：查询趋势折线图 + 模式分布柱状图。

    数据来源：session_state["hist"]（各模式查询时追加，含 time/mode/q 字段）。
    图表使用 Streamlit 原生 st.line_chart / st.bar_chart（底层 pandas），
    不引入 plotly 等额外重依赖。

    空数据时显示提示信息，不渲染图表。
    """
    import pandas as pd

    st.markdown(f"#### {t('ls_title')}")

    hist = st.session_state.get("hist") or []
    if not hist:
        st.info(t("ls_empty"))
        return

    # 概览指标
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_count = sum(1 for h in hist if _parse_hist_date(h.get("time", "")) == today_str)
    used_modes = len({h.get("mode", "") for h in hist})
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(t("ls_total"), len(hist))
    with c2:
        st.metric(t("ls_today"), today_count)
    with c3:
        st.metric(t("ls_modes"), used_modes)

    # 查询趋势折线图：按天聚合查询次数
    dates: list[str] = []
    for h in hist:
        d = _parse_hist_date(h.get("time", ""))
        if d:
            dates.append(d)
    if dates:
        df_trend = pd.DataFrame({"date": dates})
        df_trend = df_trend.groupby("date").size().reset_index(name="count")
        df_trend = df_trend.sort_values("date")
        # 补齐缺失日期（避免折线图断点），仅在数据跨度内
        if len(df_trend) > 1:
            full_range = pd.date_range(
                start=df_trend["date"].iloc[0],
                end=df_trend["date"].iloc[-1],
                freq="D",
            ).strftime("%Y-%m-%d")
            df_trend = df_trend.set_index("date").reindex(full_range, fill_value=0).reset_index()
            df_trend.columns = ["date", "count"]
        df_trend_chart = df_trend.set_index("date")
        st.caption(f"**{t('ls_trend')}** — {t('ls_trend_help')}")
        st.line_chart(df_trend_chart, use_container_width=True)

    # 模式分布柱状图：各 mode 使用次数
    mode_list = [h.get("mode", t("ls_modes")) for h in hist]
    if mode_list:
        df_dist = pd.Series(mode_list).value_counts().reset_index()
        df_dist.columns = ["mode", "count"]
        df_dist_chart = df_dist.set_index("mode")
        st.caption(f"**{t('ls_dist')}** — {t('ls_dist_help')}")
        st.bar_chart(df_dist_chart, use_container_width=True)


# ── 数据管理 ─────────────────────────────────────────────

def _build_favorites_csv(favorites: list, book_stats: dict | None = None) -> str:
    """将收藏教材列表构建为 CSV 字符串（UTF-8 BOM 头确保 Excel 正确显示中文）。"""
    output = io.StringIO()
    output.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow([t("dm_book_col"), t("tm_chunks_unit")])
    for book in favorites:
        chunks = (book_stats or {}).get(book, 0)
        writer.writerow([book, chunks])
    return output.getvalue()


def _build_backup_json() -> str:
    """构建全量用户数据备份 JSON。

    包含：偏好设置、收藏教材、搜索历史。
    登录用户额外包含 user_data 快照中的 profile / stats / learning_records。
    """
    backup = {
        "_type": "med-kb-backup",
        "_version": "1.0",
        "_exported_at": datetime.now(timezone.utc).isoformat(),
        "preferences": {
            "theme": st.session_state.get("pref_theme", "light"),
            "language": st.session_state.get("pref_language", "zh-CN"),
            "font_size": st.session_state.get("pref_font_size", 14),
            "display_name": st.session_state.get("pref_display_name", ""),
        },
        "favorites": list(st.session_state.get("favorites") or []),
        "search_history": list(st.session_state.get("hist") or []),
    }

    # 登录用户：附加服务端存储的 user_data
    user_data = st.session_state.get("user_data")
    if isinstance(user_data, dict) and st.session_state.get("auth_mode_type") != "guest":
        backup["user_data"] = {
            "profile": user_data.get("profile", {}),
            "stats": user_data.get("stats", {}),
            "learning_records": user_data.get("learning_records", []),
            "preferences": user_data.get("preferences", {}),
        }

    return json.dumps(backup, ensure_ascii=False, indent=2)


def _restore_from_backup(backup: dict) -> bool:
    """从备份字典恢复偏好、收藏和历史到 session_state。

    仅恢复客户端可安全写入的字段（偏好、收藏、搜索历史）。
    登录用户额外持久化偏好到服务端（通过 _persist_preferences）。
    不恢复 profile / stats / password（安全考虑）。

    Returns:
        True 表示恢复成功
    """
    # 偏好恢复
    prefs = backup.get("preferences") or {}
    if isinstance(prefs, dict):
        if prefs.get("theme") in ("light", "dark"):
            st.session_state["pref_theme"] = prefs["theme"]
        if prefs.get("language") in TRANSLATIONS:
            st.session_state["pref_language"] = prefs["language"]
        fs = prefs.get("font_size")
        if isinstance(fs, int) and 12 <= fs <= 20:
            st.session_state["pref_font_size"] = fs
        dn = prefs.get("display_name")
        if isinstance(dn, str):
            st.session_state["pref_display_name"] = dn

    # 收藏教材恢复
    favs = backup.get("favorites")
    if isinstance(favs, list):
        st.session_state["favorites"] = [f for f in favs if isinstance(f, str)]

    # 搜索历史恢复
    hist = backup.get("search_history")
    if isinstance(hist, list):
        st.session_state["hist"] = hist

    # 清除偏好 widget key，使其在下次渲染时从恢复值重新初始化
    _clear_pref_widget_keys()

    # 登录用户：持久化偏好到服务端
    if st.session_state.get("auth_mode_type") != "guest":
        try:
            _persist_preferences()
        except Exception as exc:
            logger.warning("恢复后偏好持久化失败: %s", exc)

    return True


def render_data_management_section(book_stats: dict) -> None:
    """渲染数据管理模块：导出学习记录、导出收藏、数据备份与恢复。

    - 导出学习记录：复用搜索历史的 CSV/JSON 构建（含 time/mode/q/model）
    - 导出收藏教材：CSV（教材名+块数）/ JSON
    - 数据备份：导出全量用户数据为 JSON
    - 数据恢复：上传备份 JSON 文件恢复偏好和收藏
    """
    st.markdown(f"#### {t('dm_title')}")

    is_guest = st.session_state.get("auth_mode_type") == "guest"
    if is_guest:
        st.info(t("dm_guest_hint"))

    hist = st.session_state.get("hist") or []
    favorites = _get_favorites()

    # ── 导出学习记录 ──
    st.caption(f"**{t('dm_export_records')}** ({len(hist)} {t('ls_records').lower() if hist else ''})")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            t("dm_export_records_csv"),
            data=_build_history_csv(hist),
            file_name="learning_records.csv",
            mime="text/csv",
            key="dm_export_records_csv",
            use_container_width=True,
            disabled=not hist,
        )
    with c2:
        st.download_button(
            t("dm_export_records_json"),
            data=json.dumps(hist, ensure_ascii=False, indent=2),
            file_name="learning_records.json",
            mime="application/json",
            key="dm_export_records_json",
            use_container_width=True,
            disabled=not hist,
        )

    # ── 导出收藏教材 ──
    st.caption(f"**{t('dm_export_fav')}** ({len(favorites)} {t('ls_favorites').lower() if favorites else ''})")
    c3, c4 = st.columns(2)
    with c3:
        st.download_button(
            t("dm_export_fav_csv"),
            data=_build_favorites_csv(favorites, book_stats),
            file_name="favorites.csv",
            mime="text/csv",
            key="dm_export_fav_csv",
            use_container_width=True,
            disabled=not favorites,
        )
    with c4:
        st.download_button(
            t("dm_export_fav_json"),
            data=json.dumps(favorites, ensure_ascii=False, indent=2),
            file_name="favorites.json",
            mime="application/json",
            key="dm_export_fav_json",
            use_container_width=True,
            disabled=not favorites,
        )

    st.divider()

    # ── 数据备份 ──
    st.caption(f"**{t('dm_backup')}** — {t('dm_backup_help')}")
    st.download_button(
        t("dm_backup_btn"),
        data=_build_backup_json(),
        file_name=f"med-kb-backup-{datetime.now().strftime('%Y%m%d')}.json",
        mime="application/json",
        key="dm_backup_download",
        use_container_width=True,
    )

    # ── 数据恢复 ──
    st.caption(f"**{t('dm_restore')}** — {t('dm_restore_help')}")
    uploaded = st.file_uploader(
        t("dm_restore"),
        type=["json"],
        key="dm_restore_uploader",
        label_visibility="collapsed",
    )
    if uploaded is not None:
        try:
            raw = uploaded.read()
            backup = json.loads(raw)
            if not isinstance(backup, dict) or backup.get("_type") != "med-kb-backup":
                st.error(t("dm_restore_invalid"))
            else:
                _restore_from_backup(backup)
                st.success(t("dm_restore_success"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            st.error(t("dm_restore_fail").format(error=str(exc)))
        except Exception as exc:
            st.error(t("dm_restore_fail").format(error=str(exc)))
