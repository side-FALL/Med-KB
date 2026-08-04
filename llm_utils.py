"""医学教材知识库 — 共享 LLM 工具（synthesize + 对话历史 + 查询重写 + 流式输出）

供 deploy/app.py 和 modelscope/app.py 导入，避免重复代码。
"""

import json
import re
import requests

# ── System prompts ────────────────────────────────────

SYSTEM_PROMPT = """你是一名严谨的医学教育助手，基于权威医学教材内容回答问题。

要求：
1. 综合多个教材段落，给出准确、有条理的回答
2. 使用层次化结构（标题、分点）组织内容
3. 标注信息来源（如[1][2]），便于溯源
4. 用中文回答，专业术语附英文原文（如：心力衰竭 Heart Failure）
5. 如果检索结果不足以回答问题，明确说明"当前教材内容不足以完整回答此问题"，并给出已有信息的初步回答
6. 回答末尾列出参考来源（教材名.章节）
7. 避免臆测，不确定的内容标注"需进一步查证"
8. 化学方程式和数学公式必须使用LaTeX格式，用 $$ 包裹独立公式，$ 包裹行内公式

公式示例（严格按此格式输出）：
$$2CH_3COSCoA + 6NAD^+ + 2FAD + 2ADP + 2P_i + 6H_2O \\rightarrow 4CO_2 + 6NADH + 6H^+ + 2FADH_2 + 2HSCoA + 2ATP$$

注意：
- 不要用 [ ] 包裹公式，必须用 $$ $$
- 不要用 \\text{}，直接写化学式即可
- 下标用 _（如 H_2O），上标用 ^（如 NAD^+）
- 箭头用 \\rightarrow
"""

COMPACT_SYSTEM_PROMPT = (
    "你是医学教育助手，基于教材段落回答问题。"
    "要求：综合多段落给出准确回答，用中文作答，专业术语附英文，标注来源[1][2]，回答末尾列出参考教材。"
    "化学方程式和数学公式使用LaTeX格式：行内用 $...$，独立公式用 $$...$$。"
    "如果信息不足，诚实说明。"
)

EXAM_SYSTEM_PROMPT = """你是医学教育助手，基于教材段落回答问题，并标注考试重点。

要求：
1. 综合多个教材段落，给出准确、有条理的回答
2. 标注信息来源（如[1][2]）
3. 用中文回答，专业术语附英文原文
4. 用以下标记区分知识点重要性：
   [高频] 执业医师考试/研究生考试中反复出现的知识点
   [核心] 理解该主题必须掌握的基础概念
   [易错] 容易与其他概念混淆、记忆易错的内容
   [临床] 与临床实践直接相关的重要信息
5. 回答末尾用"[考点清单]"汇总本题涉及的重要知识点
6. 如果检索结果不足，明确说明并给出已有信息的初步回答
"""

QUIZ_SYSTEM_PROMPT = """你是医学出题专家，根据教材段落生成高质量医学题目。

要求：
1. 一次性生成5道单选题（每题4个选项A/B/C/D，1个正确答案）
2. 难度分布：2道基础题（概念记忆）、2道理解题（机制分析）、1道应用题（临床情景）
3. 题干简洁明确，避免歧义
4. 干扰项需合理，具有迷惑性（常见错误理解、相近概念混淆）
5. 题目之间不要重复考查同一知识点
6. 解析需包含：正确答案的原因、错误选项的分析、相关知识点扩展
7. 用中文出题
8. 严格按以下格式输出，不要添加额外内容：

===题目1===
题干内容
A. 选项A
B. 选项B
C. 选项C
D. 选项D

===答案1===
正确答案：X
解析：详细解析内容（包含正确原因、错误分析、知识点扩展）

===题目2===
题干内容
A. 选项A
B. 选项B
C. 选项C
D. 选项D

===答案2===
正确答案：X
解析：详细解析内容

（以此类推，共5题）"""

