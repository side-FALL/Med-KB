"""重点总结模式 modes/summary.py - 单元测试（任务7/8）

测试内容：
1. 纯函数：_extract_source / _collect_items_with_hits / _merge_results_with_sources
2. 纯函数：_build_export_markdown（含三板块标题与条目）
3. 纯函数：_run_full_summary（mock search + generate，三板块齐全、每条含出处、检索为空标注未找到）
4. render 主流程：
   - 解析失败显示错误提示，不崩溃
   - 游客会话 _record_learning 被调用
   - 导出内容包含三板块

测试策略：
- 纯函数直接调用，不依赖 streamlit
- render 测试 patch("modes.summary.st")，session_state 用真实 dict
- mock 检索（search_engine.search）与 LLM（generate_summary_for_section）
- 参考既有测试风格（test_guest_records.py / test_summary_llm.py）

用法：
    python test_summary.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# ── stderr 保护：Streamlit 裸模式下可能关闭 sys.stderr 导致 I/O 错误 ──

class _SafeStderr:
    """包装 stderr，对 closed-file 错误静默容错，保证测试退出码可靠。"""
    def __init__(self, real):
        self._real = real
    def write(self, msg):
        try:
            return self._real.write(msg)
        except (ValueError, OSError):
            return len(msg)
    def flush(self):
        try:
            self._real.flush()
        except (ValueError, OSError):
            pass
    def isatty(self):
        try:
            return self._real.isatty()
        except (ValueError, OSError):
            return False
    def __getattr__(self, name):
        return getattr(self._real, name)

sys.stderr = _SafeStderr(sys.stderr)

# ── 路径设置 ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))

import modes.summary as summary_mod
from modes.summary import (
    _extract_source,
    _collect_items_with_hits,
    _merge_results_with_sources,
    _build_export_markdown,
    _run_full_summary,
    _preview,
)


# ═══════════════════════════════════════════════════════════════════
# 辅助数据
# ═══════════════════════════════════════════════════════════════════

def _make_hits(count=2, book="病理学", chapter="第十章 心血管系统疾病"):
    """构造模拟检索结果。"""
    return [
        {
            "text": f"这是关于心力衰竭的第{i+1}段教材内容，描述了发病机制和临床表现。",
            "book": book,
            "chapter": chapter,
            "similarity": 0.85 - i * 0.05,
        }
        for i in range(count)
    ]


def _make_llm_result(item, section_type):
    """构造模拟 LLM 单条返回（含出处标记）。"""
    if section_type == "translation":
        result = (
            f"| 中文 | 英文 |\n|------|------|\n| {item} | {item} English |\n"
            f"📖 出处：病理学·第十章"
        )
    elif section_type == "terms":
        result = f"**{item}**：这是关于{item}的定义。\n📖 出处：病理学·第十章"
    else:  # qa
        result = (
            f"**问题**：{item}\n**答案**：\n1. 要点一\n2. 要点二\n"
            f"📖 出处：病理学·第十章"
        )
    return {"item": item, "result": result}


def _make_llm_results_empty(item):
    """构造检索为空的 LLM 返回（标注未找到）。"""
    return {"item": item, "result": "教材中未找到相关内容。\n📖 出处：无"}


# ═══════════════════════════════════════════════════════════════════
# 测试 1：_extract_source 出处提取
# ═══════════════════════════════════════════════════════════════════

class TestExtractSource(unittest.TestCase):
    """验证出处提取逻辑。"""

    def test_empty_hits_returns_not_found(self):
        """检索为空时返回未找到提示。"""
        self.assertEqual(_extract_source([]), "教材中未找到相关内容")

    def test_normal_hits_returns_book_chapter(self):
        """正常检索结果返回教材名·章节名。"""
        hits = _make_hits(2, book="病理学", chapter="第十章")
        source = _extract_source(hits)
        self.assertIn("病理学", source)
        self.assertIn("第十章", source)
        self.assertIn("·", source)

    def test_single_hit(self):
        """单条检索结果正确提取出处。"""
        hits = [{"book": "内科学", "chapter": "第五章", "text": "..."}]
        self.assertEqual(_extract_source(hits), "内科学·第五章")

    def test_missing_metadata_fallback(self):
        """缺少教材/章节元数据时使用默认值。"""
        hits = [{"text": "段落", "similarity": 0.8}]
        source = _extract_source(hits)
        self.assertIn("未知教材", source)
        self.assertIn("未知章节", source)


# ═══════════════════════════════════════════════════════════════════
# 测试 2：_collect_items_with_hits 检索收集
# ═══════════════════════════════════════════════════════════════════

class TestCollectItemsWithHits(unittest.TestCase):
    """验证逐条目检索收集逻辑。"""

    def test_collects_all_items(self):
        """每个条目都调用检索并收集结果。"""
        items = ["心力衰竭", "高血压", "心肌梗死"]
        mock_search = MagicMock(side_effect=lambda text, k=5: _make_hits(2))
        result = _collect_items_with_hits(items, mock_search, k=5)
        self.assertEqual(len(result), 3)
        for entry, expected_item in zip(result, items):
            self.assertEqual(entry["item"], expected_item)
            self.assertEqual(len(entry["hits"]), 2)

    def test_search_fn_called_per_item(self):
        """检索函数对每个条目调用一次。"""
        items = ["A", "B", "C"]
        mock_search = MagicMock(return_value=[])
        _collect_items_with_hits(items, mock_search, k=3)
        self.assertEqual(mock_search.call_count, 3)

    def test_progress_cb_called(self):
        """进度回调被正确调用（done/total）。"""
        items = ["X", "Y"]
        mock_search = MagicMock(return_value=_make_hits(1))
        progress_calls = []
        _collect_items_with_hits(
            items, mock_search, k=5,
            progress_cb=lambda d, t: progress_calls.append((d, t)),
        )
        self.assertEqual(progress_calls, [(1, 2), (2, 2)])

    def test_empty_items_returns_empty(self):
        """空条目列表返回空列表。"""
        mock_search = MagicMock()
        result = _collect_items_with_hits([], mock_search)
        self.assertEqual(result, [])
        mock_search.assert_not_called()

    def test_k_param_passed_to_search(self):
        """k 参数传递给检索函数。"""
        mock_search = MagicMock(return_value=[])
        _collect_items_with_hits(["A"], mock_search, k=4)
        call_kwargs = mock_search.call_args
        # search_fn 以关键字传递 k（search_fn(item, k=k)）
        self.assertEqual(call_kwargs.kwargs.get("k"), 4)


# ═══════════════════════════════════════════════════════════════════
# 测试 3：_merge_results_with_sources 合并出处
# ═══════════════════════════════════════════════════════════════════

class TestMergeResultsWithSources(unittest.TestCase):
    """验证 LLM 结果与检索出处合并逻辑。"""

    def test_merge_matches_by_item(self):
        """按 item 文本匹配对应的检索出处。"""
        items_with_hits = [
            {"item": "心力衰竭", "hits": _make_hits(2, book="病理学", chapter="第十章")},
            {"item": "高血压", "hits": _make_hits(1, book="内科学", chapter="第五章")},
        ]
        llm_results = [
            {"item": "心力衰竭", "result": "答案1"},
            {"item": "高血压", "result": "答案2"},
        ]
        merged = _merge_results_with_sources(llm_results, items_with_hits)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["item"], "心力衰竭")
        self.assertIn("病理学", merged[0]["source"])
        self.assertEqual(merged[1]["item"], "高血压")
        self.assertIn("内科学", merged[1]["source"])

    def test_merge_fallback_on_no_match(self):
        """LLM 结果的 item 不匹配时用首个条目出处降级。"""
        items_with_hits = [
            {"item": "心力衰竭", "hits": _make_hits(1, book="病理学", chapter="第十章")},
        ]
        llm_results = [
            {"item": "全部（解析降级）", "result": "整段答案"},
        ]
        merged = _merge_results_with_sources(llm_results, items_with_hits)
        self.assertEqual(len(merged), 1)
        self.assertIn("病理学", merged[0]["source"])

    def test_empty_items_with_hits(self):
        """items_with_hits 为空时所有条目出处为未找到。"""
        llm_results = [{"item": "X", "result": "答案"}]
        merged = _merge_results_with_sources(llm_results, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "教材中未找到相关内容")

    def test_each_merged_entry_has_source(self):
        """每条合并结果都包含 source 字段。"""
        items_with_hits = [
            {"item": f"术语{i}", "hits": _make_hits(1)} for i in range(3)
        ]
        llm_results = [{"item": f"术语{i}", "result": f"答案{i}"} for i in range(3)]
        merged = _merge_results_with_sources(llm_results, items_with_hits)
        for entry in merged:
            self.assertIn("source", entry)
            self.assertTrue(entry["source"])


# ═══════════════════════════════════════════════════════════════════
# 测试 4：_build_export_markdown 导出 Markdown
# ═══════════════════════════════════════════════════════════════════

class TestBuildExportMarkdown(unittest.TestCase):
    """验证导出 Markdown 包含三板块标题与条目。"""

    def _make_full_results(self):
        """构造三板块完整结果。"""
        return {
            "translation": [
                {"item": "心力衰竭", "result": "互译答案", "source": "病理学·第十章"},
            ],
            "terms": [
                {"item": "肿瘤", "result": "定义答案", "source": "病理学·第五章"},
            ],
            "qa": [
                {"item": "简述心衰", "result": "分点答案", "source": "病理学·第十章"},
            ],
        }

    def test_contains_all_section_titles(self):
        """导出内容包含三板块标题。"""
        md = _build_export_markdown(self._make_full_results(), "病理学")
        self.assertIn("英汉互译", md)
        self.assertIn("名词解释", md)
        self.assertIn("简答题", md)

    def test_contains_book_title(self):
        """导出内容包含教材名。"""
        md = _build_export_markdown(self._make_full_results(), "病理学")
        self.assertIn("病理学", md)

    def test_contains_items_and_sources(self):
        """导出内容包含条目文本与出处。"""
        md = _build_export_markdown(self._make_full_results(), "病理学")
        self.assertIn("心力衰竭", md)
        self.assertIn("肿瘤", md)
        self.assertIn("简述心衰", md)
        self.assertIn("病理学·第十章", md)
        self.assertIn("病理学·第五章", md)

    def test_empty_section_marked(self):
        """空板块标注无条目。"""
        results = {
            "translation": [],
            "terms": [{"item": "X", "result": "Y", "source": "S"}],
            "qa": [],
        }
        md = _build_export_markdown(results, "书")
        self.assertIn("本板块无条目", md)

    def test_missing_section_key(self):
        """缺失板块键时不报错。"""
        results = {"translation": [{"item": "X", "result": "Y", "source": "S"}]}
        md = _build_export_markdown(results, "书")
        self.assertIn("英汉互译", md)


# ═══════════════════════════════════════════════════════════════════
# 测试 5：_run_full_summary 完整检索+生成流程
# ═══════════════════════════════════════════════════════════════════

class TestRunFullSummary(unittest.TestCase):
    """验证完整检索+生成流程（mock search + generate）。"""

    def test_three_sections_present(self):
        """三板块结果齐全。"""
        parsed = {
            "translation": ["心力衰竭", "高血压"],
            "terms": ["肿瘤"],
            "qa": ["简述心衰机制"],
        }
        mock_search = MagicMock(return_value=_make_hits(2))
        mock_stream = MagicMock(
            side_effect=lambda stype, items, **kw: [_make_llm_result(e["item"], stype) for e in items]
        )
        results = _run_full_summary(
            parsed, mock_search, mock_stream,
            api_key="k", api_url="u", model_id="m",
        )
        self.assertIn("translation", results)
        self.assertIn("terms", results)
        self.assertIn("qa", results)
        self.assertEqual(len(results["translation"]), 2)
        self.assertEqual(len(results["terms"]), 1)
        self.assertEqual(len(results["qa"]), 1)

    def test_each_item_has_source(self):
        """每条结果含 source 字段。"""
        parsed = {
            "translation": ["心力衰竭"],
            "terms": ["肿瘤"],
            "qa": ["简述心衰"],
        }
        mock_search = MagicMock(return_value=_make_hits(2, book="病理学", chapter="第十章"))
        mock_stream = MagicMock(
            side_effect=lambda stype, items, **kw: [_make_llm_result(e["item"], stype) for e in items]
        )
        results = _run_full_summary(
            parsed, mock_search, mock_stream,
            api_key="k", api_url="u", model_id="m",
        )
        for section in ["translation", "terms", "qa"]:
            for entry in results[section]:
                self.assertIn("source", entry)
                self.assertIn("病理学", entry["source"])

    def test_empty_search_marked_not_found(self):
        """检索为空时 source 标注未找到。"""
        parsed = {"translation": [], "terms": ["罕见术语"], "qa": []}
        mock_search = MagicMock(return_value=[])  # 检索为空
        mock_stream = MagicMock(
            side_effect=lambda stype, items, **kw: [_make_llm_results_empty(e["item"]) for e in items]
        )
        results = _run_full_summary(
            parsed, mock_search, mock_stream,
            api_key="k", api_url="u", model_id="m",
        )
        self.assertEqual(len(results["terms"]), 1)
        self.assertEqual(results["terms"][0]["source"], "教材中未找到相关内容")

    def test_empty_section_skipped(self):
        """空板块不调用 LLM。"""
        parsed = {"translation": ["X"], "terms": [], "qa": []}
        mock_search = MagicMock(return_value=_make_hits(1))
        mock_stream = MagicMock(return_value=[{"item": "X", "result": "R"}])
        _run_full_summary(parsed, mock_search, mock_stream, "k", "u", "m")
        # 只对 translation 板块调用一次 generate
        self.assertEqual(mock_stream.call_count, 1)

    def test_progress_cb_called(self):
        """进度回调被调用。"""
        parsed = {"translation": ["A"], "terms": [], "qa": []}
        mock_search = MagicMock(return_value=_make_hits(1))
        mock_stream = MagicMock(return_value=[{"item": "A", "result": "R"}])
        progress_msgs = []
        _run_full_summary(
            parsed, mock_search, mock_stream, "k", "u", "m",
            progress_cb=lambda msg: progress_msgs.append(msg),
        )
        self.assertGreater(len(progress_msgs), 0)
        # 应包含检索和生成阶段的消息
        has_search_msg = any("检索" in m for m in progress_msgs)
        has_gen_msg = any("生成" in m for m in progress_msgs)
        self.assertTrue(has_search_msg)
        self.assertTrue(has_gen_msg)

    def test_generate_fn_called_with_correct_section_type(self):
        """generate_fn 收到正确的 section_type 常量。"""
        from llm_utils import (
            SUMMARY_SECTION_TRANSLATION,
            SUMMARY_SECTION_TERMS,
            SUMMARY_SECTION_QA,
        )
        parsed = {
            "translation": ["T1"],
            "terms": ["N1"],
            "qa": ["Q1"],
        }
        mock_search = MagicMock(return_value=_make_hits(1))
        mock_stream = MagicMock(return_value=[{"item": "X", "result": "R"}])
        _run_full_summary(parsed, mock_search, mock_stream, "k", "u", "m")
        called_types = [c.args[0] for c in mock_stream.call_args_list]
        self.assertIn(SUMMARY_SECTION_TRANSLATION, called_types)
        self.assertIn(SUMMARY_SECTION_TERMS, called_types)
        self.assertIn(SUMMARY_SECTION_QA, called_types)


# ═══════════════════════════════════════════════════════════════════
# 测试 6：_preview 预览截取
# ═══════════════════════════════════════════════════════════════════

class TestPreview(unittest.TestCase):
    """验证文本预览截取。"""

    def test_short_text_unchanged(self):
        """短文本不截断。"""
        self.assertEqual(_preview("短文本", 60), "短文本")

    def test_long_text_truncated(self):
        """长文本截断并加省略号。"""
        long_text = "A" * 100
        preview = _preview(long_text, 60)
        self.assertTrue(preview.endswith("..."))
        self.assertEqual(len(preview), 63)  # 60 + "..."

    def test_multiline_collapsed(self):
        """多行文本折叠为单行。"""
        self.assertEqual(_preview("第一行\n第二行", 60), "第一行 第二行")


# ═══════════════════════════════════════════════════════════════════
# 测试 7：render 主流程 - 解析失败显示错误提示
# ═══════════════════════════════════════════════════════════════════

class TestRenderParseError(unittest.TestCase):
    """验证解析失败时显示错误提示，不崩溃。"""

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.st")
    def test_parse_error_shows_error_message(
        self, mock_st, mock_fix, mock_empty, mock_export,
    ):
        """上传无效文档时显示解析失败错误提示。"""
        # session_state 用真实 dict
        mock_st.session_state = {}

        # file_uploader 返回无效文件（无板块关键词）
        mock_file = MagicMock()
        mock_file.getvalue.return_value = "这是一段没有任何板块关键词的普通文本内容。".encode("utf-8")
        mock_file.name = "invalid.txt"
        mock_st.file_uploader.return_value = mock_file

        # 调用 render
        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：st.error 被调用（显示解析失败）
        mock_st.error.assert_called()
        error_text = str(mock_st.error.call_args)
        self.assertIn("解析失败", error_text)

        # 断言：st.info 被调用（提示检查文档格式）
        mock_st.info.assert_called()

        # 断言：render_markdown_export_button 未被调用（解析失败不导出）
        mock_export.assert_not_called()

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.st")
    def test_no_file_shows_empty_state(
        self, mock_st, mock_fix, mock_empty, mock_export,
    ):
        """未上传文件时显示空状态。"""
        mock_st.session_state = {}
        mock_st.file_uploader.return_value = None

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：render_empty_state 被调用
        mock_empty.assert_called_once()
        # 断言：st.error 未被调用
        mock_st.error.assert_not_called()

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.st")
    def test_oversized_file_shows_error(
        self, mock_st, mock_fix, mock_empty, mock_export,
    ):
        """超过 100KB 的文件显示大小限制错误。"""
        mock_st.session_state = {}

        # 构造超过 100KB 的文件
        big_content = b"x" * (101 * 1024)
        mock_file = MagicMock()
        mock_file.getvalue.return_value = big_content
        mock_file.name = "big.txt"
        mock_st.file_uploader.return_value = mock_file

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：st.error 被调用，提示超过限制
        mock_st.error.assert_called()
        error_text = str(mock_st.error.call_args)
        self.assertIn("100KB", error_text)


# ═══════════════════════════════════════════════════════════════════
# 测试 8：render 主流程 - 游客记录被调用
# ═══════════════════════════════════════════════════════════════════

class TestRenderGuestRecord(unittest.TestCase):
    """验证游客会话下 _record_learning 被调用。"""

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.get_model_api_config")
    @patch("modes.summary.load_selected_books")
    @patch("modes.summary.get_embeddings_batch", return_value=[b""])
    @patch("modes.summary.call_llm_stream")
    @patch("modes.summary.search")
    @patch("modes.summary.st")
    def test_guest_record_called(
        self, mock_st, mock_search, mock_stream, mock_batch, mock_load,
        mock_config, mock_fix, mock_empty, mock_export,
    ):
        """游客会话下点击总结按钮后 _record_learning 被调用。"""
        # 构造游客 session_state（真实 dict）
        mock_manager = MagicMock()
        mock_st.session_state = {
            "alpha": 0.7,
            "auth_data_manager": mock_manager,
            "auth_username": "guest_abc12345",
        }

        # file_uploader 返回有效文档
        doc_content = (
            "一、英汉互译\n心力衰竭、高血压\n"
            "二、名词解释\n心肌梗死\n"
            "三、简答\n1. 什么是心力衰竭？\n"
        )
        mock_file = MagicMock()
        mock_file.getvalue.return_value = doc_content.encode("utf-8")
        mock_file.name = "pathology.txt"
        mock_st.file_uploader.return_value = mock_file

        # selectbox 返回书名
        mock_st.selectbox.return_value = "病理学"

        # button 返回 True（点击）
        mock_st.button.return_value = True

        # status 上下文管理器
        mock_status = MagicMock()
        mock_st.status.return_value.__enter__.return_value = mock_status
        mock_st.status.return_value.__exit__.return_value = False

        # load_selected_books 返回模拟数据
        mock_load.return_value = (
            MagicMock(),  # embeddings
            ["doc1", "doc2"],  # documents
            [{"book": "病理学", "section": "第十章"}],  # metadatas
        )

        # search 返回模拟检索结果
        mock_search.return_value = _make_hits(2)

        # call_llm_stream 返回模拟流式 chunk（每次调用返回新迭代器，避免耗尽）
        mock_stream.side_effect = lambda *a, **kw: iter(["模拟总结内容"])

        # get_model_api_config
        mock_config.return_value = ("key", "url", "model")

        # fix_latex_formulas 原样返回
        mock_fix.side_effect = lambda x: x

        # 调用 render
        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：游客的 add_learning_record 被调用
        mock_manager.add_learning_record.assert_called_once()
        call_kwargs = mock_manager.add_learning_record.call_args
        kwargs = call_kwargs.kwargs
        self.assertEqual(kwargs.get("username"), "guest_abc12345")
        self.assertEqual(kwargs.get("source_type"), "summary")
        self.assertIn("病理学", kwargs.get("topic", ""))


# ═══════════════════════════════════════════════════════════════════
# 测试 9：render 主流程 - 导出含三板块
# ═══════════════════════════════════════════════════════════════════

class TestRenderExportContainsSections(unittest.TestCase):
    """验证导出 Markdown 包含三板块标题与条目。"""

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.get_model_api_config")
    @patch("modes.summary.load_selected_books")
    @patch("modes.summary.get_embeddings_batch", return_value=[b""])
    @patch("modes.summary.call_llm_stream")
    @patch("modes.summary.search")
    @patch("modes.summary.st")
    def test_export_contains_three_sections(
        self, mock_st, mock_search, mock_stream, mock_batch, mock_load,
        mock_config, mock_fix, mock_empty, mock_export,
    ):
        """正常流程导出的 Markdown 包含三板块标题。"""
        mock_manager = MagicMock()
        mock_st.session_state = {
            "alpha": 0.7,
            "auth_data_manager": mock_manager,
            "auth_username": "user1",
        }

        doc_content = (
            "一、英汉互译\n心力衰竭、高血压\n"
            "二、名词解释\n心肌梗死\n"
            "三、简答\n1. 什么是心力衰竭？\n"
        )
        mock_file = MagicMock()
        mock_file.getvalue.return_value = doc_content.encode("utf-8")
        mock_file.name = "pathology.txt"
        mock_st.file_uploader.return_value = mock_file
        mock_st.selectbox.return_value = "病理学"
        mock_st.button.return_value = True

        mock_status = MagicMock()
        mock_st.status.return_value.__enter__.return_value = mock_status
        mock_st.status.return_value.__exit__.return_value = False

        mock_load.return_value = (MagicMock(), ["doc1"], [{"book": "病理学", "section": "第十章"}])
        mock_search.return_value = _make_hits(2)
        mock_stream.side_effect = lambda *a, **kw: iter(["模拟总结内容"])
        mock_config.return_value = ("key", "url", "model")
        mock_fix.side_effect = lambda x: x

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：render_markdown_export_button 被调用
        mock_export.assert_called_once()

        # 获取导出的 Markdown 内容（第一位置参数）
        export_args = mock_export.call_args
        md = export_args.args[0] if export_args.args else ""

        # 断言：包含三板块标题
        self.assertIn("英汉互译", md)
        self.assertIn("名词解释", md)
        self.assertIn("简答", md)

        # 断言：包含流式生成内容
        self.assertIn("模拟总结内容", md)

        # 断言：mode_label 为重点总结
        mode_label = export_args.kwargs.get("mode_label", "") if export_args.kwargs else ""
        self.assertEqual(mode_label, "重点总结")


# ═══════════════════════════════════════════════════════════════════
# 测试 10：render 主流程 - 三板块结果齐全
# ═══════════════════════════════════════════════════════════════════

class TestRenderThreeSectionsComplete(unittest.TestCase):
    """验证正常流程生成三板块结果并渲染。"""

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.get_model_api_config")
    @patch("modes.summary.load_selected_books")
    @patch("modes.summary.get_embeddings_batch", return_value=[b""])
    @patch("modes.summary.call_llm_stream")
    @patch("modes.summary.search")
    @patch("modes.summary.st")
    def test_three_sections_generated(
        self, mock_st, mock_search, mock_stream, mock_batch, mock_load,
        mock_config, mock_fix, mock_empty, mock_export,
    ):
        """正常流程生成三板块结果。"""
        mock_st.session_state = {"alpha": 0.7}

        doc_content = (
            "一、英汉互译\n心力衰竭、高血压\n"
            "二、名词解释\n心肌梗死\n"
            "三、简答\n1. 什么是心力衰竭？\n"
        )
        mock_file = MagicMock()
        mock_file.getvalue.return_value = doc_content.encode("utf-8")
        mock_file.name = "pathology.txt"
        mock_st.file_uploader.return_value = mock_file
        mock_st.selectbox.return_value = "病理学"
        mock_st.button.return_value = True

        mock_status = MagicMock()
        mock_st.status.return_value.__enter__.return_value = mock_status
        mock_st.status.return_value.__exit__.return_value = False

        mock_load.return_value = (MagicMock(), ["doc1"], [{"book": "病理学", "section": "第十章"}])
        mock_search.return_value = _make_hits(2)
        mock_stream.side_effect = lambda *a, **kw: iter(["模拟总结内容"])
        mock_config.return_value = ("key", "url", "model")
        mock_fix.side_effect = lambda x: x

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：call_llm_stream 被调用 3 次（三板块各一次流式生成）
        self.assertEqual(mock_stream.call_count, 3)

        # 断言：search 被调用（每条目一次，共 4 条目：2+1+1）
        self.assertEqual(mock_search.call_count, 4)

        # 断言：summary_answer 已存入 session_state 且含三板块标题
        answer = mock_st.session_state.get("summary_answer", "")
        self.assertIn("英汉互译", answer)
        self.assertIn("名词解释", answer)
        self.assertIn("简答", answer)

        # 断言：st.markdown 被调用（流式渲染）
        mock_st.markdown.assert_called()

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.get_model_api_config")
    @patch("modes.summary.load_selected_books")
    @patch("modes.summary.get_embeddings_batch", return_value=[b""])
    @patch("modes.summary.call_llm_stream")
    @patch("modes.summary.search")
    @patch("modes.summary.st")
    def test_button_not_clicked_no_generation(
        self, mock_st, mock_search, mock_stream, mock_batch, mock_load,
        mock_config, mock_fix, mock_empty, mock_export,
    ):
        """未点击按钮时不调用 LLM 生成。"""
        mock_st.session_state = {"alpha": 0.7}

        doc_content = "一、英汉互译\n心力衰竭\n"
        mock_file = MagicMock()
        mock_file.getvalue.return_value = doc_content.encode("utf-8")
        mock_file.name = "pathology.txt"
        mock_st.file_uploader.return_value = mock_file
        mock_st.selectbox.return_value = "病理学"
        # button 返回 False（未点击）
        mock_st.button.return_value = False

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：generate_summary_for_section 未被调用
        mock_stream.assert_not_called()
        # 断言：search 未被调用
        mock_search.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 测试 11：render 主流程 - 检索为空标注未找到
# ═══════════════════════════════════════════════════════════════════

class TestRenderEmptySearch(unittest.TestCase):
    """验证检索为空时标注未找到。"""

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.get_model_api_config")
    @patch("modes.summary.load_selected_books")
    @patch("modes.summary.get_embeddings_batch", return_value=[b""])
    @patch("modes.summary.call_llm_stream")
    @patch("modes.summary.search")
    @patch("modes.summary.st")
    def test_empty_search_marked_not_found(
        self, mock_st, mock_search, mock_stream, mock_batch, mock_load,
        mock_config, mock_fix, mock_empty, mock_export,
    ):
        """检索为空时 source 标注'教材中未找到相关内容'。"""
        mock_st.session_state = {"alpha": 0.7}

        doc_content = "二、名词解释\n罕见术语X\n"
        mock_file = MagicMock()
        mock_file.getvalue.return_value = doc_content.encode("utf-8")
        mock_file.name = "rare.txt"
        mock_st.file_uploader.return_value = mock_file
        mock_st.selectbox.return_value = "病理学"
        mock_st.button.return_value = True

        mock_status = MagicMock()
        mock_st.status.return_value.__enter__.return_value = mock_status
        mock_st.status.return_value.__exit__.return_value = False

        mock_load.return_value = (MagicMock(), ["doc1"], [{"book": "病理学", "section": "第十章"}])

        # search 返回空列表（检索为空）
        mock_search.return_value = []

        # call_llm_stream 流式返回"未找到"内容
        mock_stream.side_effect = lambda *a, **kw: iter(["教材中未找到相关内容"])

        mock_config.return_value = ("key", "url", "model")
        mock_fix.side_effect = lambda x: x

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：search 被调用
        mock_search.assert_called()

        # 断言：summary_answer 包含"未找到"（流式生成的内容）
        answer = mock_st.session_state.get("summary_answer", "")
        self.assertIn("教材中未找到相关内容", answer)


# ═══════════════════════════════════════════════════════════════════
# 测试 12：板块缺失（仅两个板块）
# ═══════════════════════════════════════════════════════════════════

class TestRenderMissingSection(unittest.TestCase):
    """验证板块缺失时仍正常工作。"""

    @patch("modes.summary.render_markdown_export_button")
    @patch("modes.summary.render_empty_state")
    @patch("modes.summary.fix_latex_formulas")
    @patch("modes.summary.get_model_api_config")
    @patch("modes.summary.load_selected_books")
    @patch("modes.summary.get_embeddings_batch", return_value=[b""])
    @patch("modes.summary.call_llm_stream")
    @patch("modes.summary.search")
    @patch("modes.summary.st")
    def test_missing_qa_section(
        self, mock_st, mock_search, mock_stream, mock_batch, mock_load,
        mock_config, mock_fix, mock_empty, mock_export,
    ):
        """缺少简答板块时仍正常生成其他两板块。"""
        mock_st.session_state = {"alpha": 0.7}

        # 文档只有英汉互译和名词解释，无简答
        doc_content = (
            "一、英汉互译\n心力衰竭\n"
            "二、名词解释\n心肌梗死\n"
        )
        mock_file = MagicMock()
        mock_file.getvalue.return_value = doc_content.encode("utf-8")
        mock_file.name = "partial.txt"
        mock_st.file_uploader.return_value = mock_file
        mock_st.selectbox.return_value = "病理学"
        mock_st.button.return_value = True

        mock_status = MagicMock()
        mock_st.status.return_value.__enter__.return_value = mock_status
        mock_st.status.return_value.__exit__.return_value = False

        mock_load.return_value = (MagicMock(), ["doc1"], [{"book": "病理学", "section": "第十章"}])
        mock_search.return_value = _make_hits(2)
        mock_stream.side_effect = lambda *a, **kw: iter(["模拟总结内容"])
        mock_config.return_value = ("key", "url", "model")
        mock_fix.side_effect = lambda x: x

        summary_mod.render(
            manifest={},
            ALL_BOOKS=["病理学"],
            book_count=1,
            top_k=10,
            alpha=0.7,
            scope="全部教材",
            selected_model="key",
            selected_books=[],
        )

        # 断言：generate_summary_for_section 只被调用 2 次（无简答）
        self.assertEqual(mock_stream.call_count, 2)

        # 断言：导出仍成功
        mock_export.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# 运行
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
