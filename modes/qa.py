"""医学教材知识库 — 智能问答模式（聊天式布局）

使用 Streamlit 原生 st.chat_message / st.chat_input 实现对话式交互：
- 用户消息右对齐、蓝色气泡
- AI 回答左对齐、白色气泡，参考来源卡片嵌入气泡内
- 输入框固定在页面底部
- 新消息自动滚动到底部
"""

import logging
from datetime import datetime

import streamlit as st

from config import MODELS, get_model_api_config
from search_engine import search
from ui_components import (
    fix_latex_formulas, load_selected_books,
    render_source_cards_inline, render_empty_state,
    render_markdown_export_button,
)
from llm_utils import (
    COMPACT_SYSTEM_PROMPT, EXAM_SYSTEM_PROMPT,
    rewrite_query, build_user_message, call_llm_stream,
)
from settings_components import consume_pending_search

logger = logging.getLogger(__name__)


def _record_learning(query: str, topic: str = "") -> None:
    """记录学习行为（仅登录用户），失败不影响主流程。"""
    if st.session_state.get("auth_mode_type") == "guest":
        return
    try:
        manager = st.session_state.get("auth_data_manager")
        username = st.session_state.get("auth_username", "")
        if manager and username:
            manager.add_learning_record(
                username=username,
                topic=topic or query[:50],
                query=query,
                source_type="qa",
            )
    except Exception as exc:
        logger.warning("记录学习行为失败: %s", exc)


def render(
    manifest: dict,
    ALL_BOOKS: list[str],
    book_count: int,
    top_k: int,
    alpha: float,
    use_context: bool,
    scope: str,
    selected_model: str,
    selected_books: list[str],
):
    """渲染智能问答模式界面 — 聊天式布局。"""

    # ── 考点模式切换（紧凑横排）────────────────────────
    exam_toggle = st.checkbox(
        "⭐ 考点标注模式", value=False,
        help="开启后回答中标注高频考点、核心知识点、易混淆点",
        key="exam_toggle",
    )
    prompt_to_use = EXAM_SYSTEM_PROMPT if exam_toggle else COMPACT_SYSTEM_PROMPT

    # ── 提取问答历史 ──────────────────────────────────
    qa_items = [h for h in st.session_state.hist if h.get("mode") == "问答"]

    # ── 聊天历史区域 ──────────────────────────────────
    if qa_items:
        for i, item in enumerate(qa_items):
            # 用户消息
            with st.chat_message("user", avatar="👤"):
                st.markdown(item["q"])

            # AI 回答
            with st.chat_message("assistant", avatar="🤖"):
                if item.get("hits"):
                    render_source_cards_inline(item["hits"])
                st.markdown(fix_latex_formulas(item["a"]))
                st.caption(f"模型: {item.get('model', 'AI')}")
                render_markdown_export_button(
                    item["a"], question=item["q"], mode_label="问答",
                    key=f"export_md_qa_hist_{i}",
                )
    else:
        # 空状态引导
        render_empty_state(
            "输入任何医学问题，AI 从教材中检索回答",
            "基于权威医学教材，为您提供准确、专业的解答",
            examples=[
                "心衰的病理机制",
                "股三角的构成",
                "疟原虫的生活史",
                "糖尿病的分型",
                "抗生素的作用机制",
            ],
        )

    # ── 底部输入框（st.chat_input 自动固定在页面底部）──
    user_input = st.chat_input(
        placeholder="输入医学问题，如：心衰的病理机制、股三角的构成…",
        key="qa_chat_input",
    )

    # ── 处理新输入 ────────────────────────────────────
    if user_input and user_input.strip():
        # 从 session_state 实时读取 top_k/alpha/use_context，确保设置页修改立即生效
        current_top_k = st.session_state.get("top_k", top_k)
        current_alpha = st.session_state.get("alpha", alpha)
        current_use_context = st.session_state.get("use_context", use_context)
        _process_query(
            user_input.strip(),
            manifest, ALL_BOOKS, book_count,
            current_top_k, current_alpha, current_use_context,
            scope, selected_model, selected_books,
            prompt_to_use,
        )

    # 检测设置页「重新搜索」触发的待搜索请求
    pending_q = consume_pending_search("问答")
    if pending_q:
        current_top_k = st.session_state.get("top_k", top_k)
        current_alpha = st.session_state.get("alpha", alpha)
        current_use_context = st.session_state.get("use_context", use_context)
        _process_query(
            pending_q,
            manifest, ALL_BOOKS, book_count,
            current_top_k, current_alpha, current_use_context,
            scope, selected_model, selected_books,
            prompt_to_use,
        )


