---
domain: nlp
tags:
  - medical
  - knowledge-base
  - rag
  - education
license: Apache License 2.0
---

# 🏥 医学教材知识库

基于RAG（检索增强生成）的医学教材智能问答系统，支持自然语言检索8本医学教材，AI自动生成回答。

## ✨ 特性

- 📚 **8本医学教材**：覆盖解剖、生理、病理、药理等核心科目
- 🔍 **智能检索**：基于BGE-M3向量模型，语义相似度匹配
- 💡 **AI回答**：DeepSeek V4 Flash自动生成综合回答
- 🆓 **完全免费**：嵌入和回答模型均使用免费API
- 📱 **多端适配**：支持PC和手机浏览器访问
- 🔄 **多轮对话**：支持连续提问，上下文记忆

## 📖 教材列表

| 教材 | 块数 | 类型 |
|------|------|------|
| 系统解剖学 | 406 | 文字版 |
| 局部解剖学（第10版） | 305 | 文字版 |
| 断层解剖学 | 667 | 扫描版 |
| 生理学教材（十版） | 480 | 文字版 |
| 生物化学与分子生物学（第10版） | 613 | 文字版 |
| 医学免疫学（第8版） | 255 | 文字版 |
| 人体寄生虫学（第10版） | 344 | 扫描版 |
| 病理学（第10版） | 418 | 文字版 |
| **总计** | **3492** | |

## 🛠️ 技术栈

- **向量模型**：CherryIN BGE-M3 (免费)
- **OCR识别**：CherryIN DeepSeek OCR (免费)
- **回答生成**：CherryIN DeepSeek V4 Flash (免费)
- **向量存储**：NumPy (轻量级)
- **前端框架**：Streamlit

## 🚀 使用方法

1. 在输入框输入医学问题
2. 点击"搜索"或按回车
3. 查看检索到的教材段落
4. 阅读AI生成的综合回答

## 💡 示例问题

- 心衰的病理生理机制是什么？
- 股三角的构成和内容物
- 疟原虫的生活史
- 炎症的基本病理变化
- β受体阻滞剂的药理作用

## 📊 版本历史

- **v1.0.0** (2025-06-01): 初始版本，8本教材，优化UI，多模型选择

## 🔧 本地部署

```bash
# 克隆项目
git clone https://www.modelscope.cn/studios/sideFALL/med-kb.git
cd med-kb

# 安装依赖
pip install -r requirements.txt

# 设置环境变量
export CS_API_KEY="your-api-key"

# 运行
streamlit run app.py
```

## 📝 更新日志

### v1.0.0 (2025-06-01)
- ✅ 初始版本发布
- ✅ 8本医学教材入库
- ✅ 优化UI界面（渐变背景、聊天气泡、来源卡片）
- ✅ 多模型选择（DeepSeek V4 Flash / V3.2）
- ✅ 搜索历史记录
- ✅ 响应式布局（手机适配）
- ✅ 向量预归一化（加速检索）

## 📄 许可证

Apache License 2.0

## 👨‍💻 作者

sideFALL

## 🔗 链接

- [ModelScope主页](https://modelscope.cn/studios/sideFALL/med-kb)
- [项目文档](https://modelscope.cn/docs)
