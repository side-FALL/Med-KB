"""LangChain tools for the medical knowledge base agent.

Each tool wraps existing functionality or implements new logic
for use with a LangChain ReAct agent.
"""

import json
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np
from langchain.tools import Tool
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from openai import OpenAI

from llm_utils import (
    call_llm,
    build_user_message,
    build_compare_message,
    build_case_message,
    COMPARE_SYSTEM_PROMPT,
    CASE_SYSTEM_PROMPT,
)

# ── Paths ────────────────────────────────────────────────
_APP = Path(__file__).resolve().parent
BOOKS_DIR = _APP / "books"
_MANIFEST = BOOKS_DIR / "manifest.json"

# ── Shared API client ────────────────────────────────────
_API_KEY = os.environ.get("CS_API_KEY", "")
_embed_client: Optional[OpenAI] = None


def _get_embed_client() -> OpenAI:
    """Lazy-init the embedding API client."""
    global _embed_client
    if _embed_client is None:
        _embed_client = OpenAI(api_key=_API_KEY, base_url="https://open.cherryin.net/v1")
    return _embed_client


# ── BM25 tokenization (copied from app.py) ──────────────
_STOPWORDS = set("的了是在不有我这个们他她它们和与或但而如果因为所以可以已经正在".replace(" ", ""))
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+|\d+")


def _tokenize(text: str) -> list[str]:
    """Tokenize text for BM25 scoring."""
    text = re.sub(r"[的了是在不有我这个们]", " ", text)
    tokens = [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]
    chars = re.findall(r"[\u4e00-\u9fff]", text.lower())
    for i in range(len(chars) - 1):
        bg = chars[i] + chars[i + 1]
        if bg not in _STOPWORDS:
            tokens.append(bg)
    return tokens


# ── Book data cache ──────────────────────────────────────
_book_cache: dict[str, tuple] = {}


def _load_book(book_name: str) -> tuple:
    """Load a single book's embeddings, documents, and metadata."""
    if book_name in _book_cache:
        return _book_cache[book_name]

    if not _MANIFEST.exists():
        raise FileNotFoundError("books/manifest.json 不存在，请先运行 split_books.py")

    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    if book_name not in manifest:
        raise KeyError(f"教材 '{book_name}' 不存在于清单中")

    info = manifest[book_name]
    fname = info["file"]
    emb_data = np.load(BOOKS_DIR / f"{fname}.npz")
    emb = emb_data["embeddings"].astype(np.float32)
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    with open(BOOKS_DIR / f"{fname}.json", encoding="utf-8") as f:
        meta = json.load(f)

    result = (emb, meta["documents"], meta["metadatas"])
    _book_cache[book_name] = result
    return result


def _load_all_books() -> tuple:
    """Load all books and return concatenated (embeddings, documents, metadatas)."""
    if not _MANIFEST.exists():
        raise FileNotFoundError("books/manifest.json 不存在，请先运行 split_books.py")

    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    all_emb, all_docs, all_metas = [], [], []
    for name in manifest:
        emb, docs, metas = _load_book(name)
        all_emb.append(emb)
        all_docs.extend(docs)
        all_metas.extend(metas)

    if not all_emb:
        return None, [], []
    return np.vstack(all_emb), all_docs, all_metas


