"""医学教材知识库 — 智能体模式（聊天式布局）

使用 Streamlit 原生 st.chat_message / st.chat_input 实现对话式交互：
- 用户消息右对齐、AI 回答左对齐
- 思考过程折叠面板嵌入 AI 气泡内部
- 输入框固定在页面底部
"""

import logging

import streamlit as st

from config import MODELS, get_model_api_config
from ui_components import fix_latex_formulas
from agent_lite import run_agent, run_agent_stream

# 导入工具层的教材选择函数
try:
    from tools import set_selected_books
except ImportError:
    set_selected_books = lambda x: None

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
                source_type="agent",
            )
    except Exception as exc:
        logger.warning("记录学习行为失败: %s", exc)


def render(
    ALL_BOOKS: list[str],
    book_count: int,
    scope: str,
    selected_model: str,
    selected_books: list[str],
):
    """渲染智能体模式界面 — 聊天式布局。"""
    st.markdown("🤖 **智能体模式**：AI会自动选择工具（搜索教材、计算剂量、查询正常值、对比概念、病例分析）来回答你的问题。支持多轮对话。")

    if run_agent is None:
        st.error("⚠️ 智能体模块加载失败，请检查依赖是否安装正确。")
        return

    # 初始化对话历史
    if "agent_turns" not in st.session_state:
        st.session_state.agent_turns = []

    # ── 历史轮次统计 + 清空按钮（辅助位置，不占主输入区）──
    turns_count = len(st.session_state.agent_turns)
    if turns_count > 0:
        col_info, col_clear = st.columns([5, 1])
        with col_info:
            st.caption(f"💬 已进行 {turns_count} 轮对话")
        with col_clear:
            if st.button("🗑️ 清空对话", use_container_width=True, key="clear_agent"):
                st.session_state.agent_turns = []
                st.rerun()

    # ── 显示历史对话 ────────────────────────────────────
    if st.session_state.agent_turns:
        for q, a, steps in st.session_state.agent_turns:
            # 用户消息
            with st.chat_message("user", avatar="👤"):
                st.markdown(q)

            # AI 回答
            with st.chat_message("assistant", avatar="🤖"):
                if steps:
                    with st.expander(f"🔍 查看思考过程（{len(steps)} 步）", expanded=False):
                        for j, step in enumerate(steps):
                            st.markdown(f"**步骤 {j+1}:** `{step['tool']}` → {step['output'][:100]}...")
                st.markdown(fix_latex_formulas(a))
    else:
        # 空对话引导提示
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

    # ── 底部输入框（st.chat_input 自动固定在页面底部）──
    agent_input = st.chat_input(
        placeholder="如：心衰患者用呋塞米的剂量是多少？白细胞正常值是多少？",
        key="agent_chat_input",
    )

    # ── 处理新输入 ─────────────────────────────────────
    if agent_input and agent_input.strip():
        _process_agent_query(
            agent_input.strip(),
            ALL_BOOKS, book_count,
            scope, selected_model, selected_books,
        )


