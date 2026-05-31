"""
🏥 医学教材知识库 — ModelScope 部署版
"""
import streamlit as st
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 简易版配置（不依赖本地 config.py）
import requests
from openai import OpenAI

# —— API ——
API_KEY = os.environ["CS_API_KEY"]
EMBED_BASE = "https://open.cherryin.net/v1"
EMBED_MODEL = "baai/bge-m3(free)"

# ChromaDB
import chromadb
CHROMA_DIR = Path(__file__).resolve().parent / "chromadb"
COLLECTION = "medical_textbooks"

# —— 嵌入 ——
_embed_client = OpenAI(api_key=API_KEY, base_url=EMBED_BASE)

def search(text, top_k=5):
    r = _embed_client.embeddings.create(model=EMBED_MODEL, input=[text])
    vec = r.data[0].embedding
    c = chromadb.PersistentClient(path=str(CHROMA_DIR))
    col = c.get_or_create_collection(COLLECTION)
    res = col.query(query_embeddings=[vec], n_results=top_k, include=["documents","metadatas","distances"])
    hits = []
    for i in range(len(res["ids"][0])):
        dist = res["distances"][0][i]
        hits.append({
            "text": res["documents"][0][i],
            "book": res["metadatas"][0][i].get("book","?"),
            "chapter": res["metadatas"][0][i].get("chapter","?"),
            "similarity": round(1 - dist/2, 4),
        })
    return hits

def synthesize(query, hits):
    ctx = "\n\n---\n\n".join(
        f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}" for i,h in enumerate(hits)
    )
    r = requests.post(
        f"{EMBED_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={
            "model": "deepseek/deepseek-v4-flash(free)",
            "messages": [
                {"role": "system", "content": "你是医学知识助手。根据以下教材段落回答用户问题，标注来源。"},
                {"role": "user", "content": f"教材段落:\n{ctx}\n\n问题: {query}"},
            ],
            "temperature": 0.3,
        },
        timeout=60,
    )
    return r.json()["choices"][0]["message"]["content"]

# —— UI ——
st.set_page_config(page_title="医学教材知识库", page_icon="🏥", layout="wide")

st.markdown("""
<style>
    .stApp { background: linear-gradient(180deg, #f0f4f8, #e8ecf1); }
    .stTextInput>div>div>input {
        border:2px solid #e0e6ed; border-radius:12px; padding:.7rem 1rem; font-size:1rem;
    }
    .stTextInput>div>div>input:focus { border-color:#2e86c1; }
    .answer-box {
        background:#fff; border-radius:14px; padding:1.2rem 1.8rem;
        box-shadow:0 2px 12px rgba(0,0,0,.05); border-top:4px solid #27ae60;
        font-size:.93rem; line-height:1.75;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<h2 style="text-align:center">🏥 医学教材知识库</h2>', unsafe_allow_html=True)
st.caption("7 本医学教材 · 全免费模型 · 24h 在线")

q = st.text_input("🔍 输入医学问题", placeholder="如：心衰的病理机制、股三角的构成…", label_visibility="collapsed")

if "hist" not in st.session_state:
    st.session_state.hist = []

if st.button("搜索", type="primary") and q.strip():
    with st.spinner("检索中…"):
        hits = search(q.strip())
    if hits:
        with st.spinner("生成回答…"):
            ans = synthesize(q.strip(), hits)
    else:
        ans = "未找到相关内容"
    st.session_state.hist.append({"q": q.strip(), "hits": hits, "a": ans})

for item in reversed(st.session_state.hist):
    st.markdown(f"**❓ {item['q']}**")
    if item["hits"]:
        for h in item["hits"][:5]:
            with st.expander(f"📖 {h['book']} · {h['chapter'][:30]} ({h['similarity']:.2f})", expanded=False):
                st.caption(h["text"])
    st.markdown(f'<div class="answer-box">{item["a"]}</div>', unsafe_allow_html=True)
    st.divider()
