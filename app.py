"""🏥 医学教材知识库 — 轻量版（不依赖 chromadb）"""
import streamlit as st
import os, json, numpy as np
from pathlib import Path

st.set_page_config(page_title="医学教材知识库", page_icon="🏥", layout="wide")

# ── 加载数据 ──────────────────────────────────────
# 尝试多个可能的路径
_APP = Path(__file__).resolve().parent
_PATHS = [
    _APP / "vectors.npz",
    _APP / "workspace" / "vectors.npz",
    Path("/workspace/vectors.npz"),
    Path("/home/user/app/vectors.npz"),
    Path("/home/user/app/workspace/vectors.npz"),
]

VPATH = None
for p in _PATHS:
    if p.exists():
        VPATH = p
        break

if VPATH is None:
    import glob
    # 搜索全盘
    found = list(Path("/").glob("**/vectors.npz"))[:5] if os.name != "nt" else []
    st.error(f"vectors.npz 未找到。\n搜索路径: {_PATHS[:3]}\n当前目录: {_APP}\n文件列表: {sorted(os.listdir(_APP))[:20]}")
    if found:
        st.info(f"找到: {found}")
    st.stop()

MPATH = VPATH.parent / "metadata.json"

@st.cache_resource
def load_data():
    if not MPATH.exists():
        st.error(f"metadata.json 未找到: {MPATH}")
        st.stop()
    data = np.load(vpath)
    embeddings = data["embeddings"].astype(np.float32)
    with open(mpath, encoding="utf-8") as f:
        meta = json.load(f)
    return embeddings, meta["documents"], meta["metadatas"]

embeddings, documents, metadatas = load_data()
book_count = len(set(m.get("book","?") for m in metadatas))

# ── API ───────────────────────────────────────────
import requests
from openai import OpenAI
API_KEY = os.environ.get("CS_API_KEY", "")
if not API_KEY:
    st.error("未配置 CS_API_KEY")
    st.stop()
_embed = OpenAI(api_key=API_KEY, base_url="https://open.cherryin.net/v1")

# ── 检索 ──────────────────────────────────────────
def search(text, k=10):
    r = _embed.embeddings.create(model="baai/bge-m3(free)", input=[text])
    qvec = np.array(r.data[0].embedding, dtype=np.float32)
    # 余弦相似度 = 点积（向量已归一化）
    scores = embeddings @ qvec
    top_k = np.argsort(scores)[-k:][::-1]
    hits = []
    for idx in top_k:
        hits.append({
            "text": documents[int(idx)],
            "book": metadatas[int(idx)].get("book","?"),
            "chapter": metadatas[int(idx)].get("chapter","?"),
            "similarity": round(float(scores[idx]), 4),
        })
    return hits

def synthesize(q, hits):
    ctx = "\n\n---\n\n".join(f"[{i+1}] {h['book']}·{h['chapter']}\n{h['text']}" for i,h in enumerate(hits))
    r = requests.post("https://open.cherryin.net/v1/chat/completions",
        headers={"Authorization":f"Bearer {API_KEY}"},
        json={"model":"deepseek/deepseek-v4-flash(free)","messages":[
            {"role":"system","content":"你是医学知识助手。根据以下教材段落回答用户问题，标注来源。"},
            {"role":"user","content":f"教材段落:\n{ctx}\n\n问题: {q}"}],"temperature":0.3},timeout=60)
    return r.json()["choices"][0]["message"]["content"]

# ── UI ────────────────────────────────────────────
st.markdown("""<style>
.stApp{background:linear-gradient(180deg,#f0f4f8,#e8ecf1)}
.stTextInput>div>div>input{border:2px solid #e0e6ed;border-radius:12px;padding:.7rem 1rem}
.answer-box{background:#fff;border-radius:14px;padding:1.2rem 1.8rem;box-shadow:0 2px 12px rgba(0,0,0,.05);border-top:4px solid #27ae60;font-size:.93rem;line-height:1.75}
</style>""",unsafe_allow_html=True)

st.markdown('<h2 style="text-align:center">🏥 医学教材知识库</h2>',unsafe_allow_html=True)
st.caption(f"{book_count} 本医学教材 · {len(documents)} 块 · 免费 · 24h在线")

q = st.text_input("🔍 输入医学问题",placeholder="如：心衰的病理机制、股三角的构成…",label_visibility="collapsed")
if "hist" not in st.session_state: st.session_state.hist=[]

if st.button("搜索",type="primary") and q.strip():
    with st.spinner("检索…"):
        hits = search(q.strip(), k=10)
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
