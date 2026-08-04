"""医学教材知识库 — 对比学习模式

性能优化：教材数据按需加载，流式显示对比结果。
"""

import logging
from datetime import datetime

import streamlit as st

from config import MODELS, get_model_api_config
from search_engine import search
from ui_components import (
    fix_latex_formulas, load_selected_books,
    render_user_bubble, render_ai_bubble, render_empty_state,
    render_markdown_export_button,
)
from llm_utils import COMPARE_SYSTEM_PROMPT, build_compare_message, call_llm_stream
from settings_components import consume_pending_search

logger = logging.getLogger(__name__)


def _record_learning(query: str, topic: str = "") -> None:
    """记录学习行为（登录用户和游客均记录），失败不影响主流程。"""
    try:
        manager = st.session_state.get("auth_data_manager")
        username = st.session_state.get("auth_username", "")
        if manager and username:
            manager.add_learning_record(
                username=username,
                topic=topic or query[:50],
                query=query,
                source_type="compare",
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
    """渲染对比学习模式界面。"""
    st.markdown("输入两个需要对比的医学概念，系统检索后生成结构化对比表。")

    # 检测设置页「重新搜索」触发的待搜索请求
    pending_q = consume_pending_search("对比")
    pending_trigger = False
    if pending_q:
        raw = pending_q.replace("[对比] ", "")
        if " vs " in raw:
            a, b = raw.split(" vs ", 1)
            st.session_state["cmp_a"] = a.strip()
            st.session_state["cmp_b"] = b.strip()
            pending_trigger = True

    col_a, col_b = st.columns(2)
    with col_a:
        concept_a = st.text_input("概念A", placeholder="如：青霉素", label_visibility="collapsed", key="cmp_a")
    with col_b:
        concept_b = st.text_input("概念B", placeholder="如：头孢菌素", label_visibility="collapsed", key="cmp_b")

    cmp_btn = st.button("🔄 开始对比", type="primary", use_container_width=True, key="cmp_btn")

    if (cmp_btn or pending_trigger) and concept_a.strip() and concept_b.strip():
        # 从 session_state 实时读取 top_k/alpha，确保设置页修改立即生效
        current_top_k = st.session_state.get("top_k", top_k)
        current_alpha = st.session_state.get("alpha", alpha)
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        embeddings, documents, metadatas = load_selected_books(books_to_load, manifest)
        if embeddings is None:
            st.error("未加载到教材数据"); st.stop()

        api_key, api_url, model_id = get_model_api_config(selected_model)

        with st.status("🔍 分别检索两个概念...", expanded=True) as status:
            hits_a = search(concept_a.strip(), embeddings, documents, metadatas, k=current_top_k, alpha=current_alpha)
            hits_b = search(concept_b.strip(), embeddings, documents, metadatas, k=current_top_k, alpha=current_alpha)
            if hits_a or hits_b:
                st.write(f"✅ {concept_a} 找到 {len(hits_a)} 条，{concept_b} 找到 {len(hits_b)} 条")
                status.update(label="✅ 检索完成", state="complete", expanded=False)
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete", expanded=False)

        if hits_a or hits_b:
            # 流式显示对比结果
            st.markdown("---")
            render_ai_bubble()
            user_msg = build_compare_message(hits_a, hits_b, concept_a.strip(), concept_b.strip())

            cmp_placeholder = st.empty()
            cmp_ans = ""
            for chunk in call_llm_stream(api_key, user_msg, api_url=api_url, model=model_id, system_prompt=COMPARE_SYSTEM_PROMPT):
                cmp_ans += chunk
                cmp_placeholder.markdown(fix_latex_formulas(cmp_ans))

            all_hits = (hits_a or []) + (hits_b or [])
            st.session_state.hist.append({"q": f"[对比] {concept_a} vs {concept_b}", "hits": all_hits, "a": cmp_ans, "model": MODELS[selected_model]["name"], "mode": "对比", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

            # 记录学习行为
            _record_learning(f"{concept_a} vs {concept_b}", topic=f"{concept_a} vs {concept_b}")

            # 导出 Markdown 按钮
            render_markdown_export_button(
                cmp_ans, question=f"{concept_a} vs {concept_b}", mode_label="对比",
                key=f"export_md_cmp_new_{datetime.now().strftime('%H%M%S%f')}",
            )
        else:
            st.warning("未找到相关教材内容，请换个概念试试。")

    # 显示对比历史
    cmp_items = [h for h in st.session_state.hist if h.get("mode") == "对比"]
    if cmp_items:
        st.markdown("---")
        for i, item in enumerate(reversed(cmp_items)):
            render_user_bubble(item["q"])
            fixed_cmp = fix_latex_formulas(item["a"])
            render_ai_bubble()
            st.markdown(fixed_cmp)
            render_markdown_export_button(
                item["a"], question=item["q"], mode_label="对比",
                key=f"export_md_cmp_hist_{i}",
            )
    else:
        render_empty_state(
            "输入两个概念开始对比学习",
            "系统检索两个概念的相关内容，生成结构化对比表格",
            examples=[
                "1型糖尿病 vs 2型糖尿病",
                "青霉素 vs 头孢菌素",
                "良性肿瘤 vs 恶性肿瘤",
                "动脉 vs 静脉",
                "交感神经 vs 副交感神经",
            ],
        )
