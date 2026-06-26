"""Medical Agent - 轻量版（不依赖 LangChain）

使用 OpenAI API 直接实现 ReAct 模式，无需 LangChain。
"""

import json
import os
from typing import Optional

import requests

from tools import get_tools

# ── ReAct Prompt ─────────────────────────────────────

REACT_PROMPT = """你是一名严谨的医学教育智能助手，能够使用工具回答医学问题。

可用工具：
{tools_desc}

## 推理格式（严格遵守）

每次推理只能调用一个工具，按以下格式输出：

Thought: 分析问题，决定下一步行动
Action: 工具名称（必须是上述工具之一）
Action Input: 工具输入参数（JSON格式，如 {{"drug_name": "阿莫西林", "weight_kg": 70}}）

Observation: 工具返回结果（由系统自动填入，你不需要写）

继续推理或给出最终回答：

Thought: 根据观察结果，判断是否需要继续使用工具
Action: ...
Action Input: ...

当信息充分时，输出最终回答：

Thought: 我现在已经收集到足够信息，可以给出最终回答了
Final Answer: 完整的最终回答

## 回答要求

1. 使用中文回答，专业术语附英文原文（如：心力衰竭 Heart Failure）
2. 化学方程式使用LaTeX格式：$$CH_3 + NAD^+ \\rightarrow CO_2$$
3. 使用层次化结构（标题、分点）组织内容
4. 如果工具返回的信息不足，诚实说明并给出已有信息的初步回答
5. 药物剂量仅供教学参考，需提醒"实际用药请遵医嘱"

## 工具选择指南

- 查找疾病/药物/解剖知识 → search_textbook
- 计算药物剂量 → calculate_dosage（需提供药物名和体重）
- 查询检验正常值 → get_normal_values
- 对比两个概念 → compare_concepts（需提供两个概念名）
- 分析临床病例 → analyze_case（需提供完整病例描述）

开始！

Question: {input}
"""

# ── API 配置 ─────────────────────────────────────────

API_URL = "https://open.cherryin.net/v1/chat/completions"


def _call_llm(api_key: str, messages: list, model: str, api_url: str = API_URL, temperature: float = 0.3, max_retries: int = 3) -> str:
    """调用 LLM API，支持重试"""
    import time

    for attempt in range(max_retries):
        try:
            r = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 1500,
                },
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            raise
        except requests.exceptions.RequestException:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            raise


def _call_llm_stream(api_key: str, messages: list, model: str, api_url: str = API_URL, temperature: float = 0.3, max_retries: int = 3):
    """流式调用 LLM API，yield 每个 token"""
    import time

    for attempt in range(max_retries):
        try:
            r = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": 1500,
                    "stream": True,
                },
                timeout=30,
                stream=True,
            )
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
            return  # 成功完成，退出重试循环
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            yield f"⚠️ 请求超时，已重试 {max_retries} 次"
            return
        except requests.exceptions.RequestException:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            yield f"⚠️ 请求失败，已重试 {max_retries} 次"
            return


def _parse_action(text: str) -> tuple[Optional[str], Optional[str]]:
    """从 LLM 输出中解析 Action 和 Action Input"""
    lines = text.strip().split("\n")
    action = None
    action_input = None

    for line in lines:
        line = line.strip()
        if line.startswith("Action:"):
            action = line[len("Action:"):].strip()
        elif line.startswith("Action Input:"):
            action_input = line[len("Action Input:"):].strip()

    return action, action_input


def _parse_final_answer(text: str) -> Optional[str]:
    """从 LLM 输出中解析 Final Answer"""
    if "Final Answer:" in text:
        return text.split("Final Answer:")[1].strip()
    return None


