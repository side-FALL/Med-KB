"""医学教材知识库 — 智能体模式"""

import streamlit as st

from config import MODELS, get_model_api_config
from ui_components import fix_latex_formulas
from agent_lite import run_agent, run_agent_stream

# 导入工具层的教材选择函数
try:
    from tools import set_selected_books
except ImportError:
    set_selected_books = lambda x: None


def render(
    ALL_BOOKS: list[str],
    book_count: int,
    scope: str,
    selected_model: str,
    selected_books: list[str],
):
    """渲染智能体模式界面。"""
    st.markdown("🤖 **智能体模式**：AI会自动选择工具（搜索教材、计算剂量、查询正常值、对比概念、病例分析）来回答你的问题。支持多轮对话。")

    if run_agent is None:
        st.error("⚠️ 智能体模块加载失败，请检查依赖是否安装正确。")
        return

    # 初始化对话历史
    if "agent_turns" not in st.session_state:
        st.session_state.agent_turns = []

    # 清空对话按钮
    col_agent_header, col_agent_clear = st.columns([4, 1])
    with col_agent_header:
        turns_count = len(st.session_state.agent_turns)
        if turns_count > 0:
            st.caption(f"💬 已进行 {turns_count} 轮对话")
    with col_agent_clear:
        if st.button("🗑️ 清空对话", use_container_width=True, key="clear_agent"):
            st.session_state.agent_turns = []
            st.rerun()

    # 显示历史对话
    for i, (q, a, steps) in enumerate(st.session_state.agent_turns):
        st.markdown(f'<div class="user-bubble">❓ {q}</div>', unsafe_allow_html=True)
        if steps:
            with st.expander(f"🔍 查看思考过程（{len(steps)} 步）", expanded=False):
                for j, step in enumerate(steps):
                    st.markdown(f"**步骤 {j+1}:** `{step['tool']}` → {step['output'][:100]}...")
        fixed_a = fix_latex_formulas(a)
        st.markdown(f'<div class="ai-bubble"></div>', unsafe_allow_html=True)
        st.markdown(fixed_a)

    # 输入区域
    col_agent_q, col_agent_btn = st.columns([6, 1])
    with col_agent_q:
        agent_query = st.text_input("输入问题",
            placeholder="如：心衰患者用呋塞米的剂量是多少？白细胞正常值是多少？",
            label_visibility="collapsed", key="agent_query")
    with col_agent_btn:
        agent_btn = st.button("🤖 发送", type="primary", use_container_width=True, key="agent_btn")

    if agent_btn and agent_query.strip():
        query_text = agent_query.strip()

        # 设置智能体可搜索的教材范围
        books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else None
        set_selected_books(books_to_load)

        api_key, api_url, model_id = get_model_api_config(selected_model)

        with st.status("🤖 智能体思考中...", expanded=True) as status:
            st.write("🧠 正在分析问题并选择工具...")

            if run_agent_stream is not None:
                # 流式输出模式
                steps = []
                final_answer = ""
                has_error = False

                for event in run_agent_stream(
                    query_text, api_key, model=model_id, api_url=api_url,
                    conversation_history=[(q, a) for q, a, _ in st.session_state.agent_turns]
                ):
                    if event["type"] == "step":
                        steps.append(event["data"])
                        st.write(f"🔧 使用工具: `{event['data']['tool']}`")
                    elif event["type"] == "token":
                        final_answer += event["data"]
                    elif event["type"] == "error":
                        status.update(label="❌ 出错了", state="error", expanded=False)
                        st.error(f"智能体执行出错：{event['data']}")
                        has_error = True
                        break

                if not has_error:
                    status.update(label="✅ 智能体完成", state="complete", expanded=False)

                    if steps:
                        with st.expander(f"🔍 查看思考过程（{len(steps)} 步）", expanded=False):
                            for i, step in enumerate(steps):
                                st.markdown(f"**步骤 {i+1}:** `{step['tool']}`")
                                st.markdown(f"输入: `{step['input']}`")
                                st.markdown(f"输出:\n```\n{step['output'][:300]}\n```")

                    if final_answer:
                        fixed_output = fix_latex_formulas(final_answer)
                        st.markdown(fixed_output)

                    st.session_state.agent_turns.append((query_text, final_answer, steps))
                    st.session_state.hist.append({
                        "q": f"[智能体] {query_text}",
                        "hits": [],
                        "a": final_answer,
                        "model": MODELS[selected_model]["name"],
                        "mode": "智能体"
                    })
            else:
                # 降级到非流式模式
                result = run_agent(query_text, api_key, model=model_id, api_url=api_url)

                if result["error"]:
                    status.update(label="❌ 出错了", state="error", expanded=False)
                    st.error(f"智能体执行出错：{result['error']}")
                else:
                    status.update(label="✅ 智能体完成", state="complete", expanded=False)

                    if result["steps"]:
                        with st.expander(f"🔍 查看思考过程（{len(result['steps'])} 步）", expanded=False):
                            for i, step in enumerate(result["steps"]):
                                st.markdown(f"**步骤 {i+1}:** `{step['tool']}`")
                                st.markdown(f"输入: `{step['input']}`")
                                st.markdown(f"输出:\n```\n{step['output'][:300]}\n```")

                    fixed_output = fix_latex_formulas(result["output"])
                    st.markdown(fixed_output)

                st.session_state.agent_turns.append((query_text, result.get("output", ""), result.get("steps", [])))
                st.session_state.hist.append({
                    "q": f"[智能体] {query_text}",
                    "hits": [],
                    "a": result.get("output", ""),
                    "model": MODELS[selected_model]["name"],
                    "mode": "智能体"
                })

        st.rerun()

    # 空对话提示
    if not st.session_state.agent_turns:
        from ui_components import render_empty_state
        render_empty_state(
            "输入问题开始智能体对话",
            "AI 会自动选择工具（搜索教材、计算剂量、查询正常值等）来回答您的问题",
            examples=[
                "阿莫西林 70kg 成人用量",
                "肌酐正常值是多少",
                "对比青霉素和头孢菌素",
            ],
        )