# ── Hybrid search (copied from app.py) ───────────────────
def _hybrid_search(
    text: str,
    embeddings: np.ndarray,
    documents: list[str],
    metadatas: list[dict],
    k: int = 10,
    alpha: float = 0.7,
) -> list[dict]:
    """Perform hybrid vector + BM25 search."""
    client = _get_embed_client()
    r = client.embeddings.create(model="baai/bge-m3(free)", input=[text])
    qvec = np.array(r.data[0].embedding, dtype=np.float32)
    qvec = qvec / np.linalg.norm(qvec)
    vec_scores = embeddings @ qvec

    # Vector top-50 candidates
    candidate_k = min(50, len(documents))
    top_candidates = np.argsort(vec_scores)[-candidate_k:][::-1]

    # BM25 only on candidates
    query_tokens = set(_tokenize(text))
    bm25_scores = np.zeros(len(documents), dtype=np.float32)
    if query_tokens:
        q_len = len(query_tokens)
        for local_idx in top_candidates:
            doc_tokens = set(_tokenize(documents[local_idx]))
            bm25_scores[local_idx] = len(query_tokens & doc_tokens) / q_len

    hybrid_scores = alpha * vec_scores + (1 - alpha) * bm25_scores
    top = np.argsort(hybrid_scores)[-k:][::-1]
    hits = []
    for local_idx in top:
        s = float(hybrid_scores[local_idx])
        hits.append({
            "text": documents[local_idx][:500],
            "book": metadatas[local_idx].get("book", "?"),
            "chapter": metadatas[local_idx].get("chapter", "?"),
            "similarity": round(s, 4),
        })
    return hits


# ══════════════════════════════════════════════════════════
# Tool 1: search_textbook
# ══════════════════════════════════════════════════════════

def _search_textbook(query: str) -> str:
    """Search medical textbooks for relevant content.

    Uses hybrid vector + BM25 retrieval across all loaded textbooks.
    """
    if not _API_KEY:
        return "错误: 未配置 CS_API_KEY 环境变量"

    try:
        embeddings, documents, metadatas = _load_all_books()
        if embeddings is None:
            return "错误: 未加载到教材数据"

        hits = _hybrid_search(query, embeddings, documents, metadatas, k=5, alpha=0.7)
        if not hits:
            return "未找到相关教材内容，请换个关键词试试。"

        lines = [f"找到 {len(hits)} 条相关内容:\n"]
        for i, h in enumerate(hits):
            lines.append(
                f"[{i+1}] {h['book']}·{h['chapter']} (相似度: {h['similarity']:.2f})\n"
                f"    {h['text'][:300]}..."
            )
        return "\n".join(lines)
    except Exception as e:
        return f"搜索出错: {e}"


search_textbook_tool = Tool(
    name="search_textbook",
    description=(
        "搜索医学教材内容。输入一个医学相关问题或关键词，"
        "返回教材中最相关的段落。适用于查找疾病、药物、解剖、生理等医学知识。"
    ),
    func=_search_textbook,
)


# ══════════════════════════════════════════════════════════
# Tool 2: calculate_dosage
# ══════════════════════════════════════════════════════════

# 常用药物剂量参考表 (mg/kg/day，仅供教学参考)
_DRUG_DOSAGE_DB = {
    "阿莫西林": {"generic": "Amoxicillin", "dose_mg_kg_day": "25-50", "max_daily_mg": 3000, "frequency": "3次/日", "route": "口服"},
    "头孢呋辛": {"generic": "Cefuroxime", "dose_mg_kg_day": "25-50", "max_daily_mg": 3000, "frequency": "2-3次/日", "route": "口服/静脉"},
    "布洛芬": {"generic": "Ibuprofen", "dose_mg_kg_day": "5-10", "max_daily_mg": 2400, "frequency": "3-4次/日", "route": "口服"},
    "对乙酰氨基酚": {"generic": "Paracetamol", "dose_mg_kg_day": "10-15", "max_daily_mg": 4000, "frequency": "3-4次/日", "route": "口服"},
    "阿奇霉素": {"generic": "Azithromycin", "dose_mg_kg_day": "10", "max_daily_mg": 500, "frequency": "1次/日", "route": "口服"},
    "甲硝唑": {"generic": "Metronidazole", "dose_mg_kg_day": "20-30", "max_daily_mg": 2000, "frequency": "3次/日", "route": "口服/静脉"},
    "地塞米松": {"generic": "Dexamethasone", "dose_mg_kg_day": "0.1-0.3", "max_daily_mg": 16, "frequency": "1-2次/日", "route": "口服/静脉/肌注"},
    "氨溴索": {"generic": "Ambroxol", "dose_mg_kg_day": "1.2-1.6", "max_daily_mg": 90, "frequency": "3次/日", "route": "口服/静脉"},
    "奥美拉唑": {"generic": "Omeprazole", "dose_mg_kg_day": "0.7-3.3", "max_daily_mg": 80, "frequency": "1-2次/日", "route": "口服/静脉"},
    "左氧氟沙星": {"generic": "Levofloxacin", "dose_mg_kg_day": "固定剂量", "max_daily_mg": 750, "frequency": "1次/日", "route": "口服/静脉"},
}


