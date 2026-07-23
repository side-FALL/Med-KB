"""医学教材知识库 - 管理面板

管理员专属面板，提供用户管理和系统统计功能。
仅对 admin 角色用户可见，普通用户和游客无法访问。

功能：
- 查看所有已注册用户列表（用户名、角色、注册时间等）
- 查看系统统计（总用户数、总查询次数、活跃用户数）
- 趋势分析（查询次数趋势图、用户增长趋势图）
- 模式使用分布（各功能模式使用占比）
- 系统配置（当前模型、API Key 状态、教材数量、存储后端状态）
- 用户管理增强：角色变更、删除用户、重置密码、禁用/启用
- 搜索、筛选、排序、分页

数据来源：通过 UserDataManager 的现有方法获取，不直接操作数据库。
"""

from datetime import datetime, timedelta, timezone
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


def _parse_dt(iso_str: str):
    """将 ISO 8601 字符串解析为 aware datetime，失败返回 None。"""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _render_system_stats(manager):
    """渲染系统统计区域（含活跃用户指标）。

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
        return

    # ── 活跃用户统计（基于最后登录时间）──
    st.markdown("##### 🔄 活跃用户")
    try:
        users = manager.list_users()
    except Exception as exc:
        st.warning(f"加载用户列表失败: {exc}")
        return

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    active_today = active_week = active_month = 0
    for u in users:
        last = _parse_dt(u.get("last_active_at", ""))
        if last is None:
            continue
        if last >= today_start:
            active_today += 1
        if last >= week_start:
            active_week += 1
        if last >= month_start:
            active_month += 1

    ca1, ca2, ca3 = st.columns(3)
    with ca1:
        st.metric("今日活跃", active_today)
    with ca2:
        st.metric("本周活跃", active_week)
    with ca3:
        st.metric("本月活跃", active_month)


def _render_trend_charts(manager):
    """渲染查询趋势图和用户增长趋势图。

    Args:
        manager: UserDataManager 实例
    """
    st.subheader("📈 趋势分析")

    col_query, col_growth = st.columns(2)

    # ── 查询次数趋势（按天聚合，基于操作日志）──
    with col_query:
        st.markdown("**查询次数趋势**")
        try:
            logs = manager.get_operation_logs()
            query_logs = [l for l in logs if l.get("action") == "query"]
            if query_logs:
                # 按天聚合
                daily_counts: dict[str, int] = {}
                for log in query_logs:
                    dt = _parse_dt(log.get("timestamp", ""))
                    if dt:
                        day_key = dt.strftime("%Y-%m-%d")
                        daily_counts[day_key] = daily_counts.get(day_key, 0) + 1

                if daily_counts:
                    import pandas as pd
                    df = pd.DataFrame(
                        sorted(daily_counts.items()),
                        columns=["日期", "查询次数"],
                    )
                    df = df.set_index("日期")
                    st.line_chart(df, use_container_width=True)
                else:
                    st.caption("暂无查询记录")
            else:
                st.caption("暂无查询记录")
        except Exception as exc:
            st.error(f"加载查询趋势失败: {exc}")

    # ── 用户增长趋势（按注册时间累积）──
    with col_growth:
        st.markdown("**用户增长趋势**")
        try:
            users = manager.list_users()
            if users:
                # 按注册日期聚合
                daily_reg: dict[str, int] = {}
                for u in users:
                    dt = _parse_dt(u.get("created_at", ""))
                    if dt:
                        day_key = dt.strftime("%Y-%m-%d")
                        daily_reg[day_key] = daily_reg.get(day_key, 0) + 1

                if daily_reg:
                    import pandas as pd
                    sorted_days = sorted(daily_reg.items())
                    dates = [d[0] for d in sorted_days]
                    counts = [d[1] for d in sorted_days]
                    # 累积增长
                    cumulative = []
                    running = 0
                    for c in counts:
                        running += c
                        cumulative.append(running)
                    df = pd.DataFrame({"用户总数": cumulative}, index=dates)
                    st.line_chart(df, use_container_width=True)
                else:
                    st.caption("暂无注册记录")
            else:
                st.caption("暂无注册用户")
        except Exception as exc:
            st.error(f"加载用户增长趋势失败: {exc}")


def _render_mode_usage(manager):
    """渲染各模式使用占比图（基于学习记录的 source_type）。

    Args:
        manager: UserDataManager 实例
    """
    st.subheader("🎯 模式使用分布")

    try:
        records = manager.get_all_learning_records()
    except Exception as exc:
        st.error(f"加载学习记录失败: {exc}")
        return

    if not records:
        st.caption("暂无学习记录数据")
        return

    # 按 source_type 聚合
    mode_counts: dict[str, int] = {}
    mode_labels = {
        "qa": "智能问答", "quiz": "自测刷题", "compare": "对比学习",
        "case": "病例分析", "agent": "智能体", "chat": "对话",
        "search": "搜索", "textbook": "教材浏览",
    }
    for rec in records:
        source = rec.get("source_type", "chat")
        label = mode_labels.get(source, source)
        mode_counts[label] = mode_counts.get(label, 0) + 1

    if not mode_counts:
        st.caption("暂无模式使用数据")
        return

    col_chart, col_detail = st.columns([3, 1])

    with col_chart:
        import pandas as pd
        df = pd.DataFrame(
            list(mode_counts.items()), columns=["模式", "次数"],
        )
        st.bar_chart(df.set_index("模式"), use_container_width=True)

    with col_detail:
        total = sum(mode_counts.values())
        for mode, count in sorted(mode_counts.items(), key=lambda x: -x[1]):
            pct = round(count / total * 100, 1) if total > 0 else 0
            st.text(f"{mode}: {count} ({pct}%)")


def _render_system_config(manager):
    """渲染系统配置区域（模型、API Key、教材、存储后端状态）。

    Args:
        manager: UserDataManager 实例
    """
    st.subheader("⚙️ 系统配置")

    # ── 当前模型 ──
    current_model = st.session_state.get("model_key", "")
    if current_model:
        try:
            from config import MODELS
            model_info = MODELS.get(current_model, {})
            model_name = model_info.get("name", current_model)
        except Exception:
            model_name = current_model
    else:
        model_name = "未选择（使用默认降级顺序）"

    # ── API Key 状态 ──
    api_key_status = {}
    try:
        from config import MODEL_PROVIDERS, get_api_key
        for provider, info in MODEL_PROVIDERS.items():
            env_var = info["api_key_env"]
            key = get_api_key(provider)
            api_key_status[provider] = {
                "env_var": env_var,
                "configured": bool(key),
            }
    except Exception:
        pass

    # ── 教材数量 ──
    book_count = 0
    total_chunks = 0
    try:
        from ui_components import load_manifest, get_book_stats
        manifest = load_manifest()
        if manifest:
            _, book_count, total_chunks, _ = get_book_stats(manifest)
    except Exception:
        pass

    # ── 存储后端状态 ──
    storage_status = "未知"
    try:
        client = manager._client
        if hasattr(client, "_available"):
            if client._available:
                storage_status = "✅ Upstash Redis（在线）"
                if getattr(client, "_jsonbin", None) is not None:
                    storage_status += " + JSONBin 降级（备用）"
            else:
                if getattr(client, "_jsonbin", None) is not None:
                    storage_status = "⚠️ Upstash 未配置，使用 JSONBin 降级"
                else:
                    storage_status = "🔴 存储后端均未配置"
    except Exception:
        pass

    # ── 展示配置 ──
    col_model, col_books = st.columns(2)

    with col_model:
        st.markdown("**当前模型**")
        st.code(model_name)
        st.markdown("**存储后端**")
        st.write(storage_status)

    with col_books:
        st.markdown("**教材索引**")
        st.write(f"📚 教材数量: **{book_count}** 本")
        st.write(f"📄 文本块总数: **{total_chunks:,}**")

    # ── API Key 状态表 ──
    if api_key_status:
        st.markdown("**API Key 配置状态**")
        key_rows = []
        for provider, info in api_key_status.items():
            status_icon = "✅ 已配置" if info["configured"] else "❌ 未配置"
            key_rows.append({
                "提供商": provider,
                "环境变量": info["env_var"],
                "状态": status_icon,
            })
        st.table(key_rows)


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

    # 系统统计（含活跃用户）
    _render_system_stats(manager)

    st.divider()

    # 趋势分析（查询趋势 + 用户增长）
    _render_trend_charts(manager)

    st.divider()

    # 模式使用分布
    _render_mode_usage(manager)

    st.divider()

    # 系统配置
    _render_system_config(manager)

    st.divider()

    # 用户管理（增强）
    _render_user_management(manager, current_admin)