COMPARE_SYSTEM_PROMPT = """你是医学教学专家，对比两个医学概念的异同。

要求：
1. 根据概念类型选择合适的对比维度：
   - 疾病：定义/病因/发病机制/临床表现/诊断/治疗/预后
   - 药物：分类/作用机制/适应症/不良反应/禁忌症
   - 解剖结构：位置/形态/毗邻/功能/临床意义
   - 检查方法：原理/适应症/禁忌症/操作步骤/结果判读
2. 用Markdown表格格式输出，表头为"对比维度 | 概念A | 概念B"
3. 在差异最大的维度前加 [重点] 标记
4. 表格后用1-2句话总结最核心的区别
5. 最后给出记忆技巧或助记方法
6. 标注信息来源教材"""

CASE_SYSTEM_PROMPT = """你是临床医学教学专家，基于教材知识进行病例分析教学。

要求：
1. 按临床推理流程分步分析：
   第一步 [病史分析]：提取关键病史信息，分析其临床意义
   第二步 [鉴别诊断]：列出3-5个可能的诊断，说明各自依据和可能性排序
   第三步 [辅助检查]：建议需要做哪些检查来确诊，说明每项检查的目的
   第四步 [诊断]：给出最可能的诊断及诊断依据
   第五步 [治疗]：给出治疗方案（包括一般治疗、药物治疗、手术指征等）
2. 每步引用教材段落作为依据（标注来源）
3. 用中文回答，专业术语附英文
4. 在关键临床思维处标注 [考点]
5. 回答末尾给出"临床思维要点"总结
6. 如果病例信息不足，说明还需要补充哪些病史或检查"""

MINDMAP_SYSTEM_PROMPT = """根据以下医学知识，生成Mermaid格式的思维导图。

要求：
1. 使用 mindmap 语法
2. 核心概念作为根节点
3. 层次清晰，不超过3层深度
4. 节点命名简洁（不超过15个字）
5. 突出核心概念和关键关系
6. 用中文标注
7. 只输出Mermaid语法，不要代码块标记，不要任何解释"""


# ── 对话历史管理器 ────────────────────────────────────

class ConversationHistory:
    """多轮对话上下文管理器。"""

    def __init__(self, max_turns: int = 5):
        self.history: list[tuple[str, str]] = []
        self.max_turns = max_turns

    def add_turn(self, question: str, answer: str):
        self.history.append((question, answer))
        if len(self.history) > self.max_turns:
            self.history = self.history[-self.max_turns:]

    def get_context(self, max_chars_per_msg: int = 200) -> str:
        """生成对话上下文字符串（用于拼入 LLM prompt）。"""
        if not self.history:
            return ""
        lines = []
        for q, a in self.history[-3:]:
            lines.append(f"用户: {q[:max_chars_per_msg]}")
            lines.append(f"助手: {a[:max_chars_per_msg]}")
        return "\n".join(lines)

    @property
    def turn_count(self) -> int:
        return len(self.history)

    def clear(self):
        self.history.clear()


# ── 查询重写（追问优化） ──────────────────────────────

# 代词/指代词列表，用于判断是否需要重写
_PRONOUNS = re.compile(r"(它|这个|那个|其|上述|前面|上一|该|此|该病|该疾)")

