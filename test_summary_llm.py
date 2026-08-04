"""重点总结 LLM 提示词与消息构建 — 单元测试

测试内容：
1. SUMMARY_SYSTEM_PROMPT 含反虚构与出处约束关键词
2. build_summary_message 消息含条目、检索段落、章节元数据
3. 检索为空的条目有明确"未找到"指引
4. split_summary_batches 分批逻辑正确
5. parse_summary_response 按条目拆分及降级
6. generate_summary_for_section 复用 call_llm 的 429/超时重试
7. generate_summary_for_section_stream 复用 call_llm_stream 的 429/超时重试

用法：
    python test_summary_llm.py
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from llm_utils import (
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_SECTION_TRANSLATION,
    SUMMARY_SECTION_TERMS,
    SUMMARY_SECTION_QA,
    build_summary_message,
    split_summary_batches,
    parse_summary_response,
    generate_summary_for_section,
    generate_summary_for_section_stream,
    _format_hits_for_summary,
    _SUMMARY_MAX_MSG_CHARS,
    _SUMMARY_MAX_ITEMS_PER_BATCH,
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


def _make_items_with_hits(count=3, hits_per_item=2):
    """构造模拟条目+检索结果列表。"""
    terms = ["心力衰竭", "动脉粥样硬化", "高血压", "心肌梗死", "瓣膜病"]
    return [
        {
            "item": terms[i % len(terms)],
            "hits": _make_hits(hits_per_item),
        }
        for i in range(count)
    ]


def _make_items_with_empty_hits(count=2):
    """构造检索为空的条目。"""
    return [
        {"item": "罕见术语X", "hits": []},
        {"item": "罕见术语Y", "hits": []},
    ][:count]


# ═══════════════════════════════════════════════════════════════════
# 测试 1：SUMMARY_SYSTEM_PROMPT 约束检查
# ═══════════════════════════════════════════════════════════════════

class TestSummarySystemPrompt(unittest.TestCase):
    """验证 SUMMARY_SYSTEM_PROMPT 包含必要的约束关键词。"""

    def test_contains_textbook_only_constraint(self):
        """系统提示必须包含"仅依据教材段落"约束。"""
        self.assertIn("仅依据", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("教材段落", SUMMARY_SYSTEM_PROMPT)

    def test_contains_anti_fabrication_constraint(self):
        """系统提示必须包含反虚构约束。"""
        self.assertIn("禁止", SUMMARY_SYSTEM_PROMPT)
        # 检查禁止虚构/编造/推测相关表述
        has_anti_fab = any(kw in SUMMARY_SYSTEM_PROMPT for kw in ["虚构", "编造", "推测"])
        self.assertTrue(has_anti_fab, "系统提示应包含禁止虚构/编造/推测的表述")

    def test_contains_source_citation_constraint(self):
        """系统提示必须包含逐条标注出处约束。"""
        self.assertIn("标注出处", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("教材名", SUMMARY_SYSTEM_PROMPT)
        self.assertIn("章节名", SUMMARY_SYSTEM_PROMPT)

    def test_contains_not_found_guidance(self):
        """系统提示必须包含检索不足时的明确回答指引。"""
        self.assertIn("教材中未找到相关内容", SUMMARY_SYSTEM_PROMPT)

    def test_contains_section_format_guidance(self):
        """系统提示必须包含三板块的输出格式指引。"""
        # 互译→中英对照表
        self.assertIn("中英对照", SUMMARY_SYSTEM_PROMPT)
        # 名词解释→定义
        self.assertIn("定义", SUMMARY_SYSTEM_PROMPT)
        # 简答→分点结构化答案
        self.assertIn("分点", SUMMARY_SYSTEM_PROMPT)

    def test_contains_item_delimiter_format(self):
        """系统提示中的输出示例使用 ===条目N=== 分隔标记。"""
        self.assertIn("===条目", SUMMARY_SYSTEM_PROMPT)


# ═══════════════════════════════════════════════════════════════════
# 测试 2：build_summary_message 消息构建
# ═══════════════════════════════════════════════════════════════════

class TestBuildSummaryMessage(unittest.TestCase):
    """验证消息构建包含条目、检索段落和章节元数据。"""

    def test_message_contains_all_items(self):
        """消息中必须包含全部条目文本。"""
        items = _make_items_with_hits(3)
        msg = build_summary_message(SUMMARY_SECTION_TRANSLATION, items)
        for entry in items:
            self.assertIn(entry["item"], msg, f"消息应包含条目: {entry['item']}")

    def test_message_contains_search_passages(self):
        """消息中必须包含检索到的段落文本。"""
        items = _make_items_with_hits(2, hits_per_item=2)
        msg = build_summary_message(SUMMARY_SECTION_TERMS, items)
        for entry in items:
            for hit in entry["hits"]:
                self.assertIn(hit["text"][:50], msg, "消息应包含检索段落文本片段")

    def test_message_contains_chapter_metadata(self):
        """消息中必须包含教材名和章节名元数据。"""
        items = _make_items_with_hits(2)
        msg = build_summary_message(SUMMARY_SECTION_QA, items)
        self.assertIn("病理学", msg, "消息应包含教材名")
        self.assertIn("第十章", msg, "消息应包含章节名")

    def test_message_contains_item_delimiters(self):
        """消息中使用 ===条目N=== 分隔标记。"""
        items = _make_items_with_hits(3)
        msg = build_summary_message(SUMMARY_SECTION_TRANSLATION, items)
        self.assertIn("===条目1===", msg)
        self.assertIn("===条目2===", msg)
        self.assertIn("===条目3===", msg)

    def test_message_section_label_translation(self):
        """互译板块消息包含正确的板块标签。"""
        items = _make_items_with_hits(1)
        msg = build_summary_message(SUMMARY_SECTION_TRANSLATION, items)
        self.assertIn("英汉互译", msg)

    def test_message_section_label_terms(self):
        """名词解释板块消息包含正确的板块标签。"""
        items = _make_items_with_hits(1)
        msg = build_summary_message(SUMMARY_SECTION_TERMS, items)
        self.assertIn("名词解释", msg)

    def test_message_section_label_qa(self):
        """简答板块消息包含正确的板块标签。"""
        items = _make_items_with_hits(1)
        msg = build_summary_message(SUMMARY_SECTION_QA, items)
        self.assertIn("简答题", msg)

    def test_empty_hits_has_not_found_guidance(self):
        """检索为空的条目在消息中有明确的'未找到'指引。"""
        items = _make_items_with_empty_hits(2)
        msg = build_summary_message(SUMMARY_SECTION_TERMS, items)
        self.assertIn("未检索到相关教材段落", msg)
        self.assertIn("教材中未找到相关内容", msg)

    def test_mixed_hits_and_empty(self):
        """混合有检索结果和无检索结果的条目，均正确呈现。"""
        items = [
            {"item": "有结果的术语", "hits": _make_hits(1)},
            {"item": "无结果的术语", "hits": []},
        ]
        msg = build_summary_message(SUMMARY_SECTION_TERMS, items)
        self.assertIn("有结果的术语", msg)
        self.assertIn("无结果的术语", msg)
        self.assertIn("未检索到相关教材段落", msg)
        self.assertIn("病理学", msg)  # 有结果的条目应有教材名


# ═══════════════════════════════════════════════════════════════════
# 测试 3：split_summary_batches 分批逻辑
# ═══════════════════════════════════════════════════════════════════

class TestSplitSummaryBatches(unittest.TestCase):
    """验证分批切分逻辑。"""

    def test_small_list_single_batch(self):
        """少量条目应归为单批。"""
        items = _make_items_with_hits(3)
        batches = split_summary_batches(items)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)

    def test_exceeds_max_items_splits(self):
        """超过 max_items 上限时自动分批。"""
        items = _make_items_with_hits(_SUMMARY_MAX_ITEMS_PER_BATCH + 3)
        batches = split_summary_batches(items)
        self.assertGreater(len(batches), 1)
        total = sum(len(b) for b in batches)
        self.assertEqual(total, len(items))

    def test_exceeds_max_chars_splits(self):
        """超过字符上限时自动分批。"""
        # 构造长文本条目
        items = [
            {"item": "A" * 2000, "hits": [{"text": "B" * 2000, "book": "X", "chapter": "Y", "similarity": 0.9}]}
            for _ in range(5)
        ]
        batches = split_summary_batches(items, max_chars=3000)
        self.assertGreater(len(batches), 1)
        total = sum(len(b) for b in batches)
        self.assertEqual(total, 5)

    def test_empty_input(self):
        """空输入返回包含一个空批次的列表。"""
        batches = split_summary_batches([])
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0], [])

    def test_single_item(self):
        """单个条目不分批。"""
        items = _make_items_with_hits(1)
        batches = split_summary_batches(items)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 1)


# ═══════════════════════════════════════════════════════════════════
# 测试 4：parse_summary_response 响应解析
# ═══════════════════════════════════════════════════════════════════

class TestParseSummaryResponse(unittest.TestCase):
    """验证 LLM 响应解析逻辑。"""

    def test_parse_normal_response(self):
        """正常分隔标记响应正确拆分。"""
        response = (
            "===条目1===\n| 中文 | 英文 |\n|------|------|\n| 心力衰竭 | Heart Failure |\n📖 出处：病理学·第十章\n\n"
            "===条目2===\n| 中文 | 英文 |\n|------|------|\n| 高血压 | Hypertension |\n📖 出处：内科学·第五章"
        )
        items = ["心力衰竭", "高血压"]
        results = parse_summary_response(response, items, SUMMARY_SECTION_TRANSLATION)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["item"], "心力衰竭")
        self.assertIn("Heart Failure", results[0]["result"])
        self.assertEqual(results[1]["item"], "高血压")
        self.assertIn("Hypertension", results[1]["result"])

    def test_parse_with_preamble(self):
        """响应前有前言文本，仍可正确解析。"""
        response = (
            "以下是英汉互译结果：\n\n"
            "===条目1===\n**心力衰竭**：心脏泵血功能下降。\n📖 出处：病理学·第十章\n\n"
            "===条目2===\n**高血压**：动脉血压持续升高。\n📖 出处：内科学·第五章"
        )
        items = ["心力衰竭", "高血压"]
        results = parse_summary_response(response, items, SUMMARY_SECTION_TERMS)
        self.assertEqual(len(results), 2)

    def test_parse_fallback_on_no_delimiters(self):
        """无分隔标记时降级为整段展示。"""
        response = "心力衰竭是指心脏泵血功能降低的病理状态。\n高血压是指动脉血压持续高于正常水平。"
        items = ["心力衰竭", "高血压"]
        results = parse_summary_response(response, items, SUMMARY_SECTION_TERMS)
        self.assertEqual(len(results), 1)
        self.assertIn("解析降级", results[0]["item"])
        self.assertEqual(results[0]["result"], response)

    def test_parse_empty_response(self):
        """空响应返回警告信息。"""
        results = parse_summary_response("", ["条目1"], SUMMARY_SECTION_TERMS)
        self.assertEqual(len(results), 1)
        self.assertIn("未返回有效内容", results[0]["result"])

    def test_parse_whitespace_only_response(self):
        """纯空白响应返回警告信息。"""
        results = parse_summary_response("   \n\n  ", ["条目1"], SUMMARY_SECTION_TERMS)
        self.assertEqual(len(results), 1)
        self.assertIn("未返回有效内容", results[0]["result"])

    def test_parse_partial_delimiters(self):
        """部分条目有分隔标记，仍可解析出有效条目。"""
        response = (
            "===条目1===\n答案1\n\n"
            "===条目3===\n答案3"
        )
        items = ["条目A", "条目B", "条目C"]
        results = parse_summary_response(response, items, SUMMARY_SECTION_QA)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["item"], "条目A")
        self.assertEqual(results[1]["item"], "条目C")


# ═══════════════════════════════════════════════════════════════════
# 测试 5：generate_summary_for_section 调用链路（mock LLM）
# ═══════════════════════════════════════════════════════════════════

class TestGenerateSummarySection(unittest.TestCase):
    """验证批量调用函数复用 call_llm 的超时保护与 429 重试。"""

    @patch("llm_utils.call_llm")
    def test_calls_call_llm_with_correct_params(self, mock_call_llm):
        """验证调用 call_llm 时传入正确的 system_prompt 和参数。"""
        mock_call_llm.return_value = (
            "===条目1===\n| 中文 | 英文 |\n|------|------|\n| 心力衰竭 | Heart Failure |\n"
            "📖 出处：病理学·第十章"
        )
        items = _make_items_with_hits(1)
        results = generate_summary_for_section(
            SUMMARY_SECTION_TRANSLATION,
            items,
            api_key="test-key",
            api_url="https://test.api/v1/chat/completions",
            model="test-model",
        )
        mock_call_llm.assert_called_once()
        call_kwargs = mock_call_llm.call_args
        # 验证 system_prompt 是 SUMMARY_SYSTEM_PROMPT
        self.assertEqual(call_kwargs.kwargs.get("system_prompt") or call_kwargs[1].get("system_prompt"), SUMMARY_SYSTEM_PROMPT)
        # 验证 api_key
        self.assertEqual(call_kwargs.kwargs.get("api_key") or call_kwargs[1].get("api_key"), "test-key")
        self.assertGreater(len(results), 0)

    @patch("llm_utils.call_llm")
    def test_empty_items_returns_empty(self, mock_call_llm):
        """空条目列表不调用 LLM，返回空列表。"""
        results = generate_summary_for_section(
            SUMMARY_SECTION_TERMS, [], api_key="test-key"
        )
        mock_call_llm.assert_not_called()
        self.assertEqual(results, [])

    @patch("llm_utils.call_llm")
    def test_multiple_batches_all_called(self, mock_call_llm):
        """多批次时每批都调用 call_llm。"""
        mock_call_llm.return_value = "===条目1===\n答案"
        # 构造超过 max_items 的条目
        items = _make_items_with_hits(_SUMMARY_MAX_ITEMS_PER_BATCH + 2)
        results = generate_summary_for_section(
            SUMMARY_SECTION_TERMS, items, api_key="test-key"
        )
        self.assertEqual(mock_call_llm.call_count, 2)

    @patch("llm_utils.call_llm")
    def test_429_retry_via_call_llm(self, mock_call_llm):
        """验证 429 重试通过 call_llm 既有链路处理（call_llm 返回超时提示时透传）。"""
        # call_llm 在重试耗尽后返回超时提示
        mock_call_llm.return_value = "⚠️ 请求超时，请稍后重试"
        items = _make_items_with_hits(1)
        results = generate_summary_for_section(
            SUMMARY_SECTION_TERMS, items, api_key="test-key"
        )
        # 结果应包含超时提示（降级展示）
        self.assertEqual(len(results), 1)
        self.assertIn("超时", results[0]["result"])

    @patch("llm_utils.call_llm")
    def test_timeout_degradation(self, mock_call_llm):
        """超时降级：call_llm 返回错误提示，parse 降级展示。"""
        mock_call_llm.return_value = "⚠️ 生成回答失败: Connection error"
        items = _make_items_with_hits(1)
        results = generate_summary_for_section(
            SUMMARY_SECTION_QA, items, api_key="test-key"
        )
        self.assertEqual(len(results), 1)
        self.assertIn("生成回答失败", results[0]["result"])


# ═══════════════════════════════════════════════════════════════════
# 测试 6：generate_summary_for_section_stream 流式调用链路
# ═══════════════════════════════════════════════════════════════════

class TestGenerateSummaryStream(unittest.TestCase):
    """验证流式批量调用复用 call_llm_stream 的 429/超时重试。"""

    @patch("llm_utils.call_llm_stream")
    def test_stream_yields_batches(self, mock_stream):
        """流式调用 yield (batch_index, chunk) 元组。"""
        mock_stream.return_value = iter(["心力衰竭", "的定义", "是心脏功能", "下降"])
        items = _make_items_with_hits(1)
        chunks = list(generate_summary_for_section_stream(
            SUMMARY_SECTION_TERMS, items, api_key="test-key"
        ))
        self.assertGreater(len(chunks), 0)
        # 每个 chunk 是 (batch_index, text) 元组
        for batch_idx, text in chunks:
            self.assertEqual(batch_idx, 0)
            self.assertIsInstance(text, str)

    @patch("llm_utils.call_llm_stream")
    def test_stream_empty_items(self, mock_stream):
        """空条目列表不调用 LLM。"""
        chunks = list(generate_summary_for_section_stream(
            SUMMARY_SECTION_TERMS, [], api_key="test-key"
        ))
        mock_stream.assert_not_called()
        self.assertEqual(chunks, [])

    @patch("llm_utils.call_llm_stream")
    def test_stream_429_degradation(self, mock_stream):
        """429 限流降级：call_llm_stream yield 限流提示。"""
        mock_stream.return_value = iter(["⚠️ [429限流] 当前模型已达到速率限制。请切换到其他模型后重试。"])
        items = _make_items_with_hits(1)
        chunks = list(generate_summary_for_section_stream(
            SUMMARY_SECTION_TERMS, items, api_key="test-key"
        ))
        self.assertEqual(len(chunks), 1)
        self.assertIn("429", chunks[0][1])

    @patch("llm_utils.call_llm_stream")
    def test_stream_timeout_degradation(self, mock_stream):
        """超时降级：call_llm_stream yield 超时提示。"""
        mock_stream.return_value = iter(["⚠️ 请求超时，请稍后重试"])
        items = _make_items_with_hits(1)
        chunks = list(generate_summary_for_section_stream(
            SUMMARY_SECTION_QA, items, api_key="test-key"
        ))
        self.assertEqual(len(chunks), 1)
        self.assertIn("超时", chunks[0][1])

    @patch("llm_utils.call_llm_stream")
    def test_stream_uses_summary_system_prompt(self, mock_stream):
        """流式调用使用 SUMMARY_SYSTEM_PROMPT。"""
        mock_stream.return_value = iter(["答案"])
        items = _make_items_with_hits(1)
        list(generate_summary_for_section_stream(
            SUMMARY_SECTION_TERMS, items, api_key="test-key"
        ))
        call_kwargs = mock_stream.call_args
        sp = call_kwargs.kwargs.get("system_prompt") or call_kwargs[1].get("system_prompt")
        self.assertEqual(sp, SUMMARY_SYSTEM_PROMPT)


# ═══════════════════════════════════════════════════════════════════
# 测试 7：_format_hits_for_summary 格式化
# ═══════════════════════════════════════════════════════════════════

class TestFormatHitsForSummary(unittest.TestCase):
    """验证检索结果格式化逻辑。"""

    def test_normal_hits_formatting(self):
        """正常检索结果包含教材名、章节名和段落文本。"""
        hits = _make_hits(2)
        text = _format_hits_for_summary(hits)
        self.assertIn("病理学", text)
        self.assertIn("第十章", text)
        self.assertIn("[1]", text)
        self.assertIn("[2]", text)

    def test_empty_hits_not_found_guidance(self):
        """空检索结果包含'未找到'指引。"""
        text = _format_hits_for_summary([])
        self.assertIn("未检索到相关教材段落", text)
        self.assertIn("教材中未找到相关内容", text)

    def test_text_truncation(self):
        """超长段落文本被截断。"""
        hits = [{"text": "A" * 1000, "book": "X", "chapter": "Y", "similarity": 0.9}]
        text = _format_hits_for_summary(hits, max_chars=200)
        # 截断后文本长度应受控
        self.assertLess(len(text), 1000)

    def test_missing_metadata_fallback(self):
        """缺少教材/章节元数据时使用默认值。"""
        hits = [{"text": "段落内容", "similarity": 0.8}]
        text = _format_hits_for_summary(hits)
        self.assertIn("未知教材", text)
        self.assertIn("未知章节", text)


# ═══════════════════════════════════════════════════════════════════
# 运行
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
