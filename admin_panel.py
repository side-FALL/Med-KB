"""医学教材知识库 - 管理面板

管理员专属面板，提供用户管理和系统统计功能。
仅对 admin 角色用户可见，普通用户和游客无法访问。

功能：
- 查看所有已注册用户列表（用户名、角色、注册时间等）
- 查看系统统计（总用户数、总查询次数）
- 用户管理增强：角色变更、删除用户、重置密码、禁用/启用
- 搜索、筛选、排序、分页

数据来源：通过 UserDataManager 的现有方法获取，不直接操作数据库。
"""

from datetime import datetime
from typing import Optional

import streamlit as st


# ── 工具函数 ─────────────────────────────────────────────

def _format_datetime(iso_str: str) -> str:
    """将 ISO 8601 时间字符串格式化为易读的本地时间显示。

    Args:
        iso_str: ISO 8601 格式的时间字符串（如 "2024-01-15T10:30:00Z"）

    Returns:
        格式化后的时间字符串（如 "2024-01-15 10:30"），空值返回 "-"
    """
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        # 转为本地时区显示
        local_dt = dt.astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


def _safe_str(value, default="") -> str:
    """安全提取字符串值，处理 mock 测试场景返回非字符串的情况。"""
    if isinstance(value, str):
        return value
    return default


# ── 渲染函数 ─────────────────────────────────────────────

def _render_admin_header():
    """渲染管理面板标题区域。"""
    st.markdown("""
    <div style="background:linear-gradient(135deg, rgba(13,110,253,0.08) 0%, rgba(10,88,202,0.04) 100%);
                border-radius:12px; padding:1.2rem 1.5rem; margin-bottom:1.5rem;
                border:1px solid rgba(13,110,253,0.15);">
        <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.3rem;">
            <span style="font-size:1.8rem;">🔧</span>
            <h1 style="font-weight:800; color:#0A58CA; font-size:1.5rem; margin:0;">
                管理面板
            </h1>
        </div>
        <div style="font-size:0.85rem; color:#6C757D;">
            系统管理 · 用户概览 · 统计数据
        </div>
    </div>
    """, unsafe_allow_html=True)


def _render_system_stats(manager):
    """渲染系统统计区域。

    Args:
        manager: UserDataManager 实例
    """
    st.subheader("📊 系统统计")

    try:
        stats = manager.get_system_stats()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("总用户数", stats["total_users"])
        with col2:
            st.metric("总查询次数", stats["total_queries"])
        with col3:
            st.metric("管理员数量", stats["admin_count"])

    except Exception as exc:
        st.error(f"加载统计数据失败: {exc}")


def _parse_sort_choice(sort_choice: str) -> tuple:
    """将排序选项字符串解析为 (字段名, 是否降序) 元组。"""
    sort_map = {
        "注册时间 (新→旧)": ("created_at", True),
        "注册时间 (旧→新)": ("created_at", False),
        "最后活跃 (新→旧)": ("last_active_at", True),
        "最后活跃 (旧→新)": ("last_active_at", False),
    }
    return sort_map.get(sort_choice, ("created_at", True))


def _filter_and_sort_users(
    users: list, search_query: str, role_filter: str, sort_choice: str,
) -> list:
    """对用户列表执行搜索筛选和排序。

    Args:
        users: 原始用户列表
        search_query: 搜索关键词（按用户名模糊匹配）
        role_filter: 角色筛选（"全部" 或具体角色）
        sort_choice: 排序选项字符串

    Returns:
        筛选排序后的用户列表
    """
    filtered = list(users)

    # 搜索筛选
    if search_query:
        query_lower = search_query.lower()
        filtered = [u for u in filtered if query_lower in u["username"].lower()]

    # 角色筛选
    if role_filter and role_filter != "全部":
        filtered = [u for u in filtered if u["role"] == role_filter]

    # 排序
    sort_field, reverse = _parse_sort_choice(sort_choice)
    filtered.sort(key=lambda u: u.get(sort_field, "") or "", reverse=reverse)

    return filtered


