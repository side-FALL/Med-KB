"""医学教材知识库 — 共享 LLM 工具（synthesize + 对话历史 + 查询重写 + 流式输出）

供 deploy/app.py 和 modelscope/app.py 导入，避免重复代码。
"""

import json
import re
import requests

# ── System prompts ────────────────────────────────────

SYSTEM_PROMPT = """你是医学知识助手。根据教材段落回答用户问题。

要求：
1. 综合多个段落信息，给出准确、有条理的回答
2. 标注信息来源（如[1][2]）
3. 如果检索结果不足，诚实说明
4. 用中文回答，专业术语可附英文
5. 回答末尾列出参考来源"""

COMPACT_SYSTEM_PROMPT = (
    "你是医学知识助手。根据教材段落回答用户问题。"
    "要求：综合段落给出准确回答，标注来源，中文回答，列出参考来源。"
)

EXAM_SYSTEM_PROMPT = """你是医学知识助手。根据教材段落回答用户问题。

要求：
1. 综合多个段落信息，给出准确、有条理的回答
2. 标注信息来源（如[1][2]）
3. 如果检索结果不足，诚实说明
4. 用中文回答，专业术语可附英文
5. 回答末尾列出参考来源
6. 用以下标记区分知识点重要性：
   ⭐ 高频考点（执医/考研常考）
   📌 核心知识点（必须掌握）
   ⚠️ 易混淆点（容易出错）
7. 回答末尾用"📋 考点清单"汇总本题涉及的重要知识点"""

QUIZ_SYSTEM_PROMPT = """你是医学出题专家。根据教材段落生成高质量医学题目。

要求：
1. 生成1道单选题（4个选项A/B/C/D，1个正确答案）
2. 题目考查核心知识点，干扰项需合理且有迷惑性
3. 给出正确答案和详细解析
4. 解析中标注知识点来源教材
5. 用中文出题
6. 按以下格式输出：

【题目】
...

A. ...
B. ...
C. ...
D. ...

【正确答案】X

【解析】
..."""

COMPARE_SYSTEM_PROMPT = """你是医学教学专家。对比两个医学概念的异同。

要求：
1. 从多个维度进行结构化对比（定义/病因/机制/临床表现/诊断/治疗等，根据概念类型选择合适维度）
2. 用Markdown表格格式输出，表头为"维度 | 概念A | 概念B"
3. 突出关键差异点，在差异最大的维度前加 ⭐ 标记
4. 最后用1-2句话总结最核心的区别
5. 标注信息来源教材"""

CASE_SYSTEM_PROMPT = """你是临床医学教学专家。根据教材知识进行病例分析教学。

要求：
1. 按临床推理流程分步分析：
   第一步【鉴别诊断】：列出3-5个可能的诊断，说明各自依据
   第二步【辅助检查】：建议需要做哪些检查来确诊
   第三步【诊断】：给出最可能的诊断及诊断依据
   第四步【治疗】：给出治疗方案
2. 每步引用教材段落作为依据（标注来源）
3. 用中文回答，专业术语附英文
4. 在关键临床思维处标注 ⭐ 考点"""

MINDMAP_SYSTEM_PROMPT = """根据以下医学知识，生成Mermaid格式的思维导图。

要求：
1. 使用 mindmap 语法
2. 层次清晰，不超过3层深度
3. 突出核心概念和关键关系
4. 用中文标注
5. 只输出Mermaid语法，不要代码块标记，不要任何解释"""


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