def rewrite_query(query: str, prev_queries: list[str], api_key: str = "", api_url: str = "", model: str = "") -> str:
    """当用户追问含代词时，结合上一个问题重写检索词。

    例如：上一轮 "股三角"，本轮 "它的边界有哪些"
      → 重写为 "股三角的边界有哪些"

    如果不含代词或无历史，原样返回。
    优化：优先使用规则引擎（零延迟），仅在规则失败时 fallback 到 LLM。
    """
    if not prev_queries or not _PRONOUNS.search(query):
        return query

    prev = prev_queries[-1]  # 最近一轮的问题

    # 规则引擎优先（零延迟，90% 的情况可以处理）
    prev_clean = re.sub(r"^(请|帮我|告诉我|介绍一下|讲讲|说说|解释一下|说明一下)\s*", "", prev)
    prev_clean = re.sub(r"^(解释|说明)\s*", "", prev_clean)
    prev_clean = re.sub(r"[？?。.！!，,、；;：:]+$", "", prev_clean).strip()
    # 按 "的/了/在/是" 切分，取第一个有意义片段
    parts = re.split(r"[的了在是]", prev_clean)
    core = parts[0].strip() if parts else prev_clean[:20]

    if core and len(core) > 1:
        # 直接替换代词，不调用 LLM
        result = query
        for pronoun in ["它", "这个", "那个", "其", "该病", "该疾", "上述", "前面", "上一"]:
            result = result.replace(pronoun, core)
        if result != query:
            return result

    # 仅在规则失败时才调用 LLM（<10% 的情况）
    if api_key:
        try:
            r = requests.post(
                api_url or DEFAULT_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or DEFAULT_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"上一个问题: {prev}\n"
                                f"当前问题: {query}\n\n"
                                "请将当前问题中的代词（它、这个、该病等）替换为上一个问题中的具体概念，"
                                "输出一个可以直接用于搜索的完整问题。只输出重写后的问题，不要解释。"
                            ),
                        }
                    ],
                    "temperature": 0.0,
                    "max_tokens": 100,
                    "reasoning": {"max_tokens": 128},
                },
                timeout=15,
            )
            if r.status_code == 200:
                rewritten = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if rewritten and len(rewritten) < 200:
                    return rewritten
        except Exception:
            pass  # fallback 到简单拼接

    # 最终 fallback：简单拼接
    if core and len(core) > 1:
        return f"{core} {query}"
    return query


# ── LLM 调用 ──────────────────────────────────────────

DEFAULT_API_URL = "https://open.cherryin.net/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash(free)"


def build_user_message(hits: list[dict], query: str, conv_context: str = "") -> str:
    """构建 LLM 用户消息（教材段落 + 对话上下文 + 问题）。"""
    ctx = "\n\n---\n\n".join(
        f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}"
        for i, h in enumerate(hits[:5])
    )
    user_msg = f"教材段落:\n{ctx}\n\n问题: {query}"
    if conv_context:
        user_msg = (
            "对话历史（参考代词指代，如上一轮提到的概念）:\n"
            f"{conv_context}\n\n\n{user_msg}"
        )
    return user_msg


def call_llm(
    api_key: str,
    user_msg: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: int = 30,
    max_retries: int = 2,
) -> str:
    """调用 OpenAI-compatible Chat API 生成回答（非流式）。支持重试和 429 退避。"""
    import time
    import random

    for attempt in range(max_retries + 1):
        try:
            r = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "reasoning": {"max_tokens": 128},
                },
                timeout=timeout,
            )
            if r.status_code == 429:
                wait = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            return "⚠️ 请求超时，请稍后重试"
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
                continue
            return f"⚠️ 生成回答失败: {e}"


def call_llm_stream(
    api_key: str,
    user_msg: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: int = 30,
    max_retries: int = 2,
):
    """流式调用 Chat API，逐 token 生成。yield 每个文本片段。支持 429 重试和读取超时保护。"""
    import time
    import random

    for attempt in range(max_retries + 1):
        try:
            r = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "reasoning": {"max_tokens": 128},
                    "stream": True,
                },
                timeout=timeout,
                stream=True,
            )
            if r.status_code == 429:
                if attempt < max_retries:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                    continue
                yield f"⚠️ [429限流] 当前模型已达到速率限制。请切换到其他模型后重试。"
                return
            r.raise_for_status()
            r.encoding = "utf-8"

            last_activity = time.time()
            for line in r.iter_lines(decode_unicode=True):
                # 检查读取超时（30秒无数据则超时）
                if time.time() - last_activity > 30:
                    yield "⚠️ 流式响应超时，请重试"
                    return
                last_activity = time.time()

                if not line:
                    continue
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    choices = obj.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
            return  # 成功完成
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            yield "⚠️ 请求超时，请稍后重试"
            return
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
                continue
            yield f"⚠️ 生成回答失败: {e}"
            return


