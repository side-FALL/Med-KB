"""校验全部教材数据一致性：json chunks == npz 行数 == manifest chunks，维度 1024。"""

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "books"

manifest = json.load(open(BOOKS / "manifest.json", encoding="utf-8"))
bad = []
for book, info in manifest.items():
    fname = info["file"]
    jp, np_ = BOOKS / f"{fname}.json", BOOKS / f"{fname}.npz"
    if not jp.exists() or not np_.exists():
        bad.append((book, "文件缺失"))
        continue
    j = json.load(open(jp, encoding="utf-8"))
    e = np.load(np_)["embeddings"]
    if len(j["documents"]) != e.shape[0] or len(j["documents"]) != info["chunks"]:
        bad.append((book, f"json={len(j['documents'])} npz={e.shape[0]} manifest={info['chunks']}"))
    if e.shape[1] != 1024:
        bad.append((book, f"维度异常 {e.shape}"))

total = sum(i["chunks"] for i in manifest.values())
print(f"校验书目: {len(manifest)} | 总 chunks: {total}")
if bad:
    print("不一致项:")
    for b in bad:
        print("  -", b)
    sys.exit(1)
print("全部一致 ✅")