def _render_role_change_dialog(manager, target_username: str, current_role: str,
                                current_admin: str):
    """渲染修改角色确认弹窗。

    Args:
        manager: UserDataManager 实例
        target_username: 目标用户名
        current_role: 当前角色
        current_admin: 当前管理员用户名
    """
    st.warning(f"⚠️ 确认修改用户 **{target_username}** 的角色（当前: {current_role}）")

    # 角色不可变性守卫说明
    st.info("ℹ️ 安全限制：不允许通过管理面板将用户提升为管理员（admin）")

    available_roles = ["user", "guest"]
    new_role = st.selectbox(
        "选择新角色", available_roles,
        key=f"new_role_{target_username}",
        help="仅可选择 user 或 guest，不可提升为 admin",
    )
    new_role = _safe_str(new_role, "user")

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button("✅ 确认变更", key=f"confirm_role_{target_username}",
                      type="primary", use_container_width=True):
            try:
                manager.change_role(target_username, new_role, operator=current_admin)
                st.success(f"✅ 用户 {target_username} 的角色已变更为 {new_role}")
                st.session_state.pop("admin_pending_action", None)
                st.rerun()
            except Exception as exc:
                st.error(f"❌ 角色变更失败: {exc}")
    with col_cancel:
        if st.button("取消", key=f"cancel_role_{target_username}",
                      use_container_width=True):
            st.session_state.pop("admin_pending_action", None)
            st.rerun()


def _render_delete_dialog(manager, target_username: str, current_admin: str):
    """渲染删除用户确认弹窗（需输入用户名二次确认）。

    Args:
        manager: UserDataManager 实例
        target_username: 目标用户名
        current_admin: 当前管理员用户名
    """
    st.error(f"🚨 危险操作：删除用户 **{target_username}**")
    st.markdown("**此操作不可逆！** 用户的全部数据（学习记录、偏好设置等）将被永久删除。")
    st.markdown(f"请输入用户名 **{target_username}** 以确认删除：")

    confirm_input = st.text_input(
        "输入用户名确认", placeholder=target_username,
        key=f"delete_confirm_{target_username}",
    )
    confirm_input = _safe_str(confirm_input)

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        can_delete = confirm_input == target_username
        if st.button("🗑️ 确认删除", key=f"confirm_delete_{target_username}",
                      type="primary", use_container_width=True,
                      disabled=not can_delete):
            try:
                manager.delete_user(target_username)
                st.success(f"✅ 用户 {target_username} 已被删除")
                st.session_state.pop("admin_pending_action", None)
                st.rerun()
            except Exception as exc:
                st.error(f"❌ 删除失败: {exc}")
    with col_cancel:
        if st.button("取消", key=f"cancel_delete_{target_username}",
                      use_container_width=True):
            st.session_state.pop("admin_pending_action", None)
            st.rerun()


def _render_reset_password_dialog(manager, target_username: str, current_admin: str):
    """渲染重置密码弹窗。

    Args:
        manager: UserDataManager 实例
        target_username: 目标用户名
        current_admin: 当前管理员用户名
    """
    st.warning(f"⚠️ 重置用户 **{target_username}** 的密码")

    new_password = st.text_input(
        "新临时密码", type="password",
        placeholder="至少8位，包含大小写字母/数字中的两种",
        key=f"new_pwd_{target_username}",
    )
    new_password = _safe_str(new_password)
    confirm_password = st.text_input(
        "确认新密码", type="password",
        key=f"confirm_pwd_{target_username}",
    )
    confirm_password = _safe_str(confirm_password)

    # 密码规则校验
    pwd_ok = True
    if new_password:
        import re as _re
        if len(new_password) < 8:
            st.caption("⚠️ 密码至少 8 位")
            pwd_ok = False
        categories = sum(1 for pat in [r"[A-Z]", r"[a-z]", r"[0-9]"] if _re.search(pat, new_password))
        if categories < 2:
            st.caption("⚠️ 需包含大写字母、小写字母、数字中的至少两种")
            pwd_ok = False
        if new_password != confirm_password:
            st.caption("⚠️ 两次密码不一致")
            pwd_ok = False

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button("✅ 确认重置", key=f"confirm_pwd_{target_username}",
                      type="primary", use_container_width=True,
                      disabled=not pwd_ok or not new_password):
            try:
                from auth_logic import hash_password
                new_hash = hash_password(new_password)
                manager.reset_password(target_username, new_hash, operator=current_admin)
                st.success(f"✅ 用户 {target_username} 的密码已重置")
                st.session_state.pop("admin_pending_action", None)
                st.rerun()
            except Exception as exc:
                st.error(f"❌ 密码重置失败: {exc}")
    with col_cancel:
        if st.button("取消", key=f"cancel_pwd_{target_username}",
                      use_container_width=True):
            st.session_state.pop("admin_pending_action", None)
            st.rerun()


