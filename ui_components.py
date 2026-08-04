"""医学教材知识库 — UI 组件模块

包含侧边栏渲染、教材选择器、模式切换等可复用 UI 组件。
"""

import re
import json
import html
import logging
from datetime import datetime

import numpy as np
import streamlit as st
from pathlib import Path

from config import MODELS, is_model_free, check_model_password

logger = logging.getLogger(__name__)


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符，防止 XSS 攻击。

    用于将用户可控内容（查询、用户名等）安全地嵌入 unsafe_allow_html=True 的
    st.markdown 输出中。& < > " ' 均按 HTML 实体转义。
    """
    if not isinstance(text, str):
        text = str(text)
    return html.escape(text, quote=True)


# ── LaTeX 公式修复 ──────────────────────────────────────

def fix_latex_formulas(text: str) -> str:
    """修复LLM输出的LaTeX公式格式，使其兼容Streamlit渲染。

    将 [ ... ] 格式的公式转换为 $$ ... $$
    同时清理 \\text{} 包裹，简化化学式表示。
    """
    # 将 \[ ... \] 转换为 $$ ... $$
    text = re.sub(r'\\\[(.+?)\\\]', r'$$\1$$', text, flags=re.DOTALL)

    # 将 [ ... ] 转换为 $$ ... $$ （当内容包含 LaTeX 命令时）
    def replace_bracket_formula(match):
        content = match.group(1).strip()
        content = re.sub(r'\\text\{([^}]*)\}', r'\1', content)
        return f'$${content}$$'

    pattern = r'\[([^\]]*(?:\\(?:rightarrow|leftarrow|text|frac|sum|int)|[_^]\{)[^\]]*)\]'
    text = re.sub(pattern, replace_bracket_formula, text)

    pattern2 = r'\[([^\]]{30,})\]'
    text = re.sub(pattern2, lambda m: f'$${m.group(1).strip()}$$' if '\\' in m.group(1) or '_' in m.group(1) or '^' in m.group(1) else m.group(0), text)

    return text


# ── 数据加载 ────────────────────────────────────────────

_APP = Path(__file__).resolve().parent
BOOKS_DIR = _APP / "books"
_MANIFEST = BOOKS_DIR / "manifest.json"


@st.cache_resource
def load_manifest():
    """加载教材清单（轻量，启动时调用）。"""
    if not _MANIFEST.exists():
        return None
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def get_book_stats(manifest: dict) -> tuple[list[str], int, int, dict]:
    """从清单中提取教材统计信息。

    启动时校验每本教材的 .json 和 .npz 文件是否存在，
    缺失任一文件的书不计入可选教材列表，并记录警告日志。

    Returns:
        (ALL_BOOKS, book_count, total_chunks, book_stats)
    """
    missing_books = []
    available_books = {}
    for name, info in manifest.items():
        fname = info["file"]
        json_path = BOOKS_DIR / f"{fname}.json"
        npz_path = BOOKS_DIR / f"{fname}.npz"
        if not json_path.exists() or not npz_path.exists():
            missing_files = []
            if not json_path.exists():
                missing_files.append(f"{fname}.json")
            if not npz_path.exists():
                missing_files.append(f"{fname}.npz")
            missing_books.append((name, missing_files))
        else:
            available_books[name] = info

    if missing_books:
        for name, files in missing_books:
            logger.warning("教材文件缺失，已从可选列表移除: %s (缺失: %s)", name, ", ".join(files))

    ALL_BOOKS = sorted(available_books.keys(), key=lambda x: -available_books[x]["chunks"])
    book_count = len(ALL_BOOKS)
    total_chunks = sum(v["chunks"] for v in available_books.values())
    book_stats = {name: info["chunks"] for name, info in available_books.items()}
    return ALL_BOOKS, book_count, total_chunks, book_stats


@st.cache_resource
def load_book(book_name: str, manifest: dict):
    """按需加载单本教材（带缓存）。

    若 .json 或 .npz 文件缺失，记录警告并返回 None，不抛出异常。
    """
    info = manifest[book_name]
    fname = info["file"]
    npz_path = BOOKS_DIR / f"{fname}.npz"
    json_path = BOOKS_DIR / f"{fname}.json"

    if not npz_path.exists() or not json_path.exists():
        missing = []
        if not npz_path.exists():
            missing.append(f"{fname}.npz")
        if not json_path.exists():
            missing.append(f"{fname}.json")
        logger.warning("教材文件缺失，已跳过: %s (缺失: %s)", book_name, ", ".join(missing))
        return None

    emb_data = np.load(npz_path)
    emb = emb_data["embeddings"].astype(np.float32)
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    with open(json_path, encoding="utf-8") as f:
        meta = json.load(f)
    return emb, meta["documents"], meta["metadatas"]


def load_selected_books(book_names: list[str], manifest: dict):
    """加载选中的教材数据。

    单本教材加载失败（文件缺失）时跳过该书，不影响其余书正常加载。
    """
    all_emb, all_docs, all_metas = [], [], []
    for name in book_names:
        if name in manifest:
            result = load_book(name, manifest)
            if result is None:
                continue
            emb, docs, metas = result
            all_emb.append(emb)
            all_docs.extend(docs)
            all_metas.extend(metas)
    if not all_emb:
        return None, [], []
    return np.vstack(all_emb), all_docs, all_metas


# ── 侧边栏组件 ─────────────────────────────────────────

def render_sidebar(book_count: int, total_chunks: int):
    """渲染侧边栏设置面板。"""
    with st.sidebar:
        # 品牌标识
        st.markdown("""
        <div style="text-align:center; padding:1rem 0; margin-bottom:1rem; border-bottom:2px solid #E9ECEF;">
            <div style="font-size:2rem; margin-bottom:0.3rem;">🏥</div>
            <div style="font-size:1.1rem; font-weight:700; color:#0A58CA;">医学教材知识库</div>
            <div style="font-size:0.78rem; color:#6C757D;">AI 驱动的医学学习助手</div>
        </div>
        """, unsafe_allow_html=True)

        # 设置区域
        st.markdown("### ⚙️ 检索设置")

        top_k = st.slider("返回结果数", 3, 15, 10,
            help="每次检索返回的相关文本块数量")
        alpha = st.slider("向量权重 (α)", 0.0, 1.0, 0.7,
            help="1.0=纯向量检索，0.0=纯关键词检索", key="alpha_slider")

        st.markdown("### 💬 对话设置")
        use_context = st.checkbox("启用多轮对话",
            value=st.session_state.get("use_context", True),
            help="开启后AI会参考之前对话的上下文", key="use_context_cb")

        turns = len(st.session_state.conversation_turns)
        if turns > 0:
            st.markdown(f"""
            <div style="background:#E8F5E9; border-radius:8px; padding:0.5rem 0.8rem; margin-top:0.5rem;">
                <span style="color:#2E7D32; font-size:0.85rem;">📝 已进行 {turns} 轮对话</span>
            </div>
            """, unsafe_allow_html=True)

        st.divider()

        # 搜索历史
        st.markdown("### 📜 搜索历史")
        if st.session_state.hist:
            history_html = '<div style="max-height:250px; overflow-y:auto;">'
            for i, item in enumerate(reversed(st.session_state.hist[-10:])):
                mode_icon = {"问答":"💬","刷题":"📝","对比":"🔄","病例":"🏥","智能体":"🤖"}.get(item.get("mode","问答"),"💬")
                safe_q = _escape_html(item.get('q', '')[:30])
                history_html += f'''
                <div class="sidebar-history-item">
                    <span>{mode_icon}</span>
                    <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{safe_q}</span>
                </div>'''
            history_html += '</div>'
            st.markdown(history_html, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="text-align:center; padding:1rem; color:#ADB5BD;">
                <div style="font-size:1.5rem; margin-bottom:0.3rem;">📭</div>
                <div style="font-size:0.82rem;">暂无搜索历史</div>
            </div>
            """, unsafe_allow_html=True)

        st.divider()

        # 清空按钮
        if st.button("🗑️ 清空全部记录", use_container_width=True):
            st.session_state.hist = []
            st.session_state.conversation_turns = []
            st.session_state.favorites = []
            st.rerun()

        st.divider()

        # 统计信息
        st.markdown(f"""
        <div style="background:linear-gradient(135deg, rgba(13,110,253,0.05) 0%, rgba(10,88,202,0.02) 100%); 
                    border-radius:12px; padding:1rem; border:1px solid rgba(13,110,253,0.1);">
            <div style="font-weight:700; color:#0A58CA; margin-bottom:0.8rem; font-size:0.9rem;">📊 知识库统计</div>
            <div style="display:flex; flex-direction:column; gap:0.5rem;">
                <div style="display:flex; align-items:center; gap:0.5rem; color:#495057; font-size:0.85rem;">
                    <span>📚</span> <span>教材数量: <strong>{book_count}</strong> 本</span>
                </div>
                <div style="display:flex; align-items:center; gap:0.5rem; color:#495057; font-size:0.85rem;">
                    <span>📄</span> <span>文本块数: <strong>{total_chunks:,}</strong> 块</span>
                </div>
                <div style="display:flex; align-items:center; gap:0.5rem; color:#495057; font-size:0.85rem;">
                    <span>🧠</span> <span>嵌入模型: <strong>BGE-M3</strong> (免费)</span>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    return top_k, alpha, use_context


# ── 教材范围与模型选择 ──────────────────────────────────

def _sync_scope_to_settings() -> None:
    """主界面教材范围变更时，同步到设置页 widget 状态（双向同步）。"""
    st.session_state["settings_scope_sel"] = st.session_state["scope_selector"]


def _sync_model_to_settings() -> None:
    """主界面模型变更时，同步到设置页 widget 状态（双向同步）。"""
    st.session_state["settings_model_sel"] = st.session_state["model_selector"]


def render_scope_and_model_selector(book_count: int, book_stats: dict, ALL_BOOKS: list[str]):
    """渲染教材范围选择器和模型选择器。

    Returns:
        (scope, selected_model, selected_books)
    """
    col_scope, col_model = st.columns([3, 1])
    with col_scope:
        scope = st.selectbox("📚 教材范围", ["全部教材", "选择教材"], label_visibility="collapsed", key="scope_selector",
                             on_change=_sync_scope_to_settings)

    # 切换到"选择教材"时重置选择，确保初始状态为空
    if st.session_state.get("_prev_scope") != scope:
        if scope == "选择教材":
            st.session_state.selected_books = []
        st.session_state._prev_scope = scope
    with col_model:
        selected_model = st.selectbox("🤖 AI模型", list(MODELS.keys()),
            format_func=lambda x: MODELS[x]["name"], label_visibility="collapsed", key="model_selector",
            on_change=_sync_model_to_settings)

    # 模型信息提示
    _current_model = MODELS[selected_model]
    _is_free = is_model_free(selected_model)
    if _is_free:
        st.caption("🆓 免费模型 · 请求过多会限制，如遇报错请切换其他模型")
    else:
        if not check_model_password():
            st.stop()
        st.caption(f"🤖 当前模型: {_current_model['name']}（已验证）")

    selected_books = ALL_BOOKS
    if scope == "选择教材":
        with st.expander("📖 选择要检索的教材", expanded=True):
            # 初始化 session_state（在渲染 widget 之前）
            if "selected_books" not in st.session_state:
                st.session_state.selected_books = []

            c1, c2 = st.columns([4, 1])
            with c2:
                if st.button("全选", use_container_width=True, key="sel_all"):
                    st.session_state.selected_books = ALL_BOOKS
                    st.rerun()
                if st.button("清空", use_container_width=True, key="sel_clr"):
                    st.session_state.selected_books = []
                    st.rerun()

            # 使用 key 绑定 session_state，不使用 default 参数避免状态冲突
            selected_books = st.multiselect("选择教材", ALL_BOOKS,
                key="selected_books",
                placeholder="请选择教材",
                format_func=lambda x: f"{x} ({book_stats[x]}块)",
                label_visibility="collapsed")
            if selected_books:
                n = sum(book_stats.get(b, 0) for b in selected_books)
                st.caption(f"已选 {len(selected_books)}/{book_count} 本，{n} 个文本块")
            else:
                st.warning("请至少选择一本教材")

    return scope, selected_model, selected_books


# ── 更新公告 ────────────────────────────────────────────

def render_update_announcement():
    """渲染更新公告 tooltip。"""
    from ui_styles import inject_tooltip_styles
    inject_tooltip_styles()

    st.markdown("""
<div style="text-align:center; margin-bottom:1rem;">
    <span class="tooltip-container">
        <span class="tooltip-icon">?</span>
        <div class="tooltip-content">
            <h4>v2.2.6 更新公告</h4>
            <p><strong>重点总结模式 + 游客记录持久化：</strong></p>
            <ul>
                <li>📋 重点总结：上传重点文档，依据教材总结并导出 Markdown</li>
                <li>👤 游客记录：游客模式按会话生成 ID，保存学习记录 30 天</li>
                <li>📊 管理统计：注册用户与游客分列统计，不互相稀释</li>
                <li>🛡️ 防御容错：教材文件缺失时跳过不崩溃，单书缺失不影响整站</li>
                <li>📚 数据重建：药理学、病理生理学从 OCR 重建，43 本全量校验</li>
            </ul>
            <p style="margin-top:0.8rem; color:#666; font-size:0.8rem;">
                注册账号永久保存学习数据，游客仅保存学习记录 30 天
            </p>
        </div>
    </span>
</div>
""", unsafe_allow_html=True)


# ── 空状态提示 ──────────────────────────────────────────

def render_empty_state(title: str, subtitle: str, examples: list[str] = None):
    """渲染空状态居中提示。"""
    examples_html = ""
    if examples:
        examples_html = '<div class="example-questions">'
        for ex in examples:
            examples_html += f'<span class="example-question">{ex}</span>'
        examples_html += '</div>'
    
    st.markdown(f"""
    <div class="empty-state fade-in-up">
        <span class="empty-state-icon">🏥</span>
        <h2 class="empty-state-title">{title}</h2>
        <p class="empty-state-subtitle">{subtitle}</p>
        {examples_html}
    </div>
    """, unsafe_allow_html=True)


# ── Markdown 导出 ───────────────────────────────────────

def render_markdown_export_button(answer: str, question: str = "",
                                  mode_label: str = "", key: str = ""):
    """在输出结果下方渲染"导出为 Markdown"下载按钮。

    Args:
        answer: AI 输出正文（Markdown 原文，未做 LaTeX 修复，保持原始内容）
        question: 用户问题（作为文档标题）
        mode_label: 模式名称（问答/对比/病例/智能体）
        key: st.download_button 唯一 key（同一页面多个按钮必须不同）
    """
    if not answer or not answer.strip():
        return

    ts = datetime.now()
    title = question.strip()[:50] or "输出结果"
    lines = [f"# {title}", ""]
    if mode_label:
        lines.append(f"> 模式：{mode_label}  ")
    lines.append(f"> 导出自 医学教材知识库 · {ts.strftime('%Y-%m-%d %H:%M:%S')}")
    lines += ["", "---", "", answer, ""]
    md_content = "\n".join(lines)

    st.download_button(
        "📥 导出为 Markdown",
        data=md_content,
        file_name=f"med-kb_{mode_label or 'export'}_{ts.strftime('%Y%m%d_%H%M%S')}.md",
        mime="text/markdown",
        key=key or f"export_md_{ts.strftime('%H%M%S%f')}",
    )


# ── 对话气泡 ────────────────────────────────────────────

def render_user_bubble(text: str):
    """渲染用户消息气泡。"""
    safe_text = _escape_html(text)
    st.markdown(f'<div class="user-bubble fade-in-up">{safe_text}</div>', unsafe_allow_html=True)


def render_ai_bubble(label: str = ""):
    """渲染 AI 消息气泡头部。"""
    if label:
        safe_label = _escape_html(label)
        st.markdown(f'<div class="ai-bubble fade-in-up"><strong>💡 {safe_label}</strong></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="ai-bubble fade-in-up"></div>', unsafe_allow_html=True)


def render_source_cards(hits: list[dict]):
    """渲染参考来源卡片（独立区域版本，供其他模式使用）。"""
    if not hits:
        return

    st.markdown("""
    <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.8rem;">
        <span style="font-size:1.2rem;">📚</span>
        <span style="font-weight:700; color:#0A58CA; font-size:0.95rem;">参考来源</span>
    </div>
    """, unsafe_allow_html=True)

    cols = st.columns(min(3, len(hits[:5])))
    for i, h in enumerate(hits[:5]):
        with cols[i % 3]:
            score = h.get("similarity", 0)
            if score > 0.8:
                score_color = "#2E7D32"
                score_bg = "#E8F5E9"
                score_label = "高相关"
            elif score > 0.6:
                score_color = "#F57F17"
                score_bg = "#FFF8E1"
                score_label = "中相关"
            else:
                score_color = "#C62828"
                score_bg = "#FFEBEE"
                score_label = "低相关"

            chapter = h.get("chapter", "")
            chapter_html = f'<div class="source-chapter">{_escape_html(chapter[:25])}</div>' if chapter else ""

            st.markdown(
                f'<div class="source-card fade-in-up">'
                f'<div class="source-header">'
                f'<div>'
                f'<div class="source-book">{_escape_html(h["book"][:20])}</div>'
                f'{chapter_html}'
                f'</div>'
                f'<span class="source-score" style="background:{score_bg}; color:{score_color};">{score_label} {score:.0%}</span>'
                f'</div>'
                f'<div class="source-text">{_escape_html(h["text"][:150])}…</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def render_source_cards_inline(hits: list[dict]):
    """渲染参考来源卡片（嵌入聊天气泡内的紧凑版本）。

    以可折叠的 expander 形式嵌入 AI 回答气泡内部，
    避免来源信息占用过多垂直空间。
    """
    if not hits:
        return

    with st.expander(f"📚 参考来源 ({len(hits)} 条)", expanded=False):
        for i, h in enumerate(hits):
            score = h.get("similarity", 0)
            if score > 0.8:
                score_color = "#2E7D32"
                score_bg = "#E8F5E9"
                score_label = "高相关"
            elif score > 0.6:
                score_color = "#F57F17"
                score_bg = "#FFF8E1"
                score_label = "中相关"
            else:
                score_color = "#C62828"
                score_bg = "#FFEBEE"
                score_label = "低相关"

            chapter = h.get("chapter", "")
            chapter_html = (
                f'<div class="source-chapter">{_escape_html(chapter[:30])}</div>' if chapter else ""
            )

            st.markdown(
                f'<div class="source-card-inline">'
                f'  <div class="source-header">'
                f'    <div>'
                f'      <div class="source-book">{_escape_html(h["book"][:25])}</div>'
                f'      {chapter_html}'
                f'    </div>'
                f'    <span class="source-score" style="background:{score_bg}; color:{score_color};">'
                f'      {score_label} {score:.0%}</span>'
                f'  </div>'
                f'  <div class="source-text">{_escape_html(h["text"][:200])}…</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