# ── 新功能函数 ─────────────────────────────────────────

def build_quiz_message(hits: list[dict], topic: str) -> str:
    """构建刷题模式的用户消息。"""
    ctx = "\n\n---\n\n".join(
        f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}"
        for i, h in enumerate(hits[:5])
    )
    return f"教材段落:\n{ctx}\n\n请根据以上教材内容，围绕「{topic}」出1道单选题。"


def build_compare_message(hits_a: list[dict], hits_b: list[dict], concept_a: str, concept_b: str) -> str:
    """构建对比学习的用户消息。"""
    ctx_a = "\n\n---\n\n".join(
        f"[A{i+1}] {h['book']}·{h['chapter']}\n{h['text']}"
        for i, h in enumerate(hits_a[:3])
    )
    ctx_b = "\n\n---\n\n".join(
        f"[B{i+1}] {h['book']}·{h['chapter']}\n{h['text']}"
        for i, h in enumerate(hits_b[:3])
    )
    return (
        f"关于「{concept_a}」的教材段落:\n{ctx_a}\n\n"
        f"关于「{concept_b}」的教材段落:\n{ctx_b}\n\n"
        f"请对比「{concept_a}」和「{concept_b}」的异同。"
    )


def build_case_message(hits: list[dict], case_desc: str) -> str:
    """构建病例分析的用户消息。"""
    ctx = "\n\n---\n\n".join(
        f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}"
        for i, h in enumerate(hits[:5])
    )
    return f"教材段落:\n{ctx}\n\n病例描述:\n{case_desc}\n\n请按临床推理流程分析此病例。"


def build_mindmap_message(hits: list[dict], topic: str) -> str:
    """构建思维导图的用户消息。"""
    ctx = "\n\n---\n\n".join(
        f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}"
        for i, h in enumerate(hits[:5])
    )
    return f"教材段落:\n{ctx}\n\n请为「{topic}」生成思维导图。"


# ── 重点总结模式 ─────────────────────────────────────────

SUMMARY_SYSTEM_PROMPT = """你是一名严谨的医学教育助手，正在为学生整理教材重点总结。

【核心约束——必须严格遵守】
1. 仅依据给定教材段落作答，禁止补充教材以外的内容，禁止虚构或推测任何信息。
2. 如果给定段落中未找到某条目的相关内容，必须明确回答"教材中未找到相关内容"，不得编造答案。
3. 每条结果必须标注出处，格式为：📖 教材名·章节名。
4. 综合多个段落时，标注所有引用来源。

【输出格式要求——按板块类型区分】

▶ 英汉互译板块：
为每条术语生成中英对照表，格式如下：
===条目1===
| 中文 | 英文 |
|------|------|
| 中文术语 | English Term |
📖 出处：教材名·章节名

===条目2===
（同上格式）

▶ 名词解释板块：
为每条术语给出定义，格式如下：
===条目1===
**术语名**：定义内容（基于教材段落概括，不超过3句话）。
📖 出处：教材名·章节名

===条目2===
（同上格式）

▶ 简答题板块：
为每道问题给出分点结构化答案，格式如下：
===条目1===
**问题**：题目原文
**答案**：
1. 第一点
2. 第二点
3. 第三点
📖 出处：教材名·章节名

===条目2===
（同上格式）

【注意事项】
- 使用中文回答，专业术语附英文原文。
- 化学方程式和数学公式使用 LaTeX 格式（行内 $...$，独立 $$...$$）。
- 不要添加总结、前言或结语，直接按条目输出。"""

