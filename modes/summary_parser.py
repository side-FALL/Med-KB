"""医学教材知识库 - 重点文档解析器（需求③ P2）

将上传的重点文档（.txt/.md）解析为结构化三板块条目清单：
- translation: 英汉互译术语列表
- terms: 名词解释术语列表
- qa: 简答问题列表

格式样例见 .mimocode/病理重点.txt：
- 中文序号「一、二、三」分节
- 术语顿号/逗号分隔
- 简答数字编号

设计要点：
- 纯函数模块，无 UI 和网络依赖
- 兼容多种序号变体（中文数字、阿拉伯数字、全角/半角分隔符）
- 板块缺失时对应列表为空而非报错
- 全文无法识别任何板块时返回带 error 键的字典
- 输入超长（>100KB）时拒绝（双层防御，UI 层也有 file_uploader 限制）

返回值结构：
    成功: {"translation": [...], "terms": [...], "qa": [...]}
    超长: {"error": "输入超过 100KB 限制（实际 N 字节），请精简后重传"}
    无板块: {"error": "未识别到任何板块，请检查文档格式（需含英汉互译/名词解释/简答关键词）"}
"""

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# 输入大小上限：100KB（UTF-8 字节数）
MAX_INPUT_BYTES = 100 * 1024

# 板块关键词映射
# 匹配方式：标题中包含任一关键词且标题长度合理即归属该板块
SECTION_KEYWORDS: Dict[str, List[str]] = {
    "translation": [
        "英汉互译", "中英互译", "英译汉", "汉译英",
        "中英对照", "英汉对照", "互译",
    ],
    "terms": [
        "名词解释", "术语解释", "名词释义",
    ],
    "qa": [
        "简答", "问答", "简述", "论述", "简答题", "问答题",
    ],
}

# 标题最大长度：用于区分分节标题与简答题干
# 分节标题通常 2-6 字（如"英汉互译"4 字、"简答"2 字）
# 简答题干通常 >8 字（完整句子）；阈值取 8 兼顾"英汉互译题"(5)等变体
_MAX_TITLE_LEN = 8

# 分节标题正则：匹配行首的序号标记
# 支持：
#   中文数字 + 分隔符：一、 一． 一. 一）
#   括号包裹序号：（一） (一) （1） (1)
#   阿拉伯数字 + 分隔符：1. 1、 1． 1)
#   第X部分：第一部分 第1部分
_SECTION_NUM_PATTERN = re.compile(
    r'^\s*(?:'
    r'[一二三四五六七八九十]+\s*[、.．）)]'        # 中文数字 + 分隔符
    r'|[（(]\s*[一二三四五六七八九十\d]+\s*[）)]'    # 括号包裹序号
    r'|\d+\s*[、.．）)]'                            # 阿拉伯数字 + 分隔符
    r'|第\s*[一二三四五六七八九十\d]+\s*部分?'      # 第X(部分)
    r')\s*(.*)$'
)

# 简答条目编号正则（行首）：1. / 1、 / 1． / 1)
_QA_ITEM_PATTERN = re.compile(r'^\s*(\d+)\s*[、.．）)]\s*(.*)$')

# 术语分隔符：顿号、中文逗号、英文逗号、换行
_TERM_SPLIT_PATTERN = re.compile(r'[、，,\n]')


def _match_section_type(title: str) -> str:
    """根据标题文本匹配板块类型（子串匹配 + 长度约束）。

    Args:
        title: 去掉序号后的标题文本

    Returns:
        板块键名（translation/terms/qa）或空字符串

    长度约束：标题长度 <= _MAX_TITLE_LEN 时才匹配，
    避免将含关键词的长句（如简答题干"名词解释什么是肿瘤"）误判为分节标题。
    """
    if not title:
        return ""
    stripped = title.strip()
    if len(stripped) > _MAX_TITLE_LEN:
        return ""
    for section_type, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in stripped:
                return section_type
    return ""


