"""重点文档解析器 - 单元测试（任务5）

测试内容：
1. 标准样例（.mimocode/病理重点.txt）解析出 10 + 10 + 3 条
2. 板块缺失（仅两个板块）-> 缺失板块为空列表
3. 序号变体（一、/1./（一）/1、/1． 等组合）
4. 空文档 -> 返回 error
5. 无板块关键词 -> 返回 error
6. 混杂分隔符（顿号 + 中文逗号 + 英文逗号）
7. 条目数量不等
8. 输入超长（>100KB）-> 返回 error
9. 无序号裸关键词标题
10. 简答多行题干
11. 术语去重
12. None 输入

用法：
    python test_summary_parser.py
"""

import sys
import unittest
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from modes.summary_parser import parse_summary_document, MAX_INPUT_BYTES


# ═══════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════

def _load_sample_file() -> str:
    """读取 .mimocode/病理重点.txt 标准样例。"""
    sample_path = Path(__file__).parent / ".mimocode" / "病理重点.txt"
    return sample_path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# 测试类
# ═══════════════════════════════════════════════════════════════════


class TestStandardSample(unittest.TestCase):
    """标准样例（病理重点.txt）解析测试。"""

    def test_standard_sample_three_sections(self):
        """标准样例解析出 3 个板块。"""
        text = _load_sample_file()
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertIn("translation", result)
        self.assertIn("terms", result)
        self.assertIn("qa", result)

    def test_standard_sample_translation_count(self):
        """英汉互译板块 10 条。"""
        result = parse_summary_document(_load_sample_file())
        self.assertEqual(len(result["translation"]), 10)

    def test_standard_sample_terms_count(self):
        """名词解释板块 10 条。"""
        result = parse_summary_document(_load_sample_file())
        self.assertEqual(len(result["terms"]), 10)

    def test_standard_sample_qa_count(self):
        """简答板块 3 条。"""
        result = parse_summary_document(_load_sample_file())
        self.assertEqual(len(result["qa"]), 3)

    def test_standard_sample_translation_content(self):
        """验证英汉互译具体条目。"""
        result = parse_summary_document(_load_sample_file())
        expected = [
            "大叶性肺炎", "畸胎瘤", "原位癌", "损伤", "再生",
            "趋化作用", "脂褐素", "卫星现象", "变质", "瘀血",
        ]
        self.assertEqual(result["translation"], expected)

    def test_standard_sample_terms_content(self):
        """验证名词解释具体条目。"""
        result = parse_summary_document(_load_sample_file())
        expected = [
            "葡萄糖", "栓塞", "玻璃样变性", "风湿小体", "桥接坏死",
            "Barrett食管", "镜影细胞", "克汀病", "硅肺", "恶病质",
        ]
        self.assertEqual(result["terms"], expected)

    def test_standard_sample_qa_content(self):
        """验证简答具体条目（保留完整题干，不含编号前缀）。"""
        result = parse_summary_document(_load_sample_file())
        expected = [
            "比较原发性肺结核、继发性肺结核",
            "比较门脉型肝硬化、坏死型肝硬化",
            "从肉眼观和镜下观两方面比较胃溃疡和溃疡性胃",
        ]
        self.assertEqual(result["qa"], expected)


