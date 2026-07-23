"""医学教材知识库 — 病例分析模式

性能优化：教材数据按需加载，流式显示病例分析结果。
"""

import logging
from datetime import datetime

import streamlit as st

from config import MODELS, get_model_api_config
from search_engine import search
from ui_components import (
    fix_latex_formulas, load_selected_books,
    render_user_bubble, render_ai_bubble, render_empty_state,
)
from llm_utils import CASE_SYSTEM_PROMPT, build_case_message, call_llm_stream
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
                source_type="case",
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
    """渲染病例分析模式界面。"""
    st.markdown("输入病例描述，系统按临床推理流程分步分析。")

    # 检测设置页「重新搜索」触发的待搜索请求
    pending_q = consume_pending_search("病例")
    pending_trigger = False
    if pending_q:
        raw = pending_q.replace("[病例] ", "").rstrip("…")
        st.session_state["case_desc"] = raw
        pending_trigger = True

    case_desc = st.text_area("病例描述",
        placeholder="如：患者男，65岁，反复胸闷气促2年，加重伴双下肢水肿1周…",
        height=100, label_visibility="collapsed", key="case_desc")
    case_btn = st.button("🏥 开始分析", type="primary", use_container_width=True, key="case_btn")

    if (case_btn or pending_trigger) and case_desc.strip():
        # 从 session_state 实时读取 top_k/alpha，确保设置页修改立即生效
        current_top_k = st.session_state.get("top_k", top_k)
        current_alpha = st.session_state.get("alpha", alpha)
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else ALL_BOOKS
        embeddings, documents, metadatas = load_selected_books(books_to_load, manifest)
        if embeddings is None:
            st.error("未加载到教材数据"); st.stop()

        api_key, api_url, model_id = get_model_api_config(selected_model)

        with st.status("🔍 检索相关教材...", expanded=True) as status:
            hits = search(case_desc.strip(), embeddings, documents, metadatas, k=current_top_k, alpha=current_alpha)
            if hits:
                st.write(f"✅ 找到 {len(hits)} 条相关内容")
                status.update(label="✅ 检索完成", state="complete", expanded=False)
            else:
                status.update(label="⚠️ 未找到相关内容", state="complete", expanded=False)

        if hits:
            # 流式显示病例分析结果
            st.markdown("---")
            render_ai_bubble()
            user_msg = build_case_message(hits, case_desc.strip())

            case_placeholder = st.empty()
            case_ans = ""
            for chunk in call_llm_stream(api_key, user_msg, api_url=api_url, model=model_id, system_prompt=CASE_SYSTEM_PROMPT):
                case_ans += chunk
                case_placeholder.markdown(fix_latex_formulas(case_ans))

            st.session_state.hist.append({"q": f"[病例] {case_desc.strip()[:50]}…", "hits": hits, "a": case_ans, "model": MODELS[selected_model]["name"], "mode": "病例", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

            # 记录学习行为
            _record_learning(case_desc.strip(), topic="病例分析")
        else:
            st.warning("未找到相关教材内容，请补充更多病例信息。")

    # 显示病例历史
    case_items = [h for h in st.session_state.hist if h.get("mode") == "病例"]
    if case_items:
        st.markdown("---")
        for item in reversed(case_items):
            render_user_bubble(item["q"])
            if item["hits"]:
                with st.expander("📚 查看参考来源", expanded=False):
                    for h in item["hits"][:3]:
                        st.markdown(f"- **{h['book']}**·{h['chapter'][:20]}")
            fixed_case = fix_latex_formulas(item["a"])
            render_ai_bubble()
            st.markdown(fixed_case)
    else:
        render_empty_state(
            "输入病例描述开始分析",
            "系统按临床推理流程，分步分析病例并给出诊断建议",
            examples=[
                "患者男，65岁，反复胸闷气促2年",
                "患者女，45岁，多饮多尿多食1月",
                "患者男，30岁，发热咳嗽咳痰3天",
            ],
        )
