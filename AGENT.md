# AGENT.md — 医学教材知识库

## 项目概述

基于 RAG（检索增强生成）的医学教材智能问答系统，支持 43 本医学教材、17357 个文本块的语义检索和 AI 回答。

**核心能力**：智能问答、自测刷题、对比学习、病例分析、考点标注

## 目录结构

```
med-kb/
├── modelscope/          # 魔塔部署版（主版本）
│   ├── app.py           # Streamlit 主应用
│   ├── llm_utils.py     # LLM 工具（prompt、调用、消息构建）
│   ├── __init__.py      # 版本号
│   ├── requirements.txt # 依赖
│   ├── setup.sh         # 魔塔启动脚本（git lfs pull）
│   ├── README.md        # 项目文档
│   ├── vectors.npz      # 全量向量（67.8MB，备用）
│   ├── metadata.json    # 全量元数据（备用）
│   └── books/           # 按教材拆分的数据（43个 .npz + .json）
│       ├── manifest.json        # 教材清单（名称、文件名、块数）
│       ├── 内科学__第10版.npz   # 单本教材向量
│       └── 内科学__第10版.json  # 单本教材文档+元数据
├── deploy/              # 早期部署版（仅向量检索）
├── data/                # 本地版数据
├── split_books.py       # 数据拆分脚本
├── config.py            # 本地版配置
├── query.py             # 本地版查询
├── ingest.py            # 本地版数据导入
└── web_ui.py            # 本地版 Web 界面
```

## 版本规则

采用语义化版本 `x.y.z`：

| 类型 | 规则 | 示例 |
|------|------|------|
| 小版本 | z+1，修复 bug、优化 | 2.1.0 → 2.1.1 |
| 中版本 | y+1,z→0，增删功能 | 2.1.1 → 2.2.0 |
| 大版本 | x+1,y→0,z→0，重构/大改 | 2.x.x → 3.0.0 |

**要求**：
- commit message 和 tag message 必须包含版本更新说明
- 版本号更新后必须推送（`git push origin master --tags`）

## 关键技术决策

### 数据加载
- 启动时只读 `books/manifest.json`（几KB），不加载全量数据
- 搜索时按选中教材加载对应的小文件（每本 1-4MB）
- `@st.cache_resource` 缓存已加载的教材

### 检索策略
- **两阶段检索**：先向量取 top-50 候选，再对候选做 BM25 关键词匹配
- BM25 无 IDF 加权，仅计算 token 匹配比例
- 混合权重 α=0.7（向量 70% + BM25 30%）

### Streamlit 开发规范

**⚠️ 关键限制**：Streamlit widget 的 `key` 绑定 session state，widget 渲染后不能手动修改同名 key。

```python
# ❌ 错误：widget key="q"，渲染后赋值会报错
q = st.text_input("输入", key="q")
st.session_state.q = "新值"  # StreamlitAPIException

# ✅ 正确：使用不同的 key 存储
q = st.text_input("输入", key="q")
st.session_state.last_query = "新值"  # 用不同 key
```

**变量作用域**：
- 侧边栏变量不能引用主界面定义的变量（执行顺序问题）
- widget 默认值用 `st.session_state.get("key", default)` 而非直接引用变量

**CSS 注意事项**：
- `header {visibility: hidden;}` 会隐藏侧边栏展开/折叠按钮
- 仅隐藏 `#MainMenu` 和 `footer` 即可

## 文件说明

### modelscope/app.py
主应用文件，包含：
- 数据加载（manifest + 按需加载）
- 混合检索（向量 + BM25）
- 4 个模式的 UI（问答/刷题/对比/病例）
- 侧边栏设置

### modelscope/llm_utils.py
共享 LLM 工具，包含：
- 6 个 SYSTEM_PROMPT（默认、考点、刷题、对比、病例、思维导图）
- 4 个消息构建函数（问答、刷题、对比、病例）
- LLM 调用（流式/非流式）
- 查询重写（代词指代解析）
- 对话历史管理器（未使用，保留备用）

### split_books.py
数据拆分脚本，将 `vectors.npz` + `metadata.json` 按教材拆分到 `books/` 目录。

运行：`python split_books.py`

## 部署

**魔塔社区**：https://modelscope.cn/studios/sideFALL/med-kb

```bash
cd modelscope
git add <files>
git commit -m "v2.x.x: 更新说明"
git tag -a v2.x.x -m "v2.x.x: 更新说明"
git push origin master --tags
```

**环境变量**：在魔塔 Space Settings → Secrets 中配置 `CS_API_KEY`

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| StreamlitAPIException: session_state.xxx cannot be modified | widget key 与 session state 同名 | 改用不同的 key |
| NameError: name 'xxx' not defined | 变量未定义或作用域问题 | 检查变量定义顺序 |
| 侧边栏不显示 | CSS `header {visibility: hidden;}` | 移除该规则 |
| 页面加载慢 | 全量数据加载 | 使用按需加载（books/） |
| 搜索慢 | BM25 遍历全部文档 | 使用两阶段检索 |