# 板块类型常量
SUMMARY_SECTION_TRANSLATION = "translation"
SUMMARY_SECTION_TERMS = "terms"
SUMMARY_SECTION_QA = "qa"

# 单次 user message 最大字符数（控制 prompt 长度，避免超长导致超时或截断）
_SUMMARY_MAX_MSG_CHARS = 6000
# 每个检索段落截取最大字符数
_SUMMARY_MAX_HIT_CHARS = 400
# 每批最大条目数（硬上限，实际按字符数动态切分）
_SUMMARY_MAX_ITEMS_PER_BATCH = 10


def _format_hits_for_summary(hits: list[dict], max_chars: int = _SUMMARY_MAX_HIT_CHARS) -> str:
    """将检索结果格式化为重点总结消息中的段落引用。"""
    if not hits:
        return '（未检索到相关教材段落，请回答"教材中未找到相关内容"）'
    parts = []
    for i, h in enumerate(hits):
        book = h.get("book", "未知教材")
        chapter = h.get("chapter", "未知章节")
        text = h.get("text", "")[:max_chars]
        parts.append(f"[{i+1}] {book}·{chapter}\n{text}")
    return "\n\n".join(parts)


def build_summary_message(
    section_type: str,
    items_with_hits: list[dict],
) -> str:
    """构建重点总结的用户消息（单批次）。

    Args:
        section_type: 板块类型，"translation" / "terms" / "qa"
        items_with_hits: 条目列表，每项格式:
            {
                "item": "条目文本",
                "hits": [{"text": ..., "book": ..., "chapter": ..., "similarity": ...}, ...]
            }
            hits 可为空列表（表示未检索到相关段落）。

    Returns:
        构建好的 user message 字符串。
    """
    section_labels = {
        SUMMARY_SECTION_TRANSLATION: "英汉互译",
        SUMMARY_SECTION_TERMS: "名词解释",
        SUMMARY_SECTION_QA: "简答题",
    }
    label = section_labels.get(section_type, "重点总结")

    parts = [f"以下是「{label}」板块的条目清单，每条附有从教材中检索到的相关段落。请严格按系统提示的格式逐条作答。\n"]

    for idx, entry in enumerate(items_with_hits, 1):
        item_text = entry.get("item", "")
        hits = entry.get("hits", [])
        hits_text = _format_hits_for_summary(hits)
        parts.append(f"===条目{idx}===\n{item_text}\n\n检索段落:\n{hits_text}\n")

    parts.append(f"\n请为以上{len(items_with_hits)}条逐一作答，使用 ===条目N=== 标记分隔。")
    return "\n".join(parts)


def split_summary_batches(
    items_with_hits: list[dict],
    max_chars: int = _SUMMARY_MAX_MSG_CHARS,
    max_items: int = _SUMMARY_MAX_ITEMS_PER_BATCH,
) -> list[list[dict]]:
    """将条目列表按字符数和条目数切分为多个批次，保证每批 user message 不超长。

    Args:
        items_with_hits: 完整条目列表。
        max_chars: 单批次 user message 最大字符数。
        max_items: 单批次最大条目数。

    Returns:
        分批后的条目列表（list of list）。
    """
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    current_chars = 0

    for entry in items_with_hits:
        # 估算本条目字符数
        item_chars = len(entry.get("item", ""))
        hits_chars = sum(len(h.get("text", "")) for h in entry.get("hits", []))
        entry_chars = item_chars + min(hits_chars, _SUMMARY_MAX_HIT_CHARS * len(entry.get("hits", []))) + 80  # 80 for formatting overhead

        # 检查是否需要切分
        if current_batch and (
            current_chars + entry_chars > max_chars or len(current_batch) >= max_items
        ):
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

        current_batch.append(entry)
        current_chars += entry_chars

    if current_batch:
        batches.append(current_batch)

    return batches if batches else [[]]


