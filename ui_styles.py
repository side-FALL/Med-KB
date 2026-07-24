"""医学教材知识库 — UI 样式模块

所有 CSS 样式从 static/ 目录加载，减少内联 HTML 解析开销。
"""

from pathlib import Path

import streamlit as st


_STATIC_DIR = Path(__file__).resolve().parent / "static"


@st.cache_resource
def _read_css(filename: str) -> str:
    """读取 CSS 文件内容（缓存，只读一次）。"""
    return (_STATIC_DIR / filename).read_text(encoding="utf-8")


def inject_global_styles():
    """注入全局 CSS 样式（从外部文件加载）。"""
    css = _read_css("styles.css")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def inject_tooltip_styles():
    """注入更新公告 tooltip 样式（从外部文件加载）。"""
    css = _read_css("tooltip.css")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# ── 用户偏好样式（主题 / 字体大小）─────────────────────────

_SETTINGS_CARD_CSS = """
/* 设置页用户信息卡片（类选择器实现，暗色主题可覆盖） */
.settings-user-card {
    background: linear-gradient(135deg, rgba(13,110,253,0.08) 0%, rgba(10,88,202,0.04) 100%);
    border-radius: 12px;
    padding: 1rem;
    margin-bottom: 1rem;
    border: 1px solid rgba(13,110,253,0.15);
}
.settings-guest-card {
    background: #FFF8E1;
    border: 1px solid #FFE082;
}
.settings-user-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.6rem;
}
.settings-user-avatar { font-size: 1.5rem; }
.settings-user-name {
    font-weight: 700;
    color: #0A58CA;
    font-size: 1.1rem;
}
.settings-user-name.guest { color: #F57F17; }
.settings-role-badge {
    font-size: 0.68rem;
    background: #6C757D;
    color: #FFFFFF;
    padding: 2px 8px;
    border-radius: 8px;
    font-weight: 600;
}
.settings-role-badge.admin { background: #0D6EFD; }
.settings-user-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.5rem 1.5rem;
    margin-bottom: 0.6rem;
}
.settings-user-field { display: flex; flex-direction: column; }
.settings-field-label {
    font-size: 0.72rem;
    color: #6C757D;
    text-transform: uppercase;
    letter-spacing: 0.03em;
}
.settings-field-value {
    font-size: 0.9rem;
    color: #212529;
    font-weight: 500;
    word-break: break-all;
}
.settings-user-meta {
    display: flex;
    gap: 2rem;
    color: #6C757D;
    font-size: 0.85rem;
}

/* 教材管理列表项 */
.tm-book-name {
    font-size: 0.9rem;
    font-weight: 500;
    color: #212529;
    line-height: 1.8;
}
.tm-subject-badge {
    display: inline-block;
    font-size: 0.72rem;
    background: rgba(13,110,253,0.10);
    color: #0A58CA;
    padding: 2px 8px;
    border-radius: 6px;
    font-weight: 600;
}
.tm-chunks {
    font-size: 0.82rem;
    color: #6C757D;
}
.tm-selected-badge {
    display: inline-block;
    font-size: 0.68rem;
    background: #198754;
    color: #FFFFFF;
    padding: 2px 8px;
    border-radius: 6px;
    font-weight: 600;
}
/* 搜索历史列表项 */
.hist-query {
    font-size: 0.88rem;
    color: #212529;
    line-height: 1.6;
    word-break: break-all;
}

/* 学习统计面板 */
.ls-card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 0.75rem;
    margin: 0.5rem 0 1rem 0;
}
.ls-card {
    background: #FFFFFF;
    border: 1px solid #E3E8EF;
    border-radius: 12px;
    padding: 0.85rem 1rem;
    display: flex;
    align-items: center;
    gap: 0.75rem;
    box-shadow: 0 1px 2px rgba(16,24,40,0.05);
}
.ls-card-icon {
    flex-shrink: 0;
    width: 2.4rem;
    height: 2.4rem;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.15rem;
}
.ls-card-icon.blue { background: rgba(13,110,253,0.10); }
.ls-card-icon.green { background: rgba(25,135,84,0.10); }
.ls-card-icon.orange { background: rgba(253,126,20,0.12); }
.ls-card-icon.purple { background: rgba(111,66,193,0.10); }
.ls-card-body { min-width: 0; }
.ls-card-value {
    font-size: 1.45rem;
    font-weight: 700;
    color: #1A2332;
    line-height: 1.2;
    font-variant-numeric: tabular-nums;
}
.ls-card-label {
    font-size: 0.72rem;
    color: #6C757D;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
}
.ls-insight-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
}
.ls-insight-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.82rem;
    color: #3D4A5C;
    background: #F1F5F9;
    border: 1px solid #E3E8EF;
    border-radius: 999px;
    padding: 0.3rem 0.85rem;
}
.ls-insight-chip strong { color: #0A58CA; font-weight: 600; }
.ls-chart-title {
    font-size: 0.85rem;
    font-weight: 600;
    color: #3D4A5C;
    margin-bottom: 0.15rem;
}
.ls-chart-help {
    font-size: 0.75rem;
    color: #8A8F98;
    margin-bottom: 0.35rem;
}
"""

