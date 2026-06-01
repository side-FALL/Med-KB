"""在线阅读器版的 app.py — 从 ZIP 加载 chromadb"""
import streamlit as st
import os, zipfile, shutil
from pathlib import Path

st.set_page_config(page_title="医学教材知识库", page_icon="🏥", layout="wide")

# 解压 chromadb（首次运行）
CHROMA_DIR = Path("/home/user/app/chromadb")
if not CHROMA_DIR.exists():
    zip_path = Path("/home/user/app/chromadb.zip")
    if zip_path.exists():
        with st.spinner("首次加载数据…"):
            shutil.unpack_archive(zip_path, CHROMA_DIR.parent)
            st.rerun()

import requests, chromadb
from openai import OpenAI

API_KEY = os.environ.get("CS_API_KEY", "")
if not API_KEY:
    st.error("未配置 CS_API_KEY")
    st.stop()

_embed = OpenAI(api_key=API_KEY, base_url="https://open.cherryin.net/v1")

try:
    _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _col = _client.get_collection("medical_textbooks")
    _meta = _col.get(include=["metadatas"], limit=100000)
    book_count = len(set(m["book"] for m in _meta["metadatas"]))
    # Debug info
    import glob
    files = glob.glob(str(CHROMA_DIR / "**/*"), recursive=True)[:20]
    st.sidebar.caption(f"ChromaDB: {len(_meta['metadatas'])} records, {book_count} books\nFiles: {len(files)}")
except Exception as e:
    st.error(f"ChromaDB 加载失败: {e}")
    st.stop()

# UI
st.markdown("""<style>
.stApp{background:linear-gradient(180deg,#f0f4f8,#e8ecf1)}
.stTextInput>div>div>input{border:2px solid #e0e6ed;border-radius:12px;padding:.7rem 1rem}
.answer-box{background:#fff;border-radius:14px;padding:1.2rem 1.8rem;box-shadow:0 2px 12px rgba(0,0,0,.05);border-top:4px solid #27ae60;font-size:.93rem;line-height:1.75}
</style>""",unsafe_allow_html=True)

st.markdown(f'<h2 style="text-align:center">🏥 医学教材知识库</h2>',unsafe_allow_html=True)
st.caption(f"{book_count} 本医学教材 · {_col.count()} 块 · 免费 · 24h在线")

def search(text, k=5):
    r = _embed.embeddings.create(model="baai/bge-m3(free)", input=[text])
    res = _col.query(query_embeddings=[r.data[0].embedding], n_results=k, include=["documents","metadatas","distances"])
    return [{"text":res["documents"][0][i],"book":res["metadatas"][0][i].get("book","?"),"chapter":res["metadatas"][0][i].get("chapter","?"),"similarity":round(1-res["distances"][0][i]/2,4)} for i in range(len(res["ids"][0]))]

def synthesize(q, hits):
    ctx = "\n\n---\n\n".join(f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}" for i,h in enumerate(hits))
    r = requests.post("https://open.cherryin.net/v1/chat/completions",
        headers={"Authorization":f"Bearer {API_KEY}"},
        json={"model":"deepseek/deepseek-v4-flash(free)","messages":[
            {"role":"system","content":"你是医学知识助手。根据以下教材段落回答，标注来源。"},
            {"role":"user","content":f"教材段落:\n{ctx}\n\n问题: {q}"}],"temperature":0.3},timeout=60)
    return r.json()["choices"][0]["message"]["content"]

q = st.text_input("🔍 输入医学问题",placeholder="如：心衰的病理机制、股三角的构成…",label_visibility="collapsed")
if "hist" not in st.session_state: st.session_state.hist=[]

if st.button("搜索",type="primary") and q.strip():
    with st.spinner("检索…"):
        hits = search(q.strip())
    if hits:
        with st.spinner("回答…"):
            ans = synthesize(q.strip(), hits)
    else:
        ans = "未找到相关内容"
    st.session_state.hist.append({"q":q.strip(),"hits":hits,"a":ans})

for item in reversed(st.session_state.hist):
    st.markdown(f"**❓ {item['q']}**")
    if item["hits"]:
        for h in item["hits"][:5]:
            with st.expander(f"📖 {h['book']} · {h['chapter'][:30]} ({h['similarity']:.2f})",expanded=False):
                st.caption(h["text"])
    st.markdown(f'<div class="answer-box">{item["a"]}</div>',unsafe_allow_html=True)
    st.divider()
