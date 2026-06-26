"""Medical Agent using LangChain ReAct pattern.

Provides a ReAct agent that can use medical tools to answer questions,
calculate dosages, look up lab values, compare concepts, and analyze cases.
"""

from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate

# 兼容不同版本的 LangChain
try:
    from langchain.agents import AgentExecutor, create_react_agent
except ImportError:
    try:
        from langchain.agents.agent import AgentExecutor
        from langchain.agents.react.agent import create_react_agent
    except ImportError:
        # 最后的 fallback
        from langchain.agents import AgentExecutor
        from langchain.agents import create_react_agent

from tools import get_tools

# ── ReAct prompt template ────────────────────────────────
AGENT_PROMPT = """你是一名医学教育智能助手，能够使用以下工具回答医学问题。

可用工具:
{tools}

工具名称列表: [{tool_names}]

请严格使用以下格式回答问题:

Question: 用户提出的问题
Thought: 思考应该采取什么行动
Action: 要使用的工具名称，必须是 [{tool_names}] 中的一个
Action Input: 工具的输入参数
Observation: 工具返回的结果
... (Thought/Action/Action Input/Observation 可以重复多次)
Thought: 我现在已经知道最终答案了
Final Answer: 对原始问题的最终回答

注意事项:
1. 优先使用 search_textbook 搜索教材获取权威信息
2. 涉及药物剂量计算时使用 calculate_dosage
3. 涉及检验正常值时使用 get_normal_values
4. 需要对比两个概念时使用 compare_concepts
5. 遇到病例分析时使用 analyze_case
6. 回答用中文，专业术语附英文
7. 如果工具返回的信息不足，诚实说明并给出已有信息的初步回答

开始!

Question: {input}
Thought:{agent_scratchpad}"""


def create_medical_agent(
    api_key: str,
    model: str = "deepseek/deepseek-v4-flash(free)",
    base_url: str = "https://open.cherryin.net/v1",
    temperature: float = 0.3,
    max_iterations: int = 5,
    verbose: bool = True,
) -> AgentExecutor:
    """Create a medical ReAct agent with tools.

    Args:
        api_key: API key for the LLM service.
        model: Model identifier (default: DeepSeek V4 Flash free).
        base_url: API base URL (default: CherryIN endpoint).
        temperature: LLM temperature (default: 0.3).
        max_iterations: Maximum agent reasoning steps (default: 5).
        verbose: Whether to print agent's reasoning process (default: True).

    Returns:
        An AgentExecutor ready to invoke with queries.
    """
    llm = ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
    )

    tools = get_tools()
    prompt = PromptTemplate.from_template(AGENT_PROMPT)
    agent = create_react_agent(llm, tools, prompt)

    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=verbose,
        max_iterations=max_iterations,
        handle_parsing_errors=True,
        return_intermediate_steps=True,
    )
    return executor


def run_agent(
    query: str,
    api_key: str,
    model: str = "deepseek/deepseek-v4-flash(free)",
    api_url: str = "https://open.cherryin.net/v1",
) -> dict:
    """Run the medical agent with a query.

    Args:
        query: The medical question or case description.
        api_key: API key for the LLM service.
        model: Model identifier (default: DeepSeek V4 Flash free).
        api_url: API base URL (default: CherryIN endpoint).

    Returns:
        A dict with keys:
            - 'output': The final answer string.
            - 'steps': List of (tool_name, tool_input, tool_output) tuples.
            - 'error': Error message if any, else None.
    """
    try:
        executor = create_medical_agent(api_key, model=model, base_url=api_url)
        result = executor.invoke({"input": query})

        # Extract intermediate steps for UI display
        steps = []
        for action, observation in result.get("intermediate_steps", []):
            steps.append({
                "tool": action.tool,
                "input": action.tool_input,
                "output": str(observation)[:500],
            })

        return {
            "output": result["output"],
            "steps": steps,
            "error": None,
        }
    except Exception as e:
        return {
            "output": "",
            "steps": [],
            "error": str(e),
        }