class DosageInput(BaseModel):
    """药物剂量计算输入参数。"""
    drug_name: str = Field(description="药物名称（中文通用名），如：阿莫西林、布洛芬")
    weight_kg: float = Field(description="患者体重（千克），如：70")


def _calculate_dosage(drug_name: str, weight_kg: float) -> str:
    """Calculate drug dosage based on patient weight.

    Uses a reference dosage table for common medications.
    Results are for educational reference only.
    """
    if weight_kg <= 0 or weight_kg > 300:
        return "错误: 体重应在 0-300 kg 之间"

    # Fuzzy match drug name
    matched_key = None
    for key in _DRUG_DOSAGE_DB:
        if drug_name in key or key in drug_name:
            matched_key = key
            break

    if not matched_key:
        available = "、".join(_DRUG_DOSAGE_DB.keys())
        return f"未找到药物 '{drug_name}' 的剂量数据。\n可用药物: {available}\n\n注意: 此工具仅包含常用药物的教学参考数据，如需其他药物请使用 search_textbook 查询教材。"

    info = _DRUG_DOSAGE_DB[matched_key]
    dose_range = info["dose_mg_kg_day"]

    if dose_range == "固定剂量":
        result = (
            f"**{matched_key} ({info['generic']})**\n"
            f"- 给药途径: {info['route']}\n"
            f"- 给药频次: {info['frequency']}\n"
            f"- 最大日剂量: {info['max_daily_mg']} mg\n"
            f"- 说明: 该药物为固定剂量，不按体重计算\n\n"
            f"⚠️ 以上为教学参考数据，实际用药请遵医嘱。"
        )
    else:
        low, high = dose_range.split("-")
        low_dose = float(low) * weight_kg
        high_dose = float(high) * weight_kg
        max_dose = info["max_daily_mg"]

        actual_low = min(low_dose, max_dose)
        actual_high = min(high_dose, max_dose)

        result = (
            f"**{matched_key} ({info['generic']})**\n"
            f"- 患者体重: {weight_kg} kg\n"
            f"- 推荐剂量范围: {dose_range} mg/kg/日\n"
            f"- 计算日剂量: {actual_low:.0f}-{actual_high:.0f} mg/日\n"
            f"- 最大日剂量: {max_dose} mg\n"
            f"- 给药途径: {info['route']}\n"
            f"- 给药频次: {info['frequency']}\n\n"
            f"⚠️ 以上为教学参考数据，实际用药请遵医嘱。"
        )

    return result


calculate_dosage_tool = StructuredTool.from_function(
    func=_calculate_dosage,
    name="calculate_dosage",
    description=(
        "根据患者体重计算药物剂量。输入药物名称和患者体重（kg），"
        "返回计算后的日剂量范围。支持常用药物如阿莫西林、布洛芬、头孢呋辛等。"
        "结果仅供教学参考，不作为临床用药依据。"
    ),
    args_schema=DosageInput,
)


# ══════════════════════════════════════════════════════════
# Tool 3: get_normal_values
# ══════════════════════════════════════════════════════════

