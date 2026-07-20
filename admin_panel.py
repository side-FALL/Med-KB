"""医学教材知识库 - 管理面板

管理员专属面板，提供用户管理和系统统计功能。
仅对 admin 角色用户可见，普通用户和游客无法访问。

功能：
- 查看所有已注册用户列表（用户名、角色、注册时间等）
- 查看系统统计（总用户数、总查询次数）

数据来源：通过 UserDataManager 的现有方法获取，不直接操作数据库。
"""

from datetime import datetime

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


def _render_user_list(manager):
    """渲染用户列表区域。

    Args:
        manager: UserDataManager 实例
    """
    st.subheader("👥 用户列表")

    try:
        users = manager.list_users()
    except Exception as exc:
        st.error(f"加载用户列表失败: {exc}")
        return

    if not users:
        st.info("暂无注册用户")
        return

    # 构建展示用表格数据
    table_data = {
        "用户名": [],
        "角色": [],
        "注册时间": [],
        "最后活跃": [],
        "数据大小(KB)": [],
    }

    for u in users:
        table_data["用户名"].append(u["username"])
        table_data["角色"].append(u["role"])
        table_data["注册时间"].append(_format_datetime(u["created_at"]))
        table_data["最后活跃"].append(_format_datetime(u["last_active_at"]))
        table_data["数据大小(KB)"].append(u["data_size_kb"])

    st.table(table_data)

    # 额外信息：角色分布
    st.caption(
        f"共 {len(users)} 位用户 · "
        f"管理员 {sum(1 for u in users if u['role'] == 'admin')} 人 · "
        f"普通用户 {sum(1 for u in users if u['role'] == 'user')} 人"
    )


# ── 主入口 ───────────────────────────────────────────────

def render_admin_panel(manager):
    """渲染完整的管理面板。

    Args:
        manager: UserDataManager 实例（通过 get_data_manager() 获取）
    """
    # 安全校验：再次确认当前用户为管理员（防御性编程）
    from auth_components import is_admin

    if not is_admin():
        st.error("⛔ 权限不足，仅管理员可访问管理面板")
        return

    # 渲染标题
    _render_admin_header()

    # 返回按钮
    col_back, _ = st.columns([1, 4])
    with col_back:
        if st.button("← 返回主界面", key="admin_back_to_main"):
            st.session_state["view"] = "main"
            st.rerun()

    st.divider()

    # 系统统计
    _render_system_stats(manager)

    st.divider()

    # 用户列表
    _render_user_list(manager)
