"""从 PaddleOCR 原始输出重建教材数据（json + npz + manifest）

用法:
    python scripts/build_book_from_ocr.py <ocr_json> <book_name> <out_fname>

示例:
    python scripts/build_book_from_ocr.py books/_ocr_source/药理学.ocr.json "药理学 (第10版)" "药理学__(第10版)"

流程:
    1. 解析 OCR 原始输出, 按页提取 markdown.text
    2. 切分: 按段落贪心打包, 每块 ≤800 字, section 取最近 markdown 标题行
       (与现有 43 本书的切分粒度一致: 平均 477~613 字, 最大 800 字)
    3. 调用 SiliconFlow BGE-M3 批量生成 embeddings (归一化 float32)
       - 断点续跑: books/_ocr_source/<out_fname>.emb_progress.npy
    4. 输出 books/<out_fname>.json 与 books/<out_fname>.npz
    5. 更新 books/manifest.json (chunks / size_kb)

注意:
    - 需要 .env 中配置 SILICONFLOW_API_KEY (只读, 不修改 .env)
    - 现有书 documents 保留 HTML img 标签, 本脚本保持一致不清洗
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT / "books"

# 加载 .env（只读）
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

CHUNK_SIZE = 800          # 每块最大字符数（与现有书一致）
EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
BATCH_SIZE = 32
MAX_RETRIES = 5


# ── 切分 ────────────────────────────────────────────────

def extract_pages(ocr_json: Path) -> list[str]:
    """从 OCR 原始输出提取每页 markdown.text。"""
    with open(ocr_json, encoding="utf-8") as f:
        data = json.load(f)
    pages = []
    for i, page in enumerate(data):
        text = page.get("markdown", {}).get("text", "")
        if not isinstance(text, str):
            print(f"  [警告] 第{i+1}页 markdown.text 非字符串, 跳过")
            text = ""
        pages.append(text)
    return pages


def split_paragraphs(pages: list[str]) -> list[str]:
    """按页→段落拆分（空行分隔），过滤空段落。"""
    paragraphs = []
    for page in pages:
        for para in page.split("\n\n"):
            para = para.strip()
            if para:
                paragraphs.append(para)
    return paragraphs


def chunk_paragraphs(paragraphs: list[str], book_name: str) -> tuple[list[str], list[dict]]:
    """贪心打包段落到 ≤800 字的块, 跟踪最近标题行作为 section。

    Returns:
        (documents, metadatas)
    """
    documents, metadatas = [], []
    current_section = ""
    buf: list[str] = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if buf:
            documents.append("\n\n".join(buf))
            metadatas.append({
                "book": book_name,
                "section": current_section,
                "chunk_index": len(documents) - 1,
            })
            buf, buf_len = [], 0

    for para in paragraphs:
        # 标题行: 更新当前 section（标题行本身保留在正文中, 与现有数据一致）
        first_line = para.split("\n", 1)[0].strip()
        if first_line.startswith("#"):
            current_section = first_line

        # 单段落超限: 先 flush, 再硬切
        if len(para) > CHUNK_SIZE:
            flush()
            for start in range(0, len(para), CHUNK_SIZE):
                documents.append(para[start:start + CHUNK_SIZE])
                metadatas.append({
                    "book": book_name,
                    "section": current_section,
                    "chunk_index": len(documents) - 1,
                })
            continue

        # 加入会超限: 先 flush
        if buf_len + len(para) + 2 > CHUNK_SIZE:
            flush()

        buf.append(para)
        buf_len += len(para) + 2  # +2 为拼接的 \n\n

    flush()
    return documents, metadatas


# ── Embedding ───────────────────────────────────────────

def _get_client():
    from openai import OpenAI
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        sys.exit("错误: 未找到 SILICONFLOW_API_KEY 环境变量")
    return OpenAI(
        api_key=api_key,
        base_url="https://api.siliconflow.cn/v1",
        timeout=60.0,
    )


def embed_batch(client, texts: list[str]) -> np.ndarray:
    """批量 embedding, 429/超时指数退避重试。"""
    for attempt in range(MAX_RETRIES):
        try:
            r = client.embeddings.create(model=EMBED_MODEL, input=texts)
            vecs = np.array([d.embedding for d in r.data], dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return vecs / norms
        except Exception as e:
            wait = min(2 ** attempt * 5, 60)
            print(f"  [重试 {attempt+1}/{MAX_RETRIES}] {type(e).__name__}: {e} — {wait}s 后重试")
            time.sleep(wait)
    sys.exit("错误: embedding 连续失败, 已达最大重试次数")


def embed_documents(client, documents: list[str], progress_path: Path) -> np.ndarray:
    """对全部 documents 生成归一化 embeddings, 支持断点续跑。"""
    n = len(documents)
    done = 0
    embeddings = np.zeros((n, EMBED_DIM), dtype=np.float32)

    if progress_path.exists():
        saved = np.load(progress_path)
        done = int(saved.shape[0])
        embeddings[:done] = saved
        print(f"  断点续跑: 已完成 {done}/{n}")

    while done < n:
        end = min(done + BATCH_SIZE, n)
        batch = documents[done:end]
        embeddings[done:end] = embed_batch(client, batch)
        done = end
        np.save(progress_path, embeddings[:done])  # 每批保存断点
        print(f"  embedding 进度: {done}/{n} ({100*done//n}%)", flush=True)
        time.sleep(0.5)  # 温和限流

    progress_path.unlink(missing_ok=True)
    return embeddings


# ── 主流程 ───────────────────────────────────────────────

def build(ocr_json: Path, book_name: str, out_fname: str) -> None:
    print(f"=== 重建教材: {book_name} ===")
    print(f"OCR 源文件: {ocr_json}")

    # 1. 切分
    pages = extract_pages(ocr_json)
    paragraphs = split_paragraphs(pages)
    documents, metadatas = chunk_paragraphs(paragraphs, book_name)
    sizes = [len(d) for d in documents]
    print(f"页数: {len(pages)}, 段落数: {len(paragraphs)}")
    print(f"切分结果: {len(documents)} 块, 平均 {sum(sizes)//max(len(sizes),1)} 字, "
          f"最小 {min(sizes, default=0)}, 最大 {max(sizes, default=0)}")

    # 2. 保存 json
    out_json = BOOKS_DIR / f"{out_fname}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"documents": documents, "metadatas": metadatas}, f, ensure_ascii=False)
    print(f"已保存: {out_json.name} ({out_json.stat().st_size/1024:.0f} KB)")

    # 3. Embedding
    client = _get_client()
    progress_path = BOOKS_DIR / "_ocr_source" / f"{out_fname}.emb_progress.npy"
    embeddings = embed_documents(client, documents, progress_path)

    out_npz = BOOKS_DIR / f"{out_fname}.npz"
    np.savez_compressed(out_npz, embeddings=embeddings)
    print(f"已保存: {out_npz.name} shape={embeddings.shape}")

    # 4. 更新 manifest
    manifest_path = BOOKS_DIR / "manifest.json"
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest[book_name] = {
        "chunks": len(documents),
        "file": out_fname,
        "size_kb": round(out_json.stat().st_size / 1024, 1),
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"manifest 已更新: {book_name} → chunks={len(documents)}")

    # 5. 校验
    assert len(documents) == embeddings.shape[0], "chunks 与 embeddings 行数不一致!"
    norms = np.linalg.norm(embeddings, axis=1)
    assert abs(norms.mean() - 1.0) < 1e-3, "embeddings 未归一化!"
    print(f"✅ 校验通过: {len(documents)} chunks, embeddings {embeddings.shape}, 已归一化")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    build(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
