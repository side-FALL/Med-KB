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