class TestMissingSection(unittest.TestCase):
    """板块缺失测试。"""

    def test_only_two_sections_qa_missing(self):
        """仅英汉互译 + 名词解释，简答缺失 -> qa 为空列表。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "二、名词解释\n"
            "葡萄糖、栓塞\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(result["qa"], [])

    def test_only_translation_section(self):
        """仅英汉互译板块。"""
        text = "一、英汉互译\n大叶性肺炎、畸胎瘤\n"
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(result["terms"], [])
        self.assertEqual(result["qa"], [])

    def test_only_qa_section(self):
        """仅简答板块。"""
        text = (
            "三、简答\n"
            "1.比较原发性肺结核\n"
            "2.比较门脉型肝硬化\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(result["translation"], [])
        self.assertEqual(result["terms"], [])
        self.assertEqual(len(result["qa"]), 2)


class TestNumberVariants(unittest.TestCase):
    """序号变体测试。"""

    def test_chinese_numeral_with_fullwidth_dot(self):
        """中文数字 + 全角点（一．二．三．）。"""
        text = (
            "一．英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "二．名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "三．简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)

    def test_arabic_numeral_sections(self):
        """阿拉伯数字分节（1. 2. 3.）。"""
        text = (
            "1.英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "2.名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "3.简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)

    def test_arabic_numeral_with_dunhao(self):
        """阿拉伯数字 + 顿号分节（1、 2、 3、）。"""
        text = (
            "1、英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "2、名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "3、简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)

    def test_arabic_numeral_with_fullwidth_dot(self):
        """阿拉伯数字 + 全角点分节（1． 2． 3．）。"""
        text = (
            "1．英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "2．名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "3．简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)

    def test_parenthesized_chinese_numeral(self):
        """括号包裹中文数字分节（（一）（二）（三））。"""
        text = (
            "（一）英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "（二）名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "（三）简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)

    def test_parenthesized_arabic_numeral(self):
        """括号包裹阿拉伯数字分节（(1)(2)(3)）。"""
        text = (
            "(1)英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "(2)名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "(3)简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)

    def test_mixed_qa_number_styles(self):
        """简答条目使用不同编号风格（1. 2、 3．）。"""
        text = (
            "三、简答\n"
            "1.比较原发性肺结核\n"
            "2、比较门脉型肝硬化\n"
            "3．从肉眼观和镜下观比较\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["qa"]), 3)
        self.assertIn("比较原发性肺结核", result["qa"][0])
        self.assertIn("比较门脉型肝硬化", result["qa"][1])
        self.assertIn("从肉眼观和镜下观比较", result["qa"][2])


class TestEmptyDocument(unittest.TestCase):
    """空文档测试。"""

    def test_empty_string(self):
        """空字符串返回 error。"""
        result = parse_summary_document("")
        self.assertIn("error", result)

    def test_whitespace_only(self):
        """纯空白文档返回 error。"""
        result = parse_summary_document("   \n\n  \t  \n")
        self.assertIn("error", result)

    def test_none_input(self):
        """None 输入返回 error。"""
        result = parse_summary_document(None)  # type: ignore
        self.assertIn("error", result)


class TestNoSectionKeywords(unittest.TestCase):
    """无板块关键词测试。"""

    def test_no_keywords_with_numbers(self):
        """有序号但无板块关键词 -> 返回 error。"""
        text = (
            "一、第一部分\n"
            "一些内容\n"
            "\n"
            "二、第二部分\n"
            "更多内容\n"
        )
        result = parse_summary_document(text)
        self.assertIn("error", result)

    def test_plain_text_no_structure(self):
        """纯文本无结构 -> 返回 error。"""
        result = parse_summary_document("这是一段普通的文本，没有任何板块标记。")
        self.assertIn("error", result)

    def test_qa_item_not_treated_as_section(self):
        """含关键词的长题干不被误判为分节标题。"""
        text = (
            "三、简答\n"
            "1.名词解释什么是肿瘤的生物学特性\n"
            "2.英汉互译练习题请完成\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        # 两个题干都不应被误判为分节标题
        self.assertEqual(len(result["qa"]), 2)
        self.assertEqual(result["translation"], [])
        self.assertEqual(result["terms"], [])


class TestMixedSeparators(unittest.TestCase):
    """混杂分隔符测试。"""

    def test_mixed_term_separators(self):
        """术语混杂顿号、中文逗号、英文逗号。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎，畸胎瘤,原位癌、损伤\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(result["translation"], [
            "大叶性肺炎", "畸胎瘤", "原位癌", "损伤"
        ])

    def test_terms_on_separate_lines(self):
        """术语每行一个（换行作为分隔符）。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎\n"
            "畸胎瘤\n"
            "原位癌\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(result["translation"], [
            "大叶性肺炎", "畸胎瘤", "原位癌"
        ])

    def test_mixed_separators_with_newlines(self):
        """混杂分隔符 + 换行。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎，畸胎瘤\n"
            "原位癌、损伤,再生\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(result["translation"], [
            "大叶性肺炎", "畸胎瘤", "原位癌", "损伤", "再生"
        ])