def run_agent(
    query: str,
    api_key: str,
    model: str = "deepseek/deepseek-v4-flash(free)",
    api_url: str = API_URL,
    max_steps: int = 5,
) -> dict:
    """运行轻量版 Agent（不依赖 LangChain）

    Args:
        query: 用户问题
        api_key: API 密钥
        model: 模型名称
        api_url: API 端点 URL
        max_steps: 最大推理步数

    Returns:
        dict: {"output": str, "steps": list, "error": str|None}
    """
    if not api_key:
        return {"output": "", "steps": [], "error": "未配置 API Key"}

    try:
        # 获取工具列表
        tools = get_tools()
        tools_desc = "\n".join([
            f"- {t.name}: {t.description}" for t in tools
        ])
        tools_map = {t.name: t for t in tools}

        # 初始化对话
        messages = [
            {
                "role": "user",
                "content": REACT_PROMPT.format(tools_desc=tools_desc, input=query)
            }
        ]

        steps = []

        for step in range(max_steps):
            # 调用 LLM
            response = _call_llm(api_key, messages, model, api_url=api_url)
            messages.append({"role": "assistant", "content": response})

            # 检查是否有 Final Answer
            final_answer = _parse_final_answer(response)
            if final_answer:
                return {
                    "output": final_answer,
                    "steps": steps,
                    "error": None,
                }

            # 解析 Action
            action, action_input = _parse_action(response)

            if not action:
                # 没有 Action，把整个回复作为最终回答
                return {
                    "output": response,
                    "steps": steps,
                    "error": None,
                }

            # 执行工具
            if action not in tools_map:
                observation = f"错误：未知工具 '{action}'。可用工具：{', '.join(tools_map.keys())}"
            else:
                try:
                    # 尝试解析 JSON 输入
                    if action_input:
                        try:
                            input_dict = json.loads(action_input)
                            if isinstance(input_dict, dict):
                                # StructuredTool 需要关键字参数
                                observation = tools_map[action].invoke(input_dict)
                            else:
                                observation = tools_map[action].invoke(action_input)
                        except json.JSONDecodeError:
                            observation = tools_map[action].invoke(action_input)
                    else:
                        observation = tools_map[action].invoke("")
                except Exception as e:
                    observation = f"工具执行出错：{e}"

            # 记录步骤
            steps.append({
                "tool": action,
                "input": action_input or "",
                "output": str(observation)[:500],
            })

            # 将观察结果加入对话
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请继续思考并给出回答。"
            })

        # 达到最大步数
        return {
            "output": "抱歉，达到最大推理步数未能得出结论。请尝试简化问题。",
            "steps": steps,
            "error": None,
        }

    except Exception as e:
        return {
            "output": "",
            "steps": [],
            "error": str(e),
        }


def run_agent_stream(
    query: str,
    api_key: str,
    model: str = "deepseek/deepseek-v4-flash(free)",
    api_url: str = API_URL,
    max_steps: int = 5,
    conversation_history: Optional[list[tuple[str, str]]] = None,
):
    """流式运行智能体，yield 每个 token

    Args:
        query: 用户问题
        api_key: API 密钥
        model: 模型名称
        api_url: API 端点 URL
        max_steps: 最大推理步数
        conversation_history: 对话历史 [(问题, 回答), ...]

    Yields:
        dict: {"type": "step"|"token"|"error", "data": ...}
            - "step": 推理步骤 {"tool": ..., "input": ..., "output": ...}
            - "token": 最终回答的 token
            - "error": 错误信息
    """
    if not api_key:
        yield {"type": "error", "data": "未配置 API Key"}
        return

    try:
        # 获取工具列表
        tools = get_tools()
        tools_desc = "\n".join([
            f"- {t.name}: {t.description}" for t in tools
        ])
        tools_map = {t.name: t for t in tools}

        # 构建输入（含历史上下文）
        input_text = query
        if conversation_history:
            recent = conversation_history[-3:]
            history_lines = []
            for q, a in recent:
                history_lines.append(f"用户: {q}")
                history_lines.append(f"助手: {a[:200]}")
            history_text = "\n".join(history_lines)
            input_text = f"对话历史:\n{history_text}\n\n当前问题: {query}"

        # 初始化对话
        messages = [
            {
                "role": "user",
                "content": REACT_PROMPT.format(tools_desc=tools_desc, input=input_text)
            }
        ]

        for step in range(max_steps):
            # 调用 LLM（非流式，用于推理阶段）
            response = _call_llm(api_key, messages, model, api_url=api_url)
            messages.append({"role": "assistant", "content": response})

            # 检查是否有 Final Answer
            final_answer = _parse_final_answer(response)
            if final_answer:
                # 流式输出最终回答
                # 将 final_answer 分段 yield
                for i in range(0, len(final_answer), 10):
                    yield {"type": "token", "data": final_answer[i:i+10]}
                return

            # 解析 Action
            action, action_input = _parse_action(response)

            if not action:
                # 没有 Action，把整个回复作为最终回答（流式）
                for i in range(0, len(response), 10):
                    yield {"type": "token", "data": response[i:i+10]}
                return

            # 执行工具
            if action not in tools_map:
                observation = f"错误：未知工具 '{action}'。可用工具：{', '.join(tools_map.keys())}"
            else:
                try:
                    if action_input:
                        try:
                            input_dict = json.loads(action_input)
                            if isinstance(input_dict, dict):
                                observation = tools_map[action].invoke(input_dict)
                            else:
                                observation = tools_map[action].invoke(action_input)
                        except json.JSONDecodeError:
                            observation = tools_map[action].invoke(action_input)
                    else:
                        observation = tools_map[action].invoke("")
                except Exception as e:
                    observation = f"工具执行出错：{e}"

            # 记录步骤
            yield {
                "type": "step",
                "data": {
                    "tool": action,
                    "input": action_input or "",
                    "output": str(observation)[:500],
                }
            }

            # 将观察结果加入对话
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请继续思考并给出回答。"
            })

        # 达到最大步数
        yield {"type": "token", "data": "抱歉，达到最大推理步数未能得出结论。请尝试简化问题。"}

    except Exception as e:
        yield {"type": "error", "data": str(e)}