_DARK_THEME_CSS = """
/* 暗色主题覆盖（用户偏好：theme=dark） */
.stApp {
    background-color: #0E1117;
    color: #E6E6E6;
}
.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stMarkdownContainer"] p,
.stApp [data-testid="stMarkdownContainer"] span,
.stApp [data-testid="stMarkdownContainer"] li,
.stApp [data-testid="stMarkdownContainer"] h1,
.stApp [data-testid="stMarkdownContainer"] h2,
.stApp [data-testid="stMarkdownContainer"] h3,
.stApp [data-testid="stMarkdownContainer"] h4,
.stApp [data-testid="stCaptionContainer"],
.stApp label, .stApp label p, .stApp label span {
    color: #E6E6E6 !important;
}
.stApp [data-testid="stHeader"] {
    background-color: rgba(14,17,23,0.9);
}
.stApp [data-testid="stTabs"] button[role="tab"] {
    color: #B0B3B8;
}
.stApp [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #FFFFFF;
}
.stApp input, .stApp textarea,
.stApp [data-baseweb="select"] > div,
.stApp [data-baseweb="input"] > div {
    background-color: #1C1F26 !important;
    color: #E6E6E6 !important;
    border-color: #3A3F4B !important;
}
.stApp [data-testid="stExpander"] {
    background-color: #161922;
    border-color: #3A3F4B;
}
.stApp [data-testid="stMetricValue"],
.stApp [data-testid="stMetricLabel"] {
    color: #E6E6E6 !important;
}
.stApp hr { border-color: #3A3F4B; }
/* 设置页卡片暗色适配 */
.settings-user-card {
    background: linear-gradient(135deg, rgba(13,110,253,0.18) 0%, rgba(10,88,202,0.10) 100%);
    border-color: rgba(13,110,253,0.35);
}
.settings-guest-card {
    background: #2A2416;
    border-color: #5C4D1F;
}
.settings-user-name { color: #6EA8FE; }
.settings-user-name.guest { color: #FFB74D; }
.settings-field-label { color: #8A8F98; }
.settings-field-value { color: #E6E6E6; }
.settings-user-meta { color: #8A8F98; }
/* 教材管理暗色适配 */
.tm-book-name { color: #E6E6E6; }
.tm-subject-badge { background: rgba(13,110,253,0.25); color: #6EA8FE; }
.tm-chunks { color: #8A8F98; }
/* 搜索历史暗色适配 */
.hist-query { color: #E6E6E6; }
/* 学习统计暗色适配 */
.ls-card {
    background: #161922;
    border-color: #3A3F4B;
    box-shadow: none;
}
.ls-card-icon.blue { background: rgba(13,110,253,0.22); }
.ls-card-icon.green { background: rgba(25,135,84,0.22); }
.ls-card-icon.orange { background: rgba(253,126,20,0.22); }
.ls-card-icon.purple { background: rgba(111,66,193,0.22); }
.ls-card-value { color: #E6E6E6; }
.ls-card-label { color: #8A8F98; }
.ls-insight-chip {
    background: #1C1F26;
    border-color: #3A3F4B;
    color: #B0B3B8;
}
.ls-insight-chip strong { color: #6EA8FE; }
.ls-chart-title { color: #B0B3B8; }
"""


def inject_user_preferences():
    """按 session_state 中的偏好注入字体大小与主题 CSS。

    需在认证检查之后、页面内容渲染之前调用：
    widget 修改偏好 → 触发 rerun → 本函数在新一次 run 顶部注入，
    保证切换主题/字体立即生效（无需额外 st.rerun）。
    注意不使用 contain: layout size（会导致元素重叠）。
    """
    font_size = st.session_state.get("pref_font_size", 14)
    try:
        font_size = int(font_size)
    except (TypeError, ValueError):
        font_size = 14
    font_size = max(10, min(32, font_size))

    font_css = f"""
.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stMarkdownContainer"] p,
.stApp [data-testid="stMarkdownContainer"] li,
.stApp [data-testid="stCaptionContainer"] {{
    font-size: {font_size}px;
}}
"""

    css = _SETTINGS_CARD_CSS + font_css
    if st.session_state.get("pref_theme", "light") == "dark":
        css += _DARK_THEME_CSS

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
