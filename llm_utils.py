"""医学教材知识库 — 共享 LLM 工具（synthesize + 对话历史）

供 deploy/app.py 和 modelscope/app.py 导入，避免重复代码。
"""

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


# ── 对话历史管理器 ────────────────────────────────────

class ConversationHistory:
    """多轮对话上下文管理器。

    用法:
        conv = ConversationHistory()
        conv.add_turn("心衰的机制", "...")  # 自动保存
        context = conv.get_context()        # 生成最近3轮上下文
        conv.turn_count                     # 已进行轮数
        conv.clear()                        # 清空
    """

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
        for q, a in self.history[-3:]:  # 最多最近3轮
            lines.append(f"用户: {q[:max_chars_per_msg]}")
            lines.append(f"助手: {a[:max_chars_per_msg]}")
        return "\n".join(lines)

    @property
    def turn_count(self) -> int:
        return len(self.history)

    def clear(self):
        self.history.clear()


# ── LLM 调用 ──────────────────────────────────────────

# 默认 CherryIn API 配置
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
    """调用 OpenAI-compatible Chat API 生成回答。"""
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
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ 生成回答失败: {e}"
