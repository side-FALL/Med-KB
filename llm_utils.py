"""医学教材知识库 — 共享 LLM 工具（synthesize + 对话历史 + 查询重写 + 流式输出）

供 deploy/app.py 和 modelscope/app.py 导入，避免重复代码。
"""

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

    # 简单 fallback：拼接上一轮主题词 + 当前问题
    return f"{prev} {query}"


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

        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                import json
                chunk = json.loads(data)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
            except (ValueError, KeyError, IndexError):
                continue
    except Exception as e:
        yield f"⚠️ 生成回答失败: {e}"