class TestUnequalItemCounts(unittest.TestCase):
    """条目数量不等测试。"""

    def test_unequal_counts(self):
        """三板块条目数不等（3 + 2 + 1）。"""
        text = (
            "一、英汉互译\n"
            "term1、term2、term3\n"
            "\n"
            "二、名词解释\n"
            "term4、term5\n"
            "\n"
            "三、简答\n"
            "1.question1\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 3)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)


class TestInputTooLong(unittest.TestCase):
    """输入超长测试。"""

    def test_exactly_100kb_passes(self):
        """恰好 100KB（含板块关键词）能正常解析。"""
        # 构造一个恰好接近 100KB 的文档
        header = "一、英汉互译\n"
        # 每个中文词 3 字节，用顿号 1 字节 -> 每条 4 字节
        # 需要填充到约 100KB
        padding_term = "大叶性肺炎"  # 5 字符 = 15 字节
        # 构造足够多的术语
        terms = "、".join([padding_term] * 3000)
        text = header + terms + "\n"
        # 确认不超过 100KB
        self.assertLessEqual(len(text.encode("utf-8")), MAX_INPUT_BYTES)

        result = parse_summary_document(text)
        self.assertNotIn("error", result)
        self.assertGreater(len(result["translation"]), 0)

    def test_over_100kb_returns_error(self):
        """超过 100KB 返回 error。"""
        # 构造 > 100KB 的文本
        chunk = "这是一段用于测试超长输入的中文文本。" * 2000
        text = "一、英汉互译\n" + chunk + "\n"
        self.assertGreater(len(text.encode("utf-8")), MAX_INPUT_BYTES)

        result = parse_summary_document(text)
        self.assertIn("error", result)
        self.assertIn("100KB", result["error"])


class TestBareKeywordHeader(unittest.TestCase):
    """无序号裸关键词标题测试。"""

    def test_bare_keyword_headers(self):
        """无序号前缀，整行恰好是关键词。"""
        text = (
            "英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)


class TestQAMultiLine(unittest.TestCase):
    """简答多行题干测试。"""

    def test_multiline_question(self):
        """题干跨行保留完整内容。"""
        text = (
            "三、简答\n"
            "1.比较原发性肺结核、继发性肺结核\n"
            "（从病因、病变、转归三方面比较）\n"
            "2.比较门脉型肝硬化\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["qa"]), 2)
        # 第一题包含跨行内容
        self.assertIn("比较原发性肺结核", result["qa"][0])
        self.assertIn("从病因", result["qa"][0])
        self.assertIn("转归", result["qa"][0])
        # 第二题
        self.assertIn("比较门脉型肝硬化", result["qa"][1])

    def test_qa_strips_number_prefix(self):
        """简答条目去除编号前缀，保留题干文本。"""
        text = (
            "三、简答\n"
            "1.题干一\n"
            "2.题干二\n"
        )
        result = parse_summary_document(text)

        self.assertEqual(result["qa"], ["题干一", "题干二"])
        self.assertFalse(result["qa"][0].startswith("1"))


class TestTermDeduplication(unittest.TestCase):
    """术语去重测试。"""

    def test_duplicate_terms_deduped(self):
        """重复术语去重，保留首次出现。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎、畸胎瘤、大叶性肺炎、原位癌、畸胎瘤\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(result["translation"], [
            "大叶性肺炎", "畸胎瘤", "原位癌"
        ])

    def test_whitespace_stripped(self):
        """术语首尾空白被去除。"""
        text = (
            "一、英汉互译\n"
            " 大叶性肺炎 、 畸胎瘤 、原位癌\n"
        )
        result = parse_summary_document(text)

        self.assertEqual(result["translation"], [
            "大叶性肺炎", "畸胎瘤", "原位癌"
        ])


class TestDuplicateSections(unittest.TestCase):
    """重复板块合并测试。"""

    def test_duplicate_section_merged(self):
        """同名板块出现两次，条目合并去重。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "二、名词解释\n"
            "葡萄糖\n"
            "\n"
            "三、英汉互译\n"
            "原位癌、损伤\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        # 两个英汉互译板块的条目合并
        self.assertIn("大叶性肺炎", result["translation"])
        self.assertIn("畸胎瘤", result["translation"])
        self.assertIn("原位癌", result["translation"])
        self.assertIn("损伤", result["translation"])
        self.assertEqual(len(result["translation"]), 4)


class TestWindowsLineEndings(unittest.TestCase):
    """Windows 换行符测试。"""

    def test_crlf_line_endings(self):
        """\\r\\n 换行符正常解析。"""
        text = "一、英汉互译\r\n大叶性肺炎、畸胎瘤\r\n\r\n二、名词解释\r\n葡萄糖、栓塞\r\n"
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)

    def test_cr_line_endings(self):
        """\\r 换行符正常解析。"""
        text = "一、英汉互译\r大叶性肺炎、畸胎瘤\r\r二、名词解释\r葡萄糖、栓塞\r"
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)


class TestBOMHandling(unittest.TestCase):
    """BOM 处理测试。"""

    def test_utf8_bom_stripped(self):
        """UTF-8 BOM 不影响解析。"""
        text = "\ufeff一、英汉互译\n大叶性肺炎、畸胎瘤\n"
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)


