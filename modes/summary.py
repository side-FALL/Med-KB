"""医学教材知识库 - 重点总结模式（需求③ P2）

流程：上传重点文档（.txt/.md）-> 解析三板块 -> 选教材 -> 逐条检索 -> LLM 分板块总结 -> 渲染+导出

三板块：
- translation 英汉互译（表格汇总 + 逐条详情）
- terms 名词解释（条目卡片）
- qa 简答题（条目卡片）

每条结果附出处（教材名·章节名），检索为空标注"教材中未找到相关内容"。
游客与登录用户均可用，均记录学习行为。
遵守已知坑：不调用多余 st.rerun()；结果展示优先 st.dataframe；
不用 contain:layout size；可展开区域不用 overflow:hidden。
"""

import logging
from datetime import datetime

import pandas as pd
import streamlit as st

from config import MODELS, get_model_api_config
from llm_utils import (
    SUMMARY_SECTION_TRANSLATION,
    SUMMARY_SECTION_TERMS,
    SUMMARY_SECTION_QA,
    SUMMARY_SYSTEM_PROMPT,
    build_summary_message,
    call_llm_stream,
    generate_summary_for_section,
)
from search_engine import search, search_with_qvec, get_embeddings_batch
from ui_components import (
    fix_latex_formulas,
    load_selected_books,
    render_empty_state,
    render_markdown_export_button,
)
from modes.summary_parser import parse_summary_document

logger = logging.getLogger(__name__)

# 重点总结模式每条检索返回数（top_k 3~5，取 5 兼顾覆盖与性能）
_SUMMARY_SEARCH_K = 5

# 上传文件大小上限（100KB，与解析器一致，双层防御）
_MAX_UPLOAD_BYTES = 100 * 1024

# 三板块元信息：(parsed_key, section_type 常量, 中文标签, 是否用表格展示)
_SECTION_META = [
    ("translation", SUMMARY_SECTION_TRANSLATION, "🔤 英汉互译", True),
    ("terms", SUMMARY_SECTION_TERMS, "📖 名词解释", False),
    ("qa", SUMMARY_SECTION_QA, "✏️ 简答题", False),
]


def _record_learning(topic: str, query: str = "") -> None:
    """记录学习行为（登录用户和游客均记录），失败不影响主流程。"""
    try:
        manager = st.session_state.get("auth_data_manager")
        username = st.session_state.get("auth_username", "")
        if manager and username:
            manager.add_learning_record(
                username=username,
                topic=topic,
                query=query or topic,
                source_type="summary",
            )
    except Exception as exc:
        logger.warning("记录学习行为失败: %s", exc)


# ── 纯函数（可单测，不依赖 streamlit）──────────────────────

def _extract_source(hits: list[dict]) -> str:
    """从检索结果提取出处（教材名·章节名），空则返回未找到提示。"""
    if not hits:
        return "教材中未找到相关内容"
    top = hits[0]
    book = top.get("book", "未知教材")
    chapter = top.get("chapter", "未知章节")
    return f"{book}·{chapter}"


def _collect_items_with_hits(
    items: list[str],
    search_fn,
    k: int = _SUMMARY_SEARCH_K,
    progress_cb=None,
) -> list[dict]:
    """对每个条目调用检索，返回 items_with_hits 列表。

    Args:
        items: 条目文本列表。
        search_fn: 检索函数，签名 search_fn(text, k=k) -> list[dict]。
        k: 每条检索返回数。
        progress_cb: 可选进度回调 progress_cb(done, total)。

    Returns:
        [{"item": str, "hits": list[dict]}]
    """
    result = []
    total = len(items)
    for i, item in enumerate(items):
        hits = search_fn(item, k=k)
        result.append({"item": item, "hits": hits})
        if progress_cb:
            progress_cb(i + 1, total)
    return result


def _merge_results_with_sources(
    llm_results: list[dict],
    items_with_hits: list[dict],
) -> list[dict]:
    """把 LLM 解析结果与检索出处合并，确保每条含 source 字段。

    按 item 文本匹配对应的检索结果；匹配失败时用首个条目的出处作降级。
    """
    if not items_with_hits:
        fallback_source = "教材中未找到相关内容"
    else:
        fallback_source = _extract_source(items_with_hits[0]["hits"])

    merged = []
    for res in llm_results:
        item_text = res.get("item", "")
        matched = next(
            (iwh for iwh in items_with_hits if iwh["item"] == item_text),
            None,
        )
        if matched is not None:
            source = _extract_source(matched["hits"])
        else:
            source = fallback_source
        merged.append({
            "item": item_text,
            "result": res.get("result", ""),
            "source": source,
        })
    return merged