def parse_summary_response(
    response_text: str,
    items: list[str],
    section_type: str,
) -> list[dict]:
    """从 LLM 响应中按条目拆分结果。

    约定 LLM 使用 ===条目N=== 分隔标记。解析失败时降级为整段展示。

    Args:
        response_text: LLM 返回的完整文本。
        items: 原始条目文本列表（用于对齐编号）。
        section_type: 板块类型。

    Returns:
        列表，每项 {"item": "原始条目", "result": "LLM 对该条目的回答"}。
        解析失败时返回单条 {"item": "全部", "result": response_text}。
    """
    if not response_text or not response_text.strip():
        return [{"item": "全部", "result": "⚠️ LLM 未返回有效内容"}]

    # 尝试按 ===条目N=== 分隔符拆分
    pattern = re.compile(r"={2,3}条目\s*(\d+)\s*={2,3}")
    splits = pattern.split(response_text)

    # split 结果: [前导文本, 编号1, 内容1, 编号2, 内容2, ...]
    if len(splits) >= 3:
        results = []
        # splits[0] 是第一个标记前的文本（通常为空或前言，忽略）
        i = 1
        while i < len(splits) - 1:
            try:
                entry_num = int(splits[i])
            except (ValueError, TypeError):
                i += 2
                continue
            content = splits[i + 1].strip()
            # 对齐原始条目
            item_text = items[entry_num - 1] if 0 < entry_num <= len(items) else f"条目{entry_num}"
            results.append({"item": item_text, "result": content})
            i += 2

        if results:
            return results

    # 解析失败：降级为整段展示
    return [{"item": "全部（解析降级）", "result": response_text}]


def generate_summary_for_section(
    section_type: str,
    items_with_hits: list[dict],
    api_key: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 45,
    max_retries: int = 2,
) -> list[dict]:
    """按板块批量调用 LLM 生成重点总结（非流式）。

    自动分批，复用 call_llm 的超时保护与 429 重试。

    Args:
        section_type: 板块类型 "translation" / "terms" / "qa"。
        items_with_hits: 条目+检索结果列表。
        api_key: API 密钥。
        api_url: API 地址。
        model: 模型 ID。
        temperature: 温度参数（总结偏低创造性，默认 0.2）。
        max_tokens: 单次最大 token。
        timeout: 单次请求超时秒数。
        max_retries: 最大重试次数。

    Returns:
        所有批次的解析结果合并列表。
    """
    if not items_with_hits:
        return []

    batches = split_summary_batches(items_with_hits)
    all_results: list[dict] = []

    for batch in batches:
        if not batch:
            continue
        user_msg = build_summary_message(section_type, batch)
        items_list = [entry.get("item", "") for entry in batch]

        response = call_llm(
            api_key=api_key,
            user_msg=user_msg,
            api_url=api_url,
            model=model,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        )

        parsed = parse_summary_response(response, items_list, section_type)
        all_results.extend(parsed)

    return all_results


def generate_summary_for_section_stream(
    section_type: str,
    items_with_hits: list[dict],
    api_key: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 45,
    max_retries: int = 2,
):
    """按板块批量调用 LLM 生成重点总结（流式）。

    yield (batch_index, chunk_text) 元组，供 UI 层逐批渲染。
    复用 call_llm_stream 的超时保护与 429 重试。

    Args:
        参数同 generate_summary_for_section。

    Yields:
        (batch_index: int, chunk: str) — 第几批（0-based）及文本片段。
    """
    if not items_with_hits:
        return

    batches = split_summary_batches(items_with_hits)

    for batch_idx, batch in enumerate(batches):
        if not batch:
            continue
        user_msg = build_summary_message(section_type, batch)

        for chunk in call_llm_stream(
            api_key=api_key,
            user_msg=user_msg,
            api_url=api_url,
            model=model,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            max_retries=max_retries,
        ):
            yield (batch_idx, chunk)
