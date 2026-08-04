"""实测重建的两本书：用 search_engine 真实检索，验证语义命中。"""

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

# search_engine 依赖 streamlit 的缓存装饰器，无 streamlit 运行时上下文也能工作
# （st.cache_data 在裸脚本中退化为普通调用，st.error 打印到 stderr）
from search_engine import search

CASES = [
    ("药理学__(第10版)", "药理学 (第10版)", "强心苷类药物中毒的表现", ["强心", "地高辛", "洋地黄", "中毒"]),
    ("药理学__(第10版)", "药理学 (第10版)", "青霉素的不良反应", ["青霉素", "过敏", "不良反应"]),
    ("病理生理学__(第10版)", "病理生理学 (第10版)", "低钾血症对心肌的影响", ["低钾", "心肌", "钾血症"]),
    ("病理生理学__(第10版)", "病理生理学 (第10版)", "休克的分期及机制", ["休克", "缺血", "淤血", "微循环"]),
]

failed = 0
for fname, book, query, keywords in CASES:
    j = json.load(open(ROOT / "books" / f"{fname}.json", encoding="utf-8"))
    emb = np.load(ROOT / "books" / f"{fname}.npz")["embeddings"]
    hits = search(query, emb, j["documents"], j["metadatas"], k=5)
    if not hits:
        print(f"[失败] {book} «{query}» — 无命中")
        failed += 1
        continue
    top = hits[0]
    hit_text = " ".join(h["text"] for h in hits[:3])
    kw_found = [k for k in keywords if k in hit_text]
    ok = bool(kw_found) and top["book"] == book
    print(f"{'✅' if ok else '❌'} {book} «{query}»")
    print(f"   top1: section={top['chapter'][:40]!r} sim={top['similarity']} 关键词命中={kw_found}")
    print(f"   摘录: {top['text'][:80]}...")
    if not ok:
        failed += 1

sys.exit(1 if failed else 0)