def _preview(text: str, max_len: int = 60) -> str:
    """截取文本预览（单行，超长省略）。"""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _build_export_markdown(results: dict, book_name: str) -> str:
    """把三板块结果拼成导出 Markdown 字符串。

    Args:
        results: {"translation": [...], "terms": [...], "qa": [...]}
                 每项 {"item", "result", "source"}
        book_name: 选定教材名。

    Returns:
        Markdown 字符串（含三板块标题与条目）。
    """
    lines = [f"# 重点总结 -《{book_name}》", ""]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"> 导出自 医学教材知识库 · {ts}")
    lines += ["", "---", ""]

    for section_key, _stype, label, _use_table in _SECTION_META:
        items = results.get(section_key, [])
        lines.append(f"## {label}")
        lines.append("")
        if not items:
            lines.append("_（本板块无条目）_")
            lines += ["", "---", ""]
            continue
        for i, entry in enumerate(items, 1):
            lines.append(f"### {i}. {entry.get('item', '')}")
            lines.append("")
            lines.append(entry.get("result", ""))
            lines.append("")
            lines.append(f"📖 出处：{entry.get('source', '未知')}")
            lines += ["", "---", ""]

    return "\n".join(lines)


def _run_full_summary(
    parsed: dict,
    search_fn,
    generate_fn,
    api_key: str,
    api_url: str,
    model_id: str,
    k: int = _SUMMARY_SEARCH_K,
    progress_cb=None,
) -> dict:
    """执行完整的检索+生成流程，返回三板块结果。

    纯函数（除 progress_cb 外），便于单元测试。

    Args:
        parsed: parse_summary_document 的成功返回值。
        search_fn: 检索函数 search_fn(text, k=k) -> list[dict]。
        generate_fn: 板块生成函数
                     generate_fn(section_type, items_with_hits, api_key, api_url, model) -> list[dict]。
        api_key/api_url/model_id: LLM 调用配置。
        k: 每条检索返回数。
        progress_cb: 可选进度回调 progress_cb(msg: str)。

    Returns:
        {"translation": [...], "terms": [...], "qa": [...]}
        每项 {"item", "result", "source"}。
    """
    results = {}
    for section_key, stype, label, _use_table in _SECTION_META:
        items = parsed.get(section_key, [])
        if not items:
            results[section_key] = []
            continue

        if progress_cb:
            progress_cb(f"正在检索「{label}」板块（{len(items)} 条）")

        # 闭包绑定当前 label（避免循环变量延迟绑定问题）
        def _sub_progress(done, total, _label=label):
            if progress_cb:
                progress_cb(f"检索「{_label}」{done}/{total}")

        items_with_hits = _collect_items_with_hits(
            items, search_fn, k=k, progress_cb=_sub_progress,
        )

        if progress_cb:
            progress_cb(f"正在生成「{label}」总结...")

        llm_results = generate_fn(
            stype, items_with_hits,
            api_key=api_key, api_url=api_url, model=model_id,
        )
        merged = _merge_results_with_sources(llm_results, items_with_hits)
        results[section_key] = merged

    return results