_NORMAL_VALUES_DB = {
    # 血常规
    "白细胞": {"WBC": "(4.0-10.0)×10⁹/L", "note": "升高见于感染、炎症；降低见于病毒感染、再障"},
    "红细胞": {"RBC": "男(4.0-5.5)×10¹²/L，女(3.5-5.0)×10¹²/L", "note": "降低见于贫血"},
    "血红蛋白": {"Hb": "男120-160 g/L，女110-150 g/L", "note": "贫血诊断关键指标"},
    "血小板": {"PLT": "(100-300)×10⁹/L", "note": "降低见于ITP、再障；升高见于感染后反应"},
    # 肝功能
    "谷丙转氨酶": {"ALT": "0-40 U/L", "note": "肝细胞损伤敏感指标"},
    "谷草转氨酶": {"AST": "0-40 U/L", "note": "心肌损伤也升高"},
    "总胆红素": {"TBIL": "3.4-17.1 μmol/L", "note": "升高见于黄疸"},
    "白蛋白": {"ALB": "35-55 g/L", "note": "降低见于肝硬化、肾病综合征"},
    # 肾功能
    "肌酐": {"Cr": "男53-106 μmol/L，女44-97 μmol/L", "note": "肾功能重要指标"},
    "尿素氮": {"BUN": "2.9-8.2 mmol/L", "note": "升高见于肾功能不全"},
    "尿酸": {"UA": "男150-416 μmol/L，女89-357 μmol/L", "note": "升高见于痛风"},
    # 血糖血脂
    "空腹血糖": {"FPG": "3.9-6.1 mmol/L", "note": "≥7.0 mmol/L考虑糖尿病"},
    "糖化血红蛋白": {"HbA1c": "4.0-6.0%", "note": "反映近2-3月血糖水平"},
    "总胆固醇": {"TC": "<5.2 mmol/L", "note": "升高为高脂血症"},
    "甘油三酯": {"TG": "<1.7 mmol/L", "note": "升高为高脂血症"},
    # 电解质
    "血钾": {"K⁺": "3.5-5.5 mmol/L", "note": "高钾可致心律失常"},
    "血钠": {"Na⁺": "135-145 mmol/L", "note": "异常见于脱水、SIADH"},
    "血钙": {"Ca²⁺": "2.25-2.75 mmol/L", "note": "甲旁亢升高，甲旁减降低"},
    # 凝血
    "凝血酶原时间": {"PT": "11-13秒", "note": "INR 2.0-3.0用于抗凝监测"},
    "活化部分凝血活酶时间": {"APTT": "28-40秒", "note": "肝素监测指标"},
    # 甲状腺
    "促甲状腺激素": {"TSH": "0.27-4.2 mIU/L", "note": "甲亢降低，甲减升高"},
    "游离T4": {"FT4": "12-22 pmol/L", "note": "甲亢升高，甲减降低"},
    # 其他
    "C反应蛋白": {"CRP": "<10 mg/L", "note": "急性炎症/感染标志物"},
    "降钙素原": {"PCT": "<0.05 ng/mL", "note": "细菌感染标志物，>0.5提示细菌感染"},
    "D-二聚体": {"D-dimer": "<0.5 mg/L FEU", "note": "升高见于DIC、肺栓塞、深静脉血栓"},
}


def _get_normal_values(test_name: str) -> str:
    """Get normal reference values for common laboratory tests.

    Returns the test name, abbreviation, normal range, and clinical notes.
    """
    # Fuzzy match
    matched = []
    for key, val in _NORMAL_VALUES_DB.items():
        if test_name in key or key in test_name:
            matched.append((key, val))
        else:
            # Check abbreviation
            for abbr in val:
                if test_name.upper() == abbr:
                    matched.append((key, val))
                    break

    if not matched:
        available = "、".join(_NORMAL_VALUES_DB.keys())
        return f"未找到 '{test_name}' 的参考值。\n\n可查询项目: {available}"

    lines = []
    for name, info in matched:
        for abbr, ref_range in info.items():
            if abbr == "note":
                continue
            note = info.get("note", "")
            lines.append(f"**{name} ({abbr})**\n- 参考范围: {ref_range}\n- 临床意义: {note}")

    return "\n\n".join(lines)