def _process_agent_query(
    query: str,
    ALL_BOOKS: list[str],
    book_count: int,
    scope: str,
    selected_model: str,
    selected_books: list[str],
):
    """处理智能体查询并渲染对话。"""

    # 设置智能体可搜索的教材范围
    books_to_load = selected_books if scope == "选择教材" and len(selected_books) < book_count else None
    set_selected_books(books_to_load)

    api_key, api_url, model_id = get_model_api_config(selected_model)

    # 用户消息气泡
    with st.chat_message("user", avatar="👤"):
        st.markdown(query)

    # AI 回答气泡 —— 所有内容在本次渲染中直接写入：
    # - 状态条固定折叠，仅通过 label 原地更新进度（容器高度不变）
    # - 最终回答通过单一占位符原地刷新
    # - 思考过程面板在状态完成后一次性渲染
    # - 结束后不再 st.rerun()，避免整页重绘抖动
    with st.chat_message("assistant", avatar="🤖"):
        status = st.status("🤖 智能体思考中...", expanded=False)
        # 思考过程面板的占位（位于状态条与最终回答之间，稍后一次性填充）
        # 占位符在整个生命周期中始终存在，避免完成后插入新元素导致布局跳动
        expander_slot = st.empty()
        # 最终回答的单一占位符
        answer_slot = st.empty()

        if run_agent_stream is not None:
            # ── 流式输出模式 ──
            steps = []
            final_answer = ""
            has_error = False
            # 流式批量化缓冲：累积到一定字符数或遇到句读/换行时才刷新占位符，
            # 减少 answer_slot.markdown() 的调用频率以降低重渲染抖动
            pending_tokens = ""
            TOKEN_BATCH_SIZE = 20
            TOKEN_FLUSH_CHARS = ("。", "！", "？", "\n")

            try:
                for event in run_agent_stream(
                    query, api_key, model=model_id, api_url=api_url,
                    conversation_history=[(q, a) for q, a, _ in st.session_state.agent_turns]
                ):
                    if event["type"] == "step":
                        # 推理步骤仅记录，不更新状态条标签
                        # （状态条仅在 开始 → 思考中 → 完成/出错 三个关键节点更新）
                        steps.append(event["data"])
                    elif event["type"] == "token":
                        final_answer += event["data"]
                        pending_tokens += event["data"]
                        if len(pending_tokens) >= TOKEN_BATCH_SIZE or any(
                            ch in pending_tokens for ch in TOKEN_FLUSH_CHARS
                        ):
                            answer_slot.markdown(fix_latex_formulas(final_answer) + " ▌")
                            pending_tokens = ""
                    elif event["type"] == "error":
                        status.update(label="❌ 出错了", state="error", expanded=False)
                        answer_slot.error(f"智能体执行出错：{event['data']}")
                        has_error = True
                        break
            except Exception as exc:
                status.update(label="❌ 出错了", state="error", expanded=False)
                answer_slot.error(f"智能体执行出错：{exc}")
                has_error = True

            if not has_error:
                status.update(label="✅ 智能体完成", state="complete", expanded=False)
                if final_answer:
                    answer_slot.markdown(fix_latex_formulas(final_answer))

                st.session_state.agent_turns.append((query, final_answer, steps))
                st.session_state.hist.append({
                    "q": f"[智能体] {query}",
                    "hits": [],
                    "a": final_answer,
                    "model": MODELS[selected_model]["name"],
                    "mode": "智能体"
                })

                # 记录学习行为（每轮对话完成时记录一次）
                if final_answer:
                    _record_learning(query, topic="智能体对话")
        else:
            # ── 降级到非流式模式 ──
            result = run_agent(
                query, api_key, model=model_id, api_url=api_url,
                conversation_history=[(q, a) for q, a, _ in st.session_state.agent_turns],
            )

            if result["error"]:
                status.update(label="❌ 出错了", state="error", expanded=False)
                answer_slot.error(f"智能体执行出错：{result['error']}")
            else:
                status.update(label="✅ 智能体完成", state="complete", expanded=False)
                if result["output"]:
                    answer_slot.markdown(fix_latex_formulas(result["output"]))

            steps = result.get("steps", [])
            has_error = bool(result["error"])
            st.session_state.agent_turns.append((query, result.get("output", ""), steps))
            st.session_state.hist.append({
                "q": f"[智能体] {query}",
                "hits": [],
                "a": result.get("output", ""),
                "model": MODELS[selected_model]["name"],
                "mode": "智能体"
            })

            # 记录学习行为（每轮对话完成时记录一次）
            if not has_error and result.get("output"):
                _record_learning(query, topic="智能体对话")

        # 思考过程折叠面板：状态完成后一次性渲染（嵌入气泡内部）
        if steps and not has_error:
            with expander_slot.container():
                with st.expander(f"🔍 查看思考过程（{len(steps)} 步）", expanded=False):
                    for i, step in enumerate(steps):
                        st.markdown(f"**步骤 {i+1}:** `{step['tool']}`")
                        st.markdown(f"输入: `{step['input']}`")
                        st.markdown(f"输出:\n```\n{step['output'][:300]}\n```")