def _process_query(
    query: str,
    manifest: dict,
    ALL_BOOKS: list[str],
    book_count: int,
    top_k: int,
    alpha: float,
    use_context: bool,
    scope: str,
    selected_model: str,
    selected_books: list[str],
    prompt_to_use: str,
):
    """处理用户查询并渲染对话。"""

    # 用户消息气泡
    with st.chat_message("user", avatar="👤"):
        st.markdown(query)

    # AI 回答气泡
    with st.chat_message("assistant", avatar="🤖"):
        # 加载教材
        with st.status("📚 正在加载教材数据...", expanded=False) as status:
            books_to_load = (
                selected_books
                if scope == "选择教材" and len(selected_books) < book_count
                else ALL_BOOKS
            )
            embeddings, documents, metadatas = load_selected_books(
                books_to_load, manifest,
            )
            if embeddings is None:
                st.error("未加载到教材数据")
                st.stop()
            st.write(f"已加载 {len(books_to_load)} 本教材，共 {len(documents)} 个文本块")
            status.update(
                label=f"✅ 已加载 {len(books_to_load)} 本教材",
                state="complete", expanded=False,
            )

        # 多轮对话上下文
        conv_context = ""
        if use_context and st.session_state.conversation_turns:
            recent = st.session_state.conversation_turns[-3:]
            lines = []
            for turn_q, turn_a in recent:
                lines.append(f"用户: {turn_q[:100]}")
                lines.append(f"助手: {turn_a[:200]}")
            conv_context = "\n".join(lines)

        api_key, api_url, model_id = get_model_api_config(selected_model)

        # 查询重写（结合上下文）
        search_query = query
        if use_context and st.session_state.conversation_turns:
            prev_queries = [t[0] for t in st.session_state.conversation_turns]
            rewritten = rewrite_query(
                search_query, prev_queries,
                api_key=api_key, api_url=api_url, model=model_id,
            )
            if rewritten != search_query:
                search_query = rewritten

        # 语义检索
        with st.status("🔍 正在检索相关内容...", expanded=False) as status:
            hits = search(
                search_query, embeddings, documents, metadatas,
                k=top_k, alpha=alpha,
            )
            if hits:
                st.write(f"找到 {len(hits)} 条相关内容：")
                for i, hit in enumerate(hits, 1):
                    st.write(f"{i}. {hit['book']} — {hit['text'][:80]}...")
                status.update(
                    label=f"✅ 找到 {len(hits)} 条相关内容",
                    state="complete", expanded=False,
                )
            else:
                st.write("未找到与查询相关的内容")
                status.update(
                    label="⚠️ 未找到相关内容",
                    state="complete", expanded=False,
                )

        if hits:
            # 参考来源卡片（嵌入气泡内，可折叠）
            render_source_cards_inline(hits)

            # 流式输出 AI 回答
            user_msg = build_user_message(
                hits, query,
                conv_context if use_context else "",
            )
            ans_placeholder = st.empty()
            ans = ""
            for chunk in call_llm_stream(
                api_key, user_msg,
                api_url=api_url, model=model_id,
                system_prompt=prompt_to_use,
            ):
                ans += chunk
                ans_placeholder.markdown(fix_latex_formulas(ans))

            # 保存到历史
            st.session_state.hist.append({
                "q": query,
                "hits": hits,
                "a": ans,
                "model": MODELS[selected_model]["name"],
                "mode": "问答",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            if use_context:
                st.session_state.conversation_turns.append((query, ans))

            # 记录学习行为
            _record_learning(query)

            # 导出 Markdown 按钮
            render_markdown_export_button(
                ans, question=query, mode_label="问答",
                key=f"export_md_qa_new_{datetime.now().strftime('%H%M%S%f')}",
            )

        else:
            st.warning("未找到相关内容，请换个关键词试试。")
            st.session_state.hist.append({
                "q": query,
                "hits": [],
                "a": "未找到相关内容",
                "model": MODELS[selected_model]["name"],
                "mode": "问答",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