class TestEmptySectionContent(unittest.TestCase):
    """空板块内容测试。"""

    def test_section_header_no_content(self):
        """板块标题后无内容 -> 该板块为空列表。"""
        text = (
            "一、英汉互译\n"
            "大叶性肺炎\n"
            "\n"
            "二、名词解释\n"
            "\n"
            "三、简答\n"
            "1.问题一\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 1)
        self.assertEqual(result["terms"], [])
        self.assertEqual(len(result["qa"]), 1)


class TestDiPartSection(unittest.TestCase):
    """第X部分 分节格式测试。"""

    def test_di_part_format(self):
        """「第一部分」「第二部分」分节格式。"""
        text = (
            "第一部分 英汉互译\n"
            "大叶性肺炎、畸胎瘤\n"
            "\n"
            "第二部分 名词解释\n"
            "葡萄糖、栓塞\n"
            "\n"
            "第三部分 简答\n"
            "1.比较原发性肺结核\n"
        )
        result = parse_summary_document(text)

        self.assertNotIn("error", result)
        self.assertEqual(len(result["translation"]), 2)
        self.assertEqual(len(result["terms"]), 2)
        self.assertEqual(len(result["qa"]), 1)


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))

    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors

    print("\n" + "=" * 60)
    print("📊 重点文档解析器测试总结")
    print(f"   总计: {total} 个测试")
    print(f"   ✅ 通过: {passed}")
    if failures:
        print(f"   ❌ 失败: {failures}")
    if errors:
        print(f"   ⚠️ 错误: {errors}")
    print("=" * 60)

    if failures or errors:
        if failures:
            print("\n失败详情:")
            for test, traceback in result.failures:
                print(f"  - {test}")
        if errors:
            print("\n错误详情:")
            for test, traceback in result.errors:
                print(f"  - {test}")
        sys.exit(1)
    else:
        print("\n🎉 所有测试通过！")
