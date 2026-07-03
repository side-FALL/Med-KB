"""医学教材知识库 — 智能问答模式

性能优化：先显示检索结果（即时），再流式显示 AI 回答（异步）。
"""

import streamlit as st

from config import MODELS, get_model_api_config
from search_engine import search
from ui_components import (
    fix_latex_formulas, load_selected_books,
    render_user_bubble, render_ai_bubble, render_source_cards, render_empty_state,
)
from llm_utils import (
    COMPACT_SYSTEM_PROMPT, EXAM_SYSTEM_PROMPT,
    rewrite_query, build_user_message, call_llm_stream,
)


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
    """渲染智能问答模式界面。"""
    col_q, col_btn = st.columns([6, 1])
    with col_q:
        q = st.text_input("输入问题", value=st.session_state.get("q", ""),
            placeholder="如：心衰的病理机制、股三角的构成、疟原虫的生活史…",
            label_visibility="collapsed", key="q")
    with col_btn:
        search_btn = st.button("🔍 搜索", type="primary", use_container_width=True, key="qa_btn")

    exam_toggle = st.checkbox("⭐ 考点标注模式", value=False,
        help="开启后回答中标注高频考点、核心知识点、易混淆点",
        key="exam_toggle")
    prompt_to_use = EXAM_SYSTEM_PROMPT if exam_toggle else COMPACT_SYSTEM_PROMPT

    if (search_btn or q.strip()) and q.strip():
        # 加载选中教材的数据
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        with st.status("📚 正在加载教材数据...", expanded=False) as status:
            embeddings, documents, metadatas = load_selected_books(books_to_load, manifest)
            if embeddings is None:
                st.error("未加载到教材数据"); st.stop()
            status.update(label=f"✅ 已加载 {len(books_to_load)} 本教材", state="complete", expanded=False)

        conv_context = ""
        if use_context and st.session_state.conversation_turns:
            recent = st.session_state.conversation_turns[-3:]
            lines = []
            for turn_q, turn_a in recent:
                lines.append(f"用户: {turn_q[:100]}")
                lines.append(f"助手: {turn_a[:200]}")
            conv_context = "\n".join(lines)

        api_key, api_url, model_id = get_model_api_config(selected_model)

        search_query = q.strip()
        if use_context and st.session_state.conversation_turns:
            prev_queries = [t[0] for t in st.session_state.conversation_turns]
            rewritten = rewrite_query(search_query, prev_queries, api_key=api_key, api_url=api_url, model=model_id)
            if rewritten != search_query:
                search_query = rewritten
                st.info(f"🔄 结合上下文重写查询：{rewritten}")

        with st.status("🔍 正在检索相关内容...", expanded=True) as status:
            st.write("📝 文本向量化中...")
            hits = search(search_query, embeddings, documents, metadatas, k=top_k, alpha=alpha)
            if hits:
                st.write(f"✅ 找到 {len(hits)} 条相关内容")
                status.update(label="✅ 检索完成", state="complete", expanded=False)
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete", expanded=False)

        if hits:
            # 立即显示检索结果，用户无需等待 AI 回答
            render_source_cards(hits)

            # 流式显示 AI 回答
            st.markdown("---")
            render_ai_bubble()
            user_msg = build_user_message(hits, q.strip(), conv_context if use_context else "")

            # 使用 st.write_stream 实现实时流式输出
            ans_placeholder = st.empty()
            ans = ""
            for chunk in call_llm_stream(api_key, user_msg, api_url=api_url, model=model_id, system_prompt=prompt_to_use):
                ans += chunk
                ans_placeholder.markdown(fix_latex_formulas(ans))

            st.session_state.hist.append({"q": q.strip(), "hits": hits, "a": ans, "model": MODELS[selected_model]["name"], "mode": "问答"})
            if use_context:
                st.session_state.conversation_turns.append((q.strip(), ans))
        else:
            st.warning("未找到相关内容，请换个关键词试试。")
            st.session_state.hist.append({"q": q.strip(), "hits": [], "a": "未找到相关内容", "model": MODELS[selected_model]["name"], "mode": "问答"})

    # 显示问答历史（排除最后一条，因为它已在上方流式显示过）
    qa_items = [h for h in st.session_state.hist if h.get("mode", "问答") == "问答"]
    # 减去当前轮次已流式显示的回答：如果刚刚执行了搜索，最后一项已在上方实时展示
    display_items = qa_items[:-1] if (search_btn or q.strip()) and q.strip() and qa_items else qa_items
    if display_items:
        st.markdown("---")
        for item in reversed(display_items):
            render_user_bubble(item["q"])
            if item["hits"]:
                render_source_cards(item["hits"])
            fixed_answer = fix_latex_formulas(item["a"])
            render_ai_bubble(item.get("model", "AI"))
            st.markdown(fixed_answer)
    else:
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
