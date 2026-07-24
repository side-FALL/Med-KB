"""医学教材知识库 — 自测刷题模式

性能优化：教材数据按需加载，流式显示 AI 出题结果。
"""

import logging
import re
from datetime import datetime

import streamlit as st

from config import MODELS, get_model_api_config
from search_engine import search
from ui_components import (
    fix_latex_formulas, load_selected_books, render_empty_state,
    render_markdown_export_button,
)
from llm_utils import QUIZ_SYSTEM_PROMPT, build_quiz_message, call_llm_stream

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
                source_type="quiz",
            )
    except Exception as exc:
        logger.warning("记录学习行为失败: %s", exc)


def render(
    manifest: dict,
    ALL_BOOKS: list[str],
    book_count: int,
    top_k: int,
    alpha: float,
    scope: str,
    selected_model: str,
    selected_books: list[str],
):
    """渲染自测刷题模式界面。"""
    st.markdown("输入知识点主题，系统从教材中检索相关内容并生成5道题。")

    col_topic, col_btn = st.columns([4, 1])
    with col_topic:
        quiz_topic = st.text_input("知识点主题",
            placeholder="如：心力衰竭、抗生素分类、疟原虫生活史…",
            label_visibility="collapsed", key="quiz_topic")
    with col_btn:
        quiz_btn = st.button("📝 出题", type="primary", use_container_width=True, key="quiz_btn")

    if quiz_btn and quiz_topic.strip():
        # 从 session_state 实时读取 top_k/alpha，确保设置页修改立即生效
        current_top_k = st.session_state.get("top_k", top_k)
        current_alpha = st.session_state.get("alpha", alpha)
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        embeddings, documents, metadatas = load_selected_books(books_to_load, manifest)
        if embeddings is None:
            st.error("未加载到教材数据"); st.stop()

        api_key, api_url, model_id = get_model_api_config(selected_model)

        with st.status("🔍 检索教材并出题...", expanded=True) as status:
            hits = search(quiz_topic.strip(), embeddings, documents, metadatas, k=current_top_k, alpha=current_alpha)
            if hits:
                user_msg = build_quiz_message(hits, quiz_topic.strip())
                status.update(label="✅ 检索完成，正在生成 5 道题目...", state="complete", expanded=False)

                # 流式输出题目
                quiz_placeholder = st.empty()
                quiz_raw = ""
                for chunk in call_llm_stream(api_key, user_msg, api_url=api_url, model=model_id, system_prompt=QUIZ_SYSTEM_PROMPT):
                    quiz_raw += chunk
                    quiz_placeholder.markdown(fix_latex_formulas(quiz_raw))
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete", expanded=False)
                quiz_raw = ""
                st.warning("未找到相关教材内容，请换个主题试试。")

        if quiz_raw:
            # 记录学习行为
            _record_learning(quiz_topic.strip(), topic=quiz_topic.strip())

            questions, answers = [], []
            parts = re.split(r'===题目\d+===', quiz_raw)
            ans_parts = re.split(r'===答案\d+===', quiz_raw)
            for i in range(1, len(ans_parts)):
                answers.append(ans_parts[i].strip())
            for i in range(1, len(parts)):
                q_text = parts[i].split('===答案')[0].strip() if '===答案' in parts[i] else parts[i].strip()
                questions.append(q_text)

            st.session_state.quiz_questions = questions
            st.session_state.quiz_answers = answers
            st.session_state.quiz_topic_display = quiz_topic.strip()
            st.session_state.quiz_revealed = [False] * len(questions)

    # 显示题目
    if "quiz_questions" in st.session_state and st.session_state.quiz_questions:
        st.markdown(f"### 📝 {st.session_state.quiz_topic_display}")
        for i, q in enumerate(st.session_state.quiz_questions):
            fixed_q = fix_latex_formulas(q)
            st.markdown(f'<div class="ai-bubble"><strong>第 {i+1} 题</strong></div>', unsafe_allow_html=True)
            st.markdown(fixed_q)
            if st.session_state.quiz_revealed[i]:
                if i < len(st.session_state.quiz_answers):
                    fixed_ans = fix_latex_formulas(st.session_state.quiz_answers[i])
                    st.markdown(f'<div style="background:#e8f5e9;border-radius:10px;padding:1rem;margin:0.5rem 0;border-left:4px solid #4caf50;"></div>', unsafe_allow_html=True)
                    st.markdown(fixed_ans)
            else:
                if st.button(f"👁️ 显示第 {i+1} 题答案", key=f"reveal_{i}"):
                    st.session_state.quiz_revealed[i] = True
                    st.rerun()
        if not all(st.session_state.quiz_revealed):
            if st.button("👁️ 显示全部答案", key="reveal_all"):
                st.session_state.quiz_revealed = [True] * len(st.session_state.quiz_questions)
                st.rerun()

        # 导出为 Markdown：拼接题目与答案的原始内容
        export_parts = []
        for i, q in enumerate(st.session_state.quiz_questions):
            ans = st.session_state.quiz_answers[i] if i < len(st.session_state.quiz_answers) else ""
            export_parts.append(f"## 第 {i+1} 题\n\n{q}\n\n**答案**\n\n{ans}")
        render_markdown_export_button(
            "\n\n---\n\n".join(export_parts),
            question=f"自测刷题：{st.session_state.quiz_topic_display}",
            mode_label="刷题",
            key=f"export_md_quiz_{datetime.now().strftime('%H%M%S%f')}",
        )
    else:
        render_empty_state(
            "输入知识点主题开始刷题",
            "系统从教材中检索相关内容，自动生成 5 道测试题",
            examples=[
                "心力衰竭",
                "抗生素药理",
                "糖尿病分型",
                "股三角解剖",
                "疟原虫生活史",
            ],
        )