get_normal_values_tool = Tool(
    name="get_normal_values",
    description=(
        "查询检验项目的正常参考值范围。输入检验项目名称（如：白细胞、肌酐、空腹血糖），"
        "返回该项目的正常范围和临床意义。支持血常规、肝肾功能、血糖血脂、电解质、凝血、甲状腺等常见项目。"
    ),
    func=_get_normal_values,
)


# ══════════════════════════════════════════════════════════
# Tool 4: compare_concepts
# ══════════════════════════════════════════════════════════

class CompareInput(BaseModel):
    """概念对比输入参数。"""
    concept_a: str = Field(description="第一个概念名称，如：青霉素")
    concept_b: str = Field(description="第二个概念名称，如：头孢菌素")


def _compare_concepts(concept_a: str, concept_b: str) -> str:
    """Compare two medical concepts by searching textbooks and generating a comparison.

    Searches for both concepts and generates a structured comparison table.
    """
    if not _API_KEY:
        return "错误: 未配置 CS_API_KEY 环境变量"

    try:
        embeddings, documents, metadatas = _load_all_books()
        if embeddings is None:
            return "错误: 未加载到教材数据"

        hits_a = _hybrid_search(concept_a, embeddings, documents, metadatas, k=5, alpha=0.7)
        hits_b = _hybrid_search(concept_b, embeddings, documents, metadatas, k=5, alpha=0.7)

        if not hits_a and not hits_b:
            return f"未找到 '{concept_a}' 和 '{concept_b}' 的相关教材内容。"

        user_msg = build_compare_message(hits_a, hits_b, concept_a, concept_b)
        result = call_llm(_API_KEY, user_msg, system_prompt=COMPARE_SYSTEM_PROMPT)
        return result
    except Exception as e:
        return f"对比分析出错: {e}"


compare_concepts_tool = StructuredTool.from_function(
    func=_compare_concepts,
    name="compare_concepts",
    description=(
        "对比两个医学概念的异同。输入两个概念名称（如药物、疾病、解剖结构），"
        "系统从教材中检索相关内容并生成结构化对比表格。"
        "适用于: 青霉素 vs 头孢菌素、1型糖尿病 vs 2型糖尿病等。"
    ),
    args_schema=CompareInput,
)


# ══════════════════════════════════════════════════════════
# Tool 5: analyze_case
# ══════════════════════════════════════════════════════════

class CaseInput(BaseModel):
    """病例分析输入参数。"""
    case_description: str = Field(description="病例描述，包含患者基本信息、主诉、现病史等")


def _analyze_case(case_description: str) -> str:
    """Analyze a clinical case using textbook knowledge.

    Searches for relevant content and performs step-by-step clinical reasoning.
    """
    if not _API_KEY:
        return "错误: 未配置 CS_API_KEY 环境变量"

    try:
        embeddings, documents, metadatas = _load_all_books()
        if embeddings is None:
            return "错误: 未加载到教材数据"

        hits = _hybrid_search(case_description, embeddings, documents, metadatas, k=5, alpha=0.7)
        if not hits:
            return "未找到相关教材内容，请补充更多病例信息。"

        user_msg = build_case_message(hits, case_description)
        result = call_llm(_API_KEY, user_msg, system_prompt=CASE_SYSTEM_PROMPT)
        return result
    except Exception as e:
        return f"病例分析出错: {e}"


analyze_case_tool = StructuredTool.from_function(
    func=_analyze_case,
    name="analyze_case",
    description=(
        "分析临床病例。输入详细的病例描述（包含患者年龄、性别、主诉、现病史、"
        "既往史、体格检查等），系统从教材检索相关内容并按临床推理流程分步分析："
        "病史分析→鉴别诊断→辅助检查→诊断→治疗。"
    ),
    args_schema=CaseInput,
)


# ══════════════════════════════════════════════════════════
# Tool registry
# ══════════════════════════════════════════════════════════

def get_tools() -> list:
    """Return all available tools for the medical agent."""
    return [
        search_textbook_tool,
        calculate_dosage_tool,
        get_normal_values_tool,
        compare_concepts_tool,
        analyze_case_tool,
    ]
