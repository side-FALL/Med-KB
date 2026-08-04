"""教材加载防御容错 — 单元测试

测试内容：
1. load_book 缺失 .json / .npz 时返回 None，不抛异常
2. load_selected_books 跳过缺失书，其余书正常加载
3. get_book_stats 缺失书不计入可选列表，日志有 warning
4. 文件齐全时行为与改动前一致

用法：
    python test_book_loading_defense.py
"""

import json
import sys
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))


def _create_book_files(directory: Path, fname: str, chunks: int = 5):
    """在指定目录创建一对 .json + .npz 教材文件。"""
    # 创建 .json
    meta = {
        "documents": [f"段落{i}" for i in range(chunks)],
        "metadatas": [{"book": fname, "chapter": f"第{i}章"} for i in range(chunks)],
    }
    json_path = directory / f"{fname}.json"
    json_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 创建 .npz
    embeddings = np.random.randn(chunks, 64).astype(np.float32)
    npz_path = directory / f"{fname}.npz"
    np.savez(npz_path, embeddings=embeddings)

    return json_path, npz_path


def _build_manifest(books: dict[str, str], chunks_map: dict[str, int] | None = None) -> dict:
    """构建 manifest 字典。

    Args:
        books: {书名: 文件名前缀}
        chunks_map: {书名: chunks数}，未指定的书默认 5
    """
    manifest = {}
    for book_name, fname in books.items():
        chunks = (chunks_map or {}).get(book_name, 5)
        manifest[book_name] = {
            "chunks": chunks,
            "file": fname,
            "size_kb": 100.0,
        }
    return manifest


class TestLoadBookDefense(unittest.TestCase):
    """load_book 防御容错测试。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.manifest = _build_manifest({
            "完整教材": "完整教材",
            "缺JSON教材": "缺JSON教材",
            "缺NPZ教材": "缺NPZ教材",
        })
        # 完整教材：创建 json + npz
        _create_book_files(self.tmpdir, "完整教材")
        # 缺 JSON 教材：只创建 npz
        _, npz_path = _create_book_files(self.tmpdir, "缺JSON教材")
        (self.tmpdir / "缺JSON教材.json").unlink()
        # 缺 NPZ 教材：只创建 json
        _create_book_files(self.tmpdir, "缺NPZ教材")
        (self.tmpdir / "缺NPZ教材.npz").unlink()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("ui_components.st")
    def test_load_book_full_files_returns_data(self, mock_st):
        """文件齐全时正常返回数据。"""
        # 使 cache_resource 装饰器变为 no-op
        mock_st.cache_resource = lambda fn: fn

        # 重新导入以应用 mock
        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            result = ui_components.load_book("完整教材", self.manifest)

        self.assertIsNotNone(result)
        emb, docs, metas = result
        self.assertEqual(len(docs), 5)
        self.assertEqual(len(metas), 5)
        self.assertEqual(emb.shape[0], 5)

    @patch("ui_components.st")
    def test_load_book_missing_json_returns_none(self, mock_st):
        """缺失 .json 时返回 None，不抛异常。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            result = ui_components.load_book("缺JSON教材", self.manifest)

        self.assertIsNone(result)

    @patch("ui_components.st")
    def test_load_book_missing_npz_returns_none(self, mock_st):
        """缺失 .npz 时返回 None，不抛异常。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            result = ui_components.load_book("缺NPZ教材", self.manifest)

        self.assertIsNone(result)

    @patch("ui_components.st")
    def test_load_book_missing_logs_warning(self, mock_st):
        """缺失文件时 logger.warning 被调用。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        # 使用唯一的书名避免 st.cache_resource 缓存命中前一个测试的结果
        unique_name = "仅用于日志测试教材"
        unique_manifest = {
            unique_name: {"chunks": 5, "file": unique_name, "size_kb": 100.0}
        }
        _create_book_files(self.tmpdir, unique_name)
        (self.tmpdir / f"{unique_name}.json").unlink()

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            with self.assertLogs("ui_components", level=logging.WARNING) as cm:
                result = ui_components.load_book(unique_name, unique_manifest)

        self.assertIsNone(result)
        self.assertTrue(any(unique_name in msg for msg in cm.output))


class TestLoadSelectedBooksDefense(unittest.TestCase):
    """load_selected_books 防御容错测试。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.manifest = _build_manifest({
            "教材A": "教材A",
            "教材B_缺文件": "教材B_缺文件",
            "教材C": "教材C",
        })
        _create_book_files(self.tmpdir, "教材A")
        # 教材B 只创建 npz（缺 json）
        _create_book_files(self.tmpdir, "教材B_缺文件")
        (self.tmpdir / "教材B_缺文件.json").unlink()
        _create_book_files(self.tmpdir, "教材C")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("ui_components.st")
    def test_skip_missing_book_load_others(self, mock_st):
        """缺失书被跳过，其余书正常加载。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            emb, docs, metas = ui_components.load_selected_books(
                ["教材A", "教材B_缺文件", "教材C"], self.manifest
            )

        self.assertIsNotNone(emb)
        # 教材A 5块 + 教材C 5块 = 10块
        self.assertEqual(len(docs), 10)
        self.assertEqual(len(metas), 10)
        self.assertEqual(emb.shape[0], 10)

    @patch("ui_components.st")
    def test_all_missing_returns_none(self, mock_st):
        """所有书都缺失时返回 (None, [], [])。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            emb, docs, metas = ui_components.load_selected_books(
                ["教材B_缺文件"], self.manifest
            )

        self.assertIsNone(emb)
        self.assertEqual(docs, [])
        self.assertEqual(metas, [])

    @patch("ui_components.st")
    def test_no_exception_on_missing(self, mock_st):
        """缺失书不抛异常。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            try:
                ui_components.load_selected_books(
                    ["教材B_缺文件", "教材A"], self.manifest
                )
            except FileNotFoundError:
                self.fail("load_selected_books 不应抛出 FileNotFoundError")