def _render_toggle_disable_dialog(manager, target_username: str,
                                   current_disabled: bool, current_admin: str):
    """渲染禁用/启用确认弹窗。

    Args:
        manager: UserDataManager 实例
        target_username: 目标用户名
        current_disabled: 当前是否已禁用
        current_admin: 当前管理员用户名
    """
    action = "启用" if current_disabled else "禁用"
    action_icon = "✅" if current_disabled else "🚫"

    st.warning(f"⚠️ 确认{action}用户 **{target_username}**")

    if not current_disabled:
        st.info("禁用后该用户将无法登录，但其数据会被保留。")

    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button(f"{action_icon} 确认{action}", key=f"confirm_disable_{target_username}",
                      type="primary", use_container_width=True):
            try:
                manager.set_user_disabled(
                    target_username, not current_disabled, operator=current_admin,
                )
                st.success(f"✅ 用户 {target_username} 已{action}")
                st.session_state.pop("admin_pending_action", None)
                st.rerun()
            except Exception as exc:
                st.error(f"❌ {action}失败: {exc}")
    with col_cancel:
        if st.button("取消", key=f"cancel_disable_{target_username}",
                      use_container_width=True):
            st.session_state.pop("admin_pending_action", None)
            st.rerun()


def _render_user_management(manager, current_admin: str):
    """渲染增强的用户管理区域（搜索、筛选、排序、分页、操作按钮）。

    Args:
        manager: UserDataManager 实例
        current_admin: 当前管理员用户名
    """
    st.subheader("👥 用户管理")

    try:
        users = manager.list_users()
    except Exception as exc:
        st.error(f"加载用户列表失败: {exc}")
        return

    if not users:
        st.info("暂无注册用户")
        return

    # ── 工具栏：搜索 + 筛选 + 排序 ──
    col_search, col_filter, col_sort = st.columns([2, 1, 1.5])
    with col_search:
        search_input = st.text_input(
            "🔍 搜索用户名", placeholder="输入用户名搜索...",
            key="admin_user_search",
        )
        search_query = _safe_str(search_input)
    with col_filter:
        role_input = st.selectbox(
            "角色筛选", ["全部", "admin", "user", "guest"],
            key="admin_role_filter",
        )
        role_filter = _safe_str(role_input, "全部")
    with col_sort:
        sort_input = st.selectbox(
            "排序方式",
            ["注册时间 (新→旧)", "注册时间 (旧→新)",
             "最后活跃 (新→旧)", "最后活跃 (旧→新)"],
            key="admin_sort",
        )
        sort_choice = _safe_str(sort_input, "注册时间 (新→旧)")

    # 筛选与排序
    filtered = _filter_and_sort_users(users, search_query, role_filter, sort_choice)

    # ── 分页 ──
    page_size = 10
    total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
    current_page = st.session_state.get("admin_page", 0)
    current_page = max(0, min(current_page, total_pages - 1))

    start_idx = current_page * page_size
    page_users = filtered[start_idx:start_idx + page_size]

    # ── 用户列表表格（概览）──
    table_data = {
        "用户名": [u["username"] for u in page_users],
        "角色": [u["role"] for u in page_users],
        "邮箱": [u.get("email", "-") or "-" for u in page_users],
        "注册时间": [_format_datetime(u["created_at"]) for u in page_users],
        "最后活跃": [_format_datetime(u["last_active_at"]) for u in page_users],
        "登录次数": [u.get("login_count", 0) for u in page_users],
        "状态": ["🚫 禁用" if u.get("disabled") else "✅ 正常" for u in page_users],
    }
    st.table(table_data)

    # ── 角色分布摘要（caption 调用一次）──
    admin_count = sum(1 for u in users if u["role"] == "admin")
    user_count = sum(1 for u in users if u["role"] == "user")
    guest_count = sum(1 for u in users if u["role"] == "guest")
    caption_parts = [f"共 {len(users)} 位用户", f"管理员 {admin_count} 人",
                     f"普通用户 {user_count} 人"]
    if guest_count > 0:
        caption_parts.append(f"游客 {guest_count} 人")
    st.caption(" · ".join(caption_parts))

    # ── 分页控件 ──
    if len(filtered) > page_size:
        col_prev, col_info, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.button("← 上一页", disabled=(current_page == 0),
                         key="admin_prev_page"):
                st.session_state["admin_page"] = current_page - 1
                st.rerun()
        with col_info:
            st.markdown(
                f"<div style='text-align:center; padding-top:0.4rem; "
                f"color:#6C757D; font-size:0.85rem;'>"
                f"第 {current_page + 1} / {total_pages} 页 · "
                f"显示 {start_idx + 1}-{min(start_idx + page_size, len(filtered))} / "
                f"{len(filtered)} 条"
                f"</div>",
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button("下一页 →",
                         disabled=(current_page >= total_pages - 1),
                         key="admin_next_page"):
                st.session_state["admin_page"] = current_page + 1
                st.rerun()

    # ── 待确认操作弹窗 ──
    pending = st.session_state.get("admin_pending_action")
    if pending:
        target = pending.get("username", "")
        action_type = pending.get("type", "")
        # 查找目标用户信息
        target_user = next((u for u in users if u["username"] == target), None)
        if target_user is None:
            st.error(f"用户 {target} 不存在")
            st.session_state.pop("admin_pending_action", None)
        elif action_type == "change_role":
            _render_role_change_dialog(
                manager, target, target_user["role"], current_admin,
            )
        elif action_type == "delete":
            _render_delete_dialog(manager, target, current_admin)
        elif action_type == "reset_password":
            _render_reset_password_dialog(manager, target, current_admin)
        elif action_type == "toggle_disable":
            _render_toggle_disable_dialog(
                manager, target, target_user.get("disabled", False), current_admin,
            )
        else:
            st.session_state.pop("admin_pending_action", None)

    # ── 每行用户操作按钮 ──
    st.markdown("#### ⚙️ 用户操作")
    for user in page_users:
        username = user["username"]
        is_self = (username == current_admin)
        is_disabled = user.get("disabled", False)
        role_badge = user["role"]
        status_badge = "🚫禁用" if is_disabled else "✅正常"

        header_text = f"👤 **{username}** ({role_badge}) [{status_badge}]"
        if is_self:
            header_text += " · **（当前账号）**"

        with st.expander(header_text, expanded=False):
            # 用户详情
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**邮箱**: {user.get('email', '-') or '-'}")
                st.write(f"**注册时间**: {_format_datetime(user['created_at'])}")
            with col2:
                st.write(f"**最后活跃**: {_format_datetime(user['last_active_at'])}")
                st.write(f"**登录次数**: {user.get('login_count', 0)}")

            # 操作按钮（每行4个）
            col_role, col_pwd, col_disable, col_delete = st.columns(4)
            with col_role:
                if st.button("🔄 修改角色", key=f"btn_role_{username}",
                             use_container_width=True,
                             disabled=is_self):
                    st.session_state["admin_pending_action"] = {
                        "type": "change_role", "username": username,
                    }
                    st.rerun()
            with col_pwd:
                if st.button("🔑 重置密码", key=f"btn_pwd_{username}",
                             use_container_width=True):
                    st.session_state["admin_pending_action"] = {
                        "type": "reset_password", "username": username,
                    }
                    st.rerun()
            with col_disable:
                disable_label = "✅ 启用" if is_disabled else "🚫 禁用"
                if st.button(disable_label, key=f"btn_disable_{username}",
                             use_container_width=True,
                             disabled=is_self):
                    st.session_state["admin_pending_action"] = {
                        "type": "toggle_disable", "username": username,
                    }
                    st.rerun()
            with col_delete:
                if st.button("🗑️ 删除", key=f"btn_delete_{username}",
                             use_container_width=True,
                             disabled=is_self):
                    st.session_state["admin_pending_action"] = {
                        "type": "delete", "username": username,
                    }
                    st.rerun()

            if is_self:
                st.caption("⚠️ 无法对当前登录的管理员账号执行修改角色/禁用/删除操作")


# ── 主入口 ───────────────────────────────────────────────

def render_admin_panel(manager):
    """渲染完整的管理面板。

    Args:
        manager: UserDataManager 实例（通过 get_data_manager() 获取）
    """
    # 安全校验：再次确认当前用户为管理员（防御性编程）
    from auth_components import is_admin, get_auth_username

    if not is_admin():
        st.error("⛔ 权限不足，仅管理员可访问管理面板")
        return

    current_admin = get_auth_username()

    # 渲染标题
    _render_admin_header()

    # 返回按钮
    col_back, col_refresh = st.columns([1, 4])
    with col_back:
        if st.button("← 返回主界面", key="admin_back_to_main"):
            st.session_state["view"] = "main"
            st.rerun()
    with col_refresh:
        if st.button("🔄 刷新数据", key="admin_refresh"):
            st.session_state.pop("admin_page", None)
            st.session_state.pop("admin_pending_action", None)
            st.rerun()

    st.divider()

    # 系统统计
    _render_system_stats(manager)

    st.divider()

    # 用户管理（增强）
    _render_user_management(manager, current_admin)