def _parse_section_header(line: str) -> str:
    """检查一行是否是分节标题，返回板块类型或空字符串。

    匹配规则（按优先级）：
    1. 行首有序号标记（一、/1./（一）等），标题部分含板块关键词且长度合理
    2. 整行恰好等于某个板块关键词（无序号前缀的裸标题）

    Args:
        line: 单行文本

    Returns:
        板块键名或空字符串
    """
    # 规则1：有序号前缀
    m = _SECTION_NUM_PATTERN.match(line)
    if m:
        title = m.group(1).strip()
        section_type = _match_section_type(title)
        if section_type:
            return section_type
    # 规则2：裸关键词标题（整行恰好等于关键词，无序号前缀）
    stripped = line.strip()
    for section_type, keywords in SECTION_KEYWORDS.items():
        if stripped in keywords:
            return section_type
    return ""


def _split_terms(content: str) -> List[str]:
    """切分术语条目（顿号/逗号/换行分隔），去空白去重，保持顺序。

    支持分隔符：、 ， , \\n
    忽略空条目；去重保留首次出现。
    """
    parts = _TERM_SPLIT_PATTERN.split(content)
    seen = set()
    result = []
    for p in parts:
        item = p.strip()
        # 去除首尾可能残留的引号
        item = item.strip(' \t\r"""\'\'')
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _split_qa(content: str) -> List[str]:
    """切分简答条目（行首数字编号），保留完整题干（可跨行）。

    支持编号格式：1. / 1、 / 1． / 1)
    题干为编号后的文本；若条目跨行，后续行原样拼接（保留换行）。
    不去重（保留所有题干，即使文本相同但编号不同）。
    """
    items = []
    current_lines: List[str] = []
    in_item = False

    for line in content.splitlines():
        m = _QA_ITEM_PATTERN.match(line)
        if m:
            # 新条目开始：先保存上一条
            if in_item:
                text = "\n".join(current_lines).strip()
                if text:
                    items.append(text)
            current_lines = [m.group(2)]
            in_item = True
        else:
            if in_item:
                # 当前条目延续行（保留原文，用于多行题干）
                current_lines.append(line)
    # 保存最后一条
    if in_item:
        text = "\n".join(current_lines).strip()
        if text:
            items.append(text)

    return items


def parse_summary_document(text: str) -> dict:
    """解析重点文档，返回三板块条目清单。

    Args:
        text: 文档全文（.txt/.md 内容）

    Returns:
        dict:
        - 成功: {"translation": [...], "terms": [...], "qa": [...]}
        - 超长: {"error": "输入超过 100KB 限制（实际 N 字节），请精简后重传"}
        - 空输入: {"error": "输入为空，请上传有效文档"}
        - 无板块: {"error": "未识别到任何板块，请检查文档格式（需含英汉互译/名词解释/简答关键词）"}

    板块缺失时对应键为空列表（非缺失键）；全文无任何板块时返回 error。
    """
    # 防御：空输入
    if not text or not text.strip():
        return {"error": "输入为空，请上传有效文档"}

    # 防御：输入超长（按 UTF-8 字节数计）
    input_bytes = len(text.encode("utf-8"))
    if input_bytes > MAX_INPUT_BYTES:
        return {
            "error": f"输入超过 100KB 限制（实际 {input_bytes} 字节），请精简后重传"
        }

    # 标准化换行符
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    # 去除 BOM（如有）
    if normalized.startswith("\ufeff"):
        normalized = normalized[1:]

    # 扫描分节：逐行判断是否为分节标题
    current_section: str = None  # type: ignore
    sections_content: Dict[str, List[str]] = {}

    for line in normalized.split("\n"):
        section_type = _parse_section_header(line)
        if section_type:
            current_section = section_type
            sections_content.setdefault(current_section, [])
            continue
        if current_section:
            sections_content[current_section].append(line)

    # 全文无法识别任何板块
    if not sections_content:
        return {
            "error": "未识别到任何板块，请检查文档格式（需含英汉互译/名词解释/简答关键词）"
        }

    # 按板块类型切分条目
    result: Dict[str, List[str]] = {
        "translation": [],
        "terms": [],
        "qa": [],
    }
    for section_type, content_lines in sections_content.items():
        content = "\n".join(content_lines).strip()
        if not content:
            continue
        if section_type == "qa":
            result[section_type] = _split_qa(content)
        else:
            result[section_type] = _split_terms(content)

    logger.debug(
        "重点文档解析完成: translation=%d terms=%d qa=%d",
        len(result["translation"]), len(result["terms"]), len(result["qa"]),
    )
    return result
