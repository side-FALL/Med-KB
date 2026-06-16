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

def rewrite_query(query: str, prev_queries: list[str], api_key: str = "") -> str:
    """当用户追问含代词时，结合上一个问题重写检索词。

    例如：上一轮 "股三角"，本轮 "它的边界有哪些"
      → 重写为 "股三角的边界有哪些"

    如果不含代词或无历史，原样返回。
    """
    if not prev_queries or not _PRONOUNS.search(query):
        return query

    prev = prev_queries[-1]  # 最近一轮的问题

    # 如果 API key 可用，用 LLM 重写（更准确）
    if api_key:
        try:
            r = requests.post(
                "https://open.cherryin.net/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek/deepseek-v4-flash(free)",
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
                },
                timeout=15,
            )
            if r.status_code == 200:
                rewritten = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if rewritten and len(rewritten) < 200:
                    return rewritten
        except Exception:
            pass  # fallback 到简单拼接

    # 简单 fallback：从上一轮提取核心名词短语
    # 先去掉常见祈使句前缀（整体匹配）
    prev_clean = re.sub(r"^(请|帮我|告诉我|介绍一下|讲讲|说说|解释一下|说明一下)\s*", "", prev)
    prev_clean = re.sub(r"^(解释|说明)\s*", "", prev_clean)
    prev_clean = re.sub(r"[？?。.！!，,、；;：:]+$", "", prev_clean).strip()
    # 按 "的/了/在/是" 切分，取第一个有意义片段
    parts = re.split(r"[的了在是]", prev_clean)
    core = parts[0].strip() if parts else prev_clean[:20]
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
    timeout: int = 90,
) -> str:
    """调用 OpenAI-compatible Chat API 生成回答（非流式）。"""
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
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ 生成回答失败: {e}"


def call_llm_stream(
    api_key: str,
    user_msg: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    timeout: int = 120,
):
    """流式调用 Chat API，逐 token 生成。yield 每个文本片段。"""
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
                "stream": True,
            },
            timeout=timeout,
            stream=True,
        )
        r.raise_for_status()
        r.encoding = "utf-8"

        for line in r.iter_lines(decode_unicode=True):
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
    except Exception as e:
        yield f"⚠️ 生成回答失败: {e}"


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