def _render_results(results: dict, book_name: str) -> None:
    """渲染三板块结果到 Streamlit。

    互译板块用 st.dataframe 表格汇总 + 逐条详情；
    名词解释、简答用条目卡片逐条展示。每条附出处。
    """
    st.markdown(f"### 📋 总结结果 -《{book_name}》")

    for section_key, _stype, label, use_table in _SECTION_META:
        items = results.get(section_key, [])
        st.markdown(f"#### {label}（{len(items)} 条）")

        if not items:
            st.info("本板块无条目")
            st.divider()
            continue

        # 互译板块：表格汇总（优先 st.dataframe，iframe 兼容）
        if use_table:
            df = pd.DataFrame([
                {
                    "序号": i,
                    "条目": e.get("item", ""),
                    "出处": e.get("source", ""),
                    "结果预览": _preview(e.get("result", ""), 60),
                }
                for i, e in enumerate(items, 1)
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

        # 逐条详情（条目卡片，不用 overflow:hidden 的 expander）
        for i, entry in enumerate(items, 1):
            st.markdown(f"**{i}. {entry.get('item', '')}**")
            st.markdown(fix_latex_formulas(entry.get("result", "")))
            st.caption(f"📖 出处：{entry.get('source', '未知')}")

        st.divider()


# ── 主渲染函数 ──────────────────────────────────────────

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
    """渲染重点总结模式界面。

    流程：上传文档 -> 解析 -> 选教材 -> 检索 -> LLM 总结 -> 渲染 -> 导出。
    游客与登录用户均可用，均记录学习行为。
    签名与 modes/case.py 一致；付费模型密码保护由主流程
    render_scope_and_model_selector 调用 check_model_password 统一处理，
    本模式与现有模式一致不在 render 内重复调用。
    """
    st.markdown("上传重点文档（.txt/.md），系统自动解析三板块并基于教材生成重点总结。")

    # ── 1. 文件上传 + 解析 ────────────────────────────────
    uploaded = st.file_uploader(
        "📎 上传重点文档", type=["txt", "md"], key="summary_file_uploader",
    )

    parsed = None
    if uploaded is not None:
        # 读取文件内容：优先 getvalue()（不受游标位置影响，rerun 安全）；
        # 兜底 seek(0)+read() 以兼容不同 Streamlit 版本的 UploadedFile。
        try:
            raw = uploaded.getvalue()
        except (AttributeError, ValueError):
            try:
                uploaded.seek(0)
                raw = uploaded.read()
            except Exception:
                raw = b""
        if not raw:
            st.warning("读取文件内容为空，请重新上传")
        elif len(raw) > _MAX_UPLOAD_BYTES:
            st.error(f"文件超过 100KB 限制（实际 {len(raw)} 字节），请精简后重传")
        else:
            # 尝试 UTF-8 解码，失败回退 GBK
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            parsed = parse_summary_document(text)
            if "error" in parsed:
                st.error(f"解析失败：{parsed['error']}")
                st.info(
                    "请检查文档格式（需含「英汉互译」「名词解释」「简答」等板块标题）后重新上传。"
                )
                parsed = None
            else:
                # 新文件解析成功，清除旧结果（不调用 st.rerun，自然在下一次渲染时失效）
                if st.session_state.get("summary_uploaded_name") != uploaded.name:
                    st.session_state["summary_results"] = None
                    st.session_state["summary_uploaded_name"] = uploaded.name

    if parsed is None:
        render_empty_state(
            "上传重点文档开始生成总结",
            "支持 .txt/.md 格式，自动识别英汉互译、名词解释、简答三板块",
            examples=[
                "一、英汉互译\n心力衰竭、动脉粥样硬化、高血压…",
                "二、名词解释\n肿瘤、炎症、栓塞…",
                "三、简答\n1. 简述心衰的发病机制",
            ],
        )
        return

    # ── 2. 解析结果摘要 + 教材选择 ────────────────────────
    n_trans = len(parsed.get("translation", []))
    n_terms = len(parsed.get("terms", []))
    n_qa = len(parsed.get("qa", []))
    st.success(
        f"✅ 解析成功：英汉互译 {n_trans} 条、名词解释 {n_terms} 条、简答 {n_qa} 条"
    )

    if not ALL_BOOKS:
        st.error("未加载到可用教材，无法生成总结")
        return

    # 教材来源：复用页面顶部「教材范围」选择器，不在标签页内重复渲染单选。
    # 重点总结需锁定 1 本教材：取顶部已选首本；未选时默认 ALL_BOOKS[0]。
    if selected_books and selected_books[0] in ALL_BOOKS:
        selected_book = selected_books[0]
        if len(selected_books) > 1:
            st.info(f"📖 本次总结将以首本《{selected_book}》为准（顶部共选 {len(selected_books)} 本）")
        else:
            st.markdown(f"📖 **本次总结教材：《{selected_book}》**")
    else:
        selected_book = ALL_BOOKS[0]
        st.markdown(f"📖 **本次总结教材：《{selected_book}》**（可在页面顶部「教材范围」切换）")
    start_btn = st.button(
        "🚀 开始总结", type="primary", use_container_width=True, key="summary_start_btn",
    )

    # ── 3. 触发总结（逐板块流式输出，像智能问答一样逐字显示）─────────
    if start_btn and selected_book:
        try:
            with st.status("⚡ 正在生成重点总结...", expanded=True) as status:
                embeddings, documents, metadatas = load_selected_books(
                    [selected_book], manifest,
                )
                if embeddings is None:
                    st.error(f"教材《{selected_book}》加载失败")
                    status.update(label="❌ 加载失败", state="error")
                    st.stop()
                st.write(f"已加载《{selected_book}》，共 {len(documents)} 个文本块")

                current_alpha = st.session_state.get("alpha", alpha)

                # 批量预计算全部条目的 embedding（1 次 API 调用替代 N 次串行）
                all_items = (
                    parsed.get("translation", [])
                    + parsed.get("terms", [])
                    + parsed.get("qa", [])
                )
                _item_vec_map: dict[str, bytes] = {}
                if all_items:
                    st.write(f"⏳ 正在批量向量化 {len(all_items)} 个条目...")
                    unique_items = list(dict.fromkeys(all_items))
                    vec_list = get_embeddings_batch(tuple(unique_items))
                    _item_vec_map = dict(zip(unique_items, vec_list))

                def _search_fn(text, k=_SUMMARY_SEARCH_K):
                    qvec = _item_vec_map.get(text)
                    if qvec:
                        return search_with_qvec(
                            qvec, text, embeddings, documents, metadatas,
                            k=k, alpha=current_alpha,
                        )
                    return search(
                        text, embeddings, documents, metadatas,
                        k=k, alpha=current_alpha,
                    )

                api_key, api_url, model_id = get_model_api_config(selected_model)
                if not api_key:
                    st.error(
                        f"未配置所选模型（{selected_model}）的 API Key，"
                        f"请在顶部切换到已配置 Key 的模型。"
                    )
                    status.update(label="❌ API Key 缺失", state="error")
                    st.stop()

                # 逐板块：检索 -> 流式生成，结果实时拼接到 full_answer
                status.update(label="⚡ 正在检索并流式生成...")
                full_answer = ""
                # 流式输出占位符（status 展开期间可见；完成后由下方 section 4 重渲染）
                ans_placeholder = st.empty()
                for section_key, stype, label, _use_table in _SECTION_META:
                    items = parsed.get(section_key, [])
                    if not items:
                        continue
                    st.write(f"检索「{label}」板块（{len(items)} 条）...")
                    items_with_hits = _collect_items_with_hits(
                        items, _search_fn, k=_SUMMARY_SEARCH_K,
                    )
                    if not items_with_hits:
                        continue
                    user_msg = build_summary_message(stype, items_with_hits)
                    full_answer += f"\n\n## {label}\n\n"
                    ans_placeholder.markdown(fix_latex_formulas(full_answer))
                    # 流式逐 token 输出（复用 call_llm_stream，带超时与 429 重试）
                    for chunk in call_llm_stream(
                        api_key, user_msg,
                        api_url=api_url, model=model_id,
                        system_prompt=SUMMARY_SYSTEM_PROMPT,
                        max_tokens=3000,
                    ):
                        full_answer += chunk
                        ans_placeholder.markdown(fix_latex_formulas(full_answer))
                    full_answer += "\n"
                    ans_placeholder.markdown(fix_latex_formulas(full_answer))

                st.session_state["summary_answer"] = full_answer.strip()
                st.session_state["summary_book"] = selected_book
                status.update(label="✅ 总结完成", state="complete", expanded=False)
        except Exception as exc:
            # 任何异常都显式展示，避免「点击无结果」的静默失败
            import traceback as _tb
            st.error(f"生成总结时出错：{exc}")
            st.code(_tb.format_exc(), language="python")
            st.session_state["summary_answer"] = None

        # 记录学习行为（仅按钮触发时调用一次，rerun 后按钮为 False 不再触发）
        total_items = n_trans + n_terms + n_qa
        _record_learning(
            topic=f"重点总结《{selected_book}》{total_items} 条目",
            query=f"重点总结 {selected_book}",
        )

    # ── 4. 渲染结果 + 导出 ───────────────────────────────
    answer = st.session_state.get("summary_answer")
    book_name = st.session_state.get("summary_book", "")
    if answer:
        st.markdown(f"### 📋 总结结果 -《{book_name}》")
        st.markdown(fix_latex_formulas(answer))

        # 导出 Markdown（复用 ui_components 的导出按钮）
        render_markdown_export_button(
            f"# 重点总结《{book_name}》\n\n{answer}",
            question=f"重点总结《{book_name}》",
            mode_label="重点总结",
            key=f"export_md_summary_{datetime.now().strftime('%H%M%S%f')}",
        )