class TestGetBookStatsDefense(unittest.TestCase):
    """get_book_stats 防御容错测试。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.manifest = _build_manifest(
            {
                "正常教材A": "正常教材A",
                "正常教材B": "正常教材B",
                "缺失教材X": "缺失教材X",
                "缺失教材Y": "缺失教材Y",
            },
            chunks_map={"正常教材A": 10, "正常教材B": 20, "缺失教材X": 5, "缺失教材Y": 5},
        )
        _create_book_files(self.tmpdir, "正常教材A", chunks=10)
        _create_book_files(self.tmpdir, "正常教材B", chunks=20)
        # 缺失教材X：不创建任何文件
        # 缺失教材Y：只创建 json
        _create_book_files(self.tmpdir, "缺失教材Y")
        (self.tmpdir / "缺失教材Y.npz").unlink()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("ui_components.st")
    def test_missing_books_excluded_from_list(self, mock_st):
        """缺失书不出现在 ALL_BOOKS 列表中。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            all_books, book_count, total_chunks, book_stats = ui_components.get_book_stats(self.manifest)

        self.assertNotIn("缺失教材X", all_books)
        self.assertNotIn("缺失教材Y", all_books)
        self.assertIn("正常教材A", all_books)
        self.assertIn("正常教材B", all_books)
        self.assertEqual(book_count, 2)

    @patch("ui_components.st")
    def test_total_chunks_only_available(self, mock_st):
        """total_chunks 只统计可用教材。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            _, _, total_chunks, book_stats = ui_components.get_book_stats(self.manifest)

        # 正常教材A 10 + 正常教材B 20 = 30
        self.assertEqual(total_chunks, 30)
        self.assertEqual(len(book_stats), 2)
        self.assertNotIn("缺失教材X", book_stats)
        self.assertNotIn("缺失教材Y", book_stats)

    @patch("ui_components.st")
    def test_missing_books_logged_as_warning(self, mock_st):
        """缺失书在日志中有 warning 记录。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            with self.assertLogs("ui_components", level=logging.WARNING) as cm:
                ui_components.get_book_stats(self.manifest)

        # 至少有两条 warning（缺失教材X 和 缺失教材Y）
        warning_texts = "\n".join(cm.output)
        self.assertIn("缺失教材X", warning_texts)
        self.assertIn("缺失教材Y", warning_texts)

    @patch("ui_components.st")
    def test_all_books_available_no_warning(self, mock_st):
        """所有书文件齐全时无 warning。"""
        # 补齐缺失文件
        _create_book_files(self.tmpdir, "缺失教材X")
        (self.tmpdir / "缺失教材Y.npz").write_bytes(
            (self.tmpdir / "正常教材A.npz").read_bytes()
        )

        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            all_books, book_count, _, _ = ui_components.get_book_stats(self.manifest)

        self.assertEqual(book_count, 4)


class TestFullBooksAvailable(unittest.TestCase):
    """文件齐全时行为与改动前一致。"""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.manifest = _build_manifest(
            {"书1": "书1", "书2": "书2"},
            chunks_map={"书1": 8, "书2": 12},
        )
        _create_book_files(self.tmpdir, "书1", chunks=8)
        _create_book_files(self.tmpdir, "书2", chunks=12)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("ui_components.st")
    def test_get_book_stats_all_available(self, mock_st):
        """文件齐全时 get_book_stats 返回全部书。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            all_books, book_count, total_chunks, book_stats = ui_components.get_book_stats(self.manifest)

        self.assertEqual(book_count, 2)
        self.assertEqual(total_chunks, 20)
        self.assertIn("书1", all_books)
        self.assertIn("书2", all_books)

    @patch("ui_components.st")
    def test_load_selected_books_all_loaded(self, mock_st):
        """文件齐全时 load_selected_books 加载全部书。"""
        mock_st.cache_resource = lambda fn: fn

        import importlib
        import ui_components
        importlib.reload(ui_components)

        with patch.object(ui_components, "BOOKS_DIR", self.tmpdir):
            emb, docs, metas = ui_components.load_selected_books(
                ["书1", "书2"], self.manifest
            )

        self.assertIsNotNone(emb)
        self.assertEqual(len(docs), 20)
        self.assertEqual(emb.shape[0], 20)


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))

    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors

    print("\n" + "=" * 60)
    print(f"📊 教材加载防御测试总结")
    print(f"   总计: {total} 个测试")
    print(f"   ✅ 通过: {passed}")
    if failures:
        print(f"   ❌ 失败: {failures}")
    if errors:
        print(f"   ⚠️ 错误: {errors}")
    print("=" * 60)

    if failures or errors:
        if failures:
            print("\n失败详情:")
            for test, traceback in result.failures:
                print(f"  - {test}")
        if errors:
            print("\n错误详情:")
            for test, traceback in result.errors:
                print(f"  - {test}")
        sys.exit(1)
    else:
        print("\n🎉 所有测试通过！")
