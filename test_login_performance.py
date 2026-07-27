"""医学教材知识库 - 登录性能与功能验证测试

验证任务 1（后端优化）和任务 2（前端错误提示）的成果：
  标准一：mock 存储模拟正常延迟（100-300ms/次），登录全流程耗时 < 2 秒
  标准二：正确凭据登录成功、错误密码报错、不存在用户报错
  标准三：Redis 不可用时按降级规则报错（不静默降级）
  标准四：登录失败时按失败类型显示明确错误信息（前端分类映射正确）
  标准五：密码校验仍使用 bcrypt，不降级为明文/弱哈希

测试策略：
  - MockRedisClient 模拟 Upstash Redis 单 key 读写，注入可控延迟
  - 关键设计：supports_single_key_ops = True 是显式 bool，避免 MagicMock 误判
  - 不访问真实 Upstash/JSONBin 服务，不读取或修改 .env

用法：
    python test_login_performance.py
"""

import sys
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 路径设置 ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 测试常量 ──────────────────────────────────────────────

_TEST_PASSWORD = "TestPass123"
_WRONG_PASSWORD = "WrongPass999"
_PERF_THRESHOLD = 2.0  # 登录全流程耗时上限（秒）
_DEFAULT_LATENCY = 0.15  # 模拟每次存储往返 150ms（正常网络延迟区间 100-300ms）


# ═══════════════════════════════════════════════════════════════════
# MockRedisClient：模拟 Upstash Redis 客户端
# ═══════════════════════════════════════════════════════════════════

class MockRedisClient:
    """模拟 Upstash Redis 客户端，支持单 key 读写与延迟注入。

    设计要点：
    - supports_single_key_ops = True 是显式 bool（非 MagicMock 自动属性），
      UserDataManager 通过 ``getattr(client, 'supports_single_key_ops', False) is True``
      检测能力，MagicMock 会因自动生成属性导致误判
    - 每次存储操作注入可配置延迟，模拟正常网络往返（100-300ms）
    - available 标志可切换为 False，模拟 Redis 宕机
    - 单 key 读写（get_user_by_name / set_user_by_name）与全量读写（get_record /
      update_record）共享同一份内存数据，保证两条路径数据一致
    """

    def __init__(self, latency: float = 0.0):
        self.supports_single_key_ops = True  # 显式能力标记（bool，非 MagicMock）
        self._latency = latency
        self._available = True
        # 内存存储：单 key 数据与全量数据共享
        self._record = {
            "users": {},
            "meta": {"logs": []},
            "config": {},
        }

    def _sleep(self):
        """模拟网络往返延迟。"""
        if self._latency > 0:
            time.sleep(self._latency)

    # ── 单 key 读写（登录优化路径） ──

    def get_user_by_name(self, username: str):
        self._sleep()
        if not self._available:
            from upstash_client import UpstashRequestError
            raise UpstashRequestError("Redis 不可用，无法读取用户数据")
        return deepcopy(self._record.get("users", {}).get(username))

    def set_user_by_name(self, username: str, data: dict) -> None:
        self._sleep()
        if not self._available:
            from upstash_client import UpstashRequestError
            raise UpstashRequestError("Redis 不可用，无法写入用户数据")
        self._record.setdefault("users", {})[username] = deepcopy(data)

    def get_meta(self) -> dict:
        self._sleep()
        if not self._available:
            from upstash_client import UpstashRequestError
            raise UpstashRequestError("Redis 不可用，无法读取 meta")
        return deepcopy(self._record.get("meta", {}))

    # ── 全量读写（create_user / 回退路径） ──

    def get_record(self) -> dict:
        self._sleep()
        if not self._available:
            from upstash_client import UpstashRequestError
            raise UpstashRequestError("Redis 不可用，无法读取记录")
        return deepcopy(self._record)

    def update_record(self, record: dict) -> None:
        self._sleep()
        if not self._available:
            from upstash_client import UpstashRequestError
            raise UpstashRequestError("Redis 不可用，无法写入记录")
        self._record = deepcopy(record)


# ── 辅助函数 ──────────────────────────────────────────────

def _make_manager(latency: float = 0.0, current_user: str = None):
    """创建使用 MockRedisClient 的 UserDataManager。"""
    from user_data_manager import UserDataManager
    mock_client = MockRedisClient(latency=latency)
    return UserDataManager(client=mock_client, auto_init=False, current_user=current_user), mock_client


def _create_test_user(manager, username="testuser", password=_TEST_PASSWORD, role="user"):
    """在 mock 存储中创建测试用户（走全量路径）。"""
    from auth_logic import hash_password
    return manager.create_user(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )


# ═══════════════════════════════════════════════════════════════════
# 标准一：登录性能 — mock 延迟下全流程 < 2 秒
# ═══════════════════════════════════════════════════════════════════

class TestLoginPerformance(unittest.TestCase):
    """验证登录全流程在模拟网络延迟下耗时 < 2 秒。"""

    def setUp(self):
        """重置登录速率限制器。"""
        import auth_logic
        auth_logic._login_rate_limiter._records.clear()

    def test_1_1_login_under_2s_with_150ms_latency(self):
        """每次存储操作 150ms 延迟下，登录全流程 < 2 秒。

        登录链路存储操作：
        1. get_user_for_login -> get_user_by_name（1 次读）
        2. verify_password（bcrypt CPU，~200ms）
        3. record_login_success -> set_user_by_name + get_meta（1 写 + 1 读）
        总计 3 次存储往返 = 450ms + bcrypt ~200ms ≈ 650ms，远低于 2 秒。
        """
        from auth_logic import login_user

        manager, mock_client = _make_manager(latency=_DEFAULT_LATENCY)
        _create_test_user(manager)

        t_start = time.perf_counter()
        user_data, error = login_user("testuser", _TEST_PASSWORD, manager)
        elapsed = time.perf_counter() - t_start

        self.assertIsNone(error, f"登录应成功: {error}")
        self.assertIsNotNone(user_data)
        self.assertLess(elapsed, _PERF_THRESHOLD,
                        f"登录耗时 {elapsed:.3f}s 超过 {_PERF_THRESHOLD}s 阈值")

    def test_1_2_login_under_2s_with_300ms_latency(self):
        """极端延迟（300ms/次）下仍 < 2 秒，验证优化路径的余量。

        3 次存储往返 = 900ms + bcrypt ~200ms ≈ 1.1s。
        """
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.30)
        _create_test_user(manager)

        t_start = time.perf_counter()
        user_data, error = login_user("testuser", _TEST_PASSWORD, manager)
        elapsed = time.perf_counter() - t_start

        self.assertIsNone(error, f"登录应成功: {error}")
        self.assertLess(elapsed, _PERF_THRESHOLD,
                        f"300ms 延迟下登录耗时 {elapsed:.3f}s 超过 {_PERF_THRESHOLD}s")

    def test_1_3_uses_optimized_single_key_path(self):
        """登录走单 key 优化路径（O(1)），而非全量加载（O(N)）。

        验证：mock_client 的 get_user_by_name 被调用，
        get_record（全量读取）未被调用。
        """
        from auth_logic import login_user

        manager, mock_client = _make_manager(latency=0.0)
        _create_test_user(manager)

        # 用 MagicMock 包装两个方法以追踪调用次数，side_effect 保持原行为
        original_get_user = mock_client.get_user_by_name
        original_get_record = mock_client.get_record
        mock_client.get_user_by_name = MagicMock(side_effect=original_get_user)
        mock_client.get_record = MagicMock(side_effect=original_get_record)

        login_user("testuser", _TEST_PASSWORD, manager)

        # 单 key 读取应被调用
        self.assertGreaterEqual(mock_client.get_user_by_name.call_count, 1,
                               "应通过 get_user_by_name 单 key 读取用户")
        # 全量读取不应被调用（登录优化路径不触发全量加载）
        mock_client.get_record.assert_not_called()

    def test_1_4_login_stats_increment_correctly(self):
        """多次登录后 login_count 正确递增（功能正确性）。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager)

        count1 = login_user("testuser", _TEST_PASSWORD, manager)[0]["stats"]["login_count"]
        count2 = login_user("testuser", _TEST_PASSWORD, manager)[0]["stats"]["login_count"]

        self.assertEqual(count2, count1 + 1, "login_count 应递增")


# ═══════════════════════════════════════════════════════════════════
# 标准二：登录功能 — 正确凭据成功 / 错误密码 / 不存在用户
# ═══════════════════════════════════════════════════════════════════

class TestLoginFunctional(unittest.TestCase):
    """验证登录功能正确性与错误处理。"""

    def setUp(self):
        import auth_logic
        auth_logic._login_rate_limiter._records.clear()

    def test_2_1_correct_credentials_login_success(self):
        """正确凭据登录成功，返回有效用户数据。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager, username="alice", password=_TEST_PASSWORD)

        user_data, error = login_user("alice", _TEST_PASSWORD, manager)

        self.assertIsNone(error, f"正确凭据应登录成功: {error}")
        self.assertIsNotNone(user_data)
        self.assertEqual(user_data["profile"]["username"], "alice")
        self.assertEqual(user_data["profile"]["role"], "user")

    def test_2_2_wrong_password_returns_error(self):
        """错误密码登录失败，返回"密码错误"。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager, username="bob", password=_TEST_PASSWORD)

        user_data, error = login_user("bob", _WRONG_PASSWORD, manager)

        self.assertIsNone(user_data, "错误密码不应返回用户数据")
        self.assertEqual(error, "密码错误")

    def test_2_3_nonexistent_user_returns_error(self):
        """不存在的用户登录失败，返回"用户名不存在"。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager, username="charlie")

        user_data, error = login_user("ghost_user", _TEST_PASSWORD, manager)

        self.assertIsNone(user_data, "不存在的用户不应返回用户数据")
        self.assertEqual(error, "用户名不存在")

    def test_2_4_empty_username_returns_error(self):
        """空用户名被后端防御层拦截。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)

        user_data, error = login_user("", _TEST_PASSWORD, manager)

        self.assertIsNone(user_data)
        self.assertIn("用户名", error)

    def test_2_5_empty_password_returns_error(self):
        """空密码被后端防御层拦截。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager, username="dave")

        user_data, error = login_user("dave", "", manager)

        self.assertIsNone(user_data)
        self.assertIn("密码", error)

    def test_2_6_login_updates_last_active(self):
        """登录后 last_active_at 被更新。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager, username="eve")

        original_active = manager.safe_get_user("eve")[0]["profile"]["last_active_at"]
        time.sleep(0.01)  # 确保时间戳不同

        user_data, _ = login_user("eve", _TEST_PASSWORD, manager)
        updated_active = user_data["profile"]["last_active_at"]

        self.assertGreaterEqual(updated_active, original_active,
                               "last_active_at 应在登录后更新")


# ═══════════════════════════════════════════════════════════════════
# 标准三：Redis 不可用时按降级规则报错（不静默降级）
# ═══════════════════════════════════════════════════════════════════

class TestLoginStorageDegradation(unittest.TestCase):
    """验证 Redis 不可用时登录按降级规则报错。"""

    def setUp(self):
        import auth_logic
        auth_logic._login_rate_limiter._records.clear()

    def test_3_1_redis_down_login_fails_with_error(self):
        """Redis 完全不可用时，登录失败并返回存储错误（不静默成功）。"""
        from auth_logic import login_user

        manager, mock_client = _make_manager(latency=0.0)
        _create_test_user(manager, username="frank")

        # 切换为 Redis 不可用
        mock_client._available = False

        user_data, error = login_user("frank", _TEST_PASSWORD, manager)

        self.assertIsNone(user_data, "Redis 不可用时不应返回用户数据")
        self.assertIsNotNone(error, "Redis 不可用时应返回错误信息")
        # 错误信息应包含存储/网络相关关键词
        self.assertTrue(
            any(kw in error for kw in ("数据加载", "系统异常", "网络", "稍后重试", "登录失败")),
            f"错误信息应表明存储不可用，实际: {error}",
        )

    def test_3_2_redis_down_no_silent_success(self):
        """Redis 不可用时绝不会因降级而静默成功（安全验证）。

        多次尝试确保不会偶然通过。
        """
        from auth_logic import login_user

        for i in range(5):
            with self.subTest(attempt=i):
                import auth_logic
                auth_logic._login_rate_limiter._records.clear()

                manager, mock_client = _make_manager(latency=0.0)
                _create_test_user(manager, username=f"user_{i}")
                mock_client._available = False

                user_data, error = login_user(f"user_{i}", _TEST_PASSWORD, manager)

                self.assertIsNone(user_data,
                                  f"第 {i} 次尝试：Redis 不可用时不应静默成功")
                self.assertIsNotNone(error,
                                     f"第 {i} 次尝试：应返回错误信息")

    def test_3_3_redis_down_error_classified_as_storage_unavailable(self):
        """Redis 不可用时的错误被前端分类为 storage_unavailable。"""
        from auth_components import _classify_login_error
        from auth_logic import login_user

        manager, mock_client = _make_manager(latency=0.0)
        _create_test_user(manager, username="grace")
        mock_client._available = False

        _, error = login_user("grace", _TEST_PASSWORD, manager)

        category, title, hint = _classify_login_error(error)
        self.assertEqual(category, "storage_unavailable",
                         f"Redis 不可用错误应分类为 storage_unavailable，实际: {category}")
        self.assertTrue(title, "应返回非空标题")


# ═══════════════════════════════════════════════════════════════════
# 标准四：登录失败时按失败类型显示明确错误信息
# ═══════════════════════════════════════════════════════════════════

class TestLoginErrorClassification(unittest.TestCase):
    """验证 _classify_login_error 将后端错误正确映射为前端分类。"""

    def test_4_1_user_not_found_classified(self):
        """用户不存在 -> user_not_found 类别。"""
        from auth_components import _classify_login_error

        category, title, hint = _classify_login_error("用户名不存在")

        self.assertEqual(category, "user_not_found")
        self.assertIn("不存在", title)
        self.assertTrue(hint, "应提供补充提示")

    def test_4_2_wrong_password_classified(self):
        """密码错误 -> wrong_password 类别。"""
        from auth_components import _classify_login_error

        category, title, hint = _classify_login_error("密码错误")

        self.assertEqual(category, "wrong_password")
        self.assertIn("密码错误", title)
        self.assertTrue(hint, "应提供补充提示")

    def test_4_3_storage_unavailable_classified(self):
        """存储不可用 -> storage_unavailable 类别（多种错误文案）。"""
        from auth_components import _classify_login_error

        storage_errors = [
            "数据加载失败，请稍后重试",
            "系统异常，请稍后重试",
            "登录失败，请稍后重试",
        ]
        for err in storage_errors:
            with self.subTest(error=err):
                category, title, hint = _classify_login_error(err)
                self.assertEqual(category, "storage_unavailable",
                                 f"错误 '{err}' 应分类为 storage_unavailable")
                self.assertTrue(title)

    def test_4_4_rate_limited_classified(self):
        """限流/锁定 -> rate_limited 类别。"""
        from auth_components import _classify_login_error

        for err in ["登录尝试过于频繁，请稍后重试",
                     "登录尝试次数过多，账户已锁定，请稍后重试"]:
            with self.subTest(error=err):
                category, _, _ = _classify_login_error(err)
                self.assertEqual(category, "rate_limited")

    def test_4_5_account_disabled_classified(self):
        """账户禁用 -> account_disabled 类别。"""
        from auth_components import _classify_login_error

        category, title, hint = _classify_login_error("该账户已被禁用，请联系管理员")

        self.assertEqual(category, "account_disabled")
        self.assertIn("禁用", title)

    def test_4_6_unknown_error_fallback(self):
        """未知错误 -> unknown 类别（兜底）。"""
        from auth_components import _classify_login_error

        category, title, _ = _classify_login_error("某种未知的奇怪错误")

        self.assertEqual(category, "unknown")
        self.assertTrue(title, "未知错误也应有展示文案")

    def test_4_7_empty_error_fallback(self):
        """空错误信息 -> unknown 类别（兜底）。"""
        from auth_components import _classify_login_error

        category, title, _ = _classify_login_error("")

        self.assertEqual(category, "unknown")
        self.assertTrue(title, "空错误也应有兜底文案")


# ═══════════════════════════════════════════════════════════════════
# 标准五：密码校验仍使用 bcrypt，不降级为明文/弱哈希
# ═══════════════════════════════════════════════════════════════════

class TestLoginSecurity(unittest.TestCase):
    """验证登录链路安全性不回退。"""

    def setUp(self):
        import auth_logic
        auth_logic._login_rate_limiter._records.clear()

    def test_5_1_password_hashed_with_bcrypt(self):
        """注册时密码使用 bcrypt 哈希（$2b$ 格式），非明文/SHA-256。"""
        from auth_logic import hash_password

        hashed = hash_password(_TEST_PASSWORD)

        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"),
                        "密码应以 bcrypt 格式存储")
        self.assertNotEqual(hashed, _TEST_PASSWORD, "不应存储明文密码")
        self.assertGreater(len(hashed), 50, "bcrypt 哈希长度应 > 50")

    def test_5_2_bcrypt_rounds_is_12(self):
        """bcrypt rounds=12（安全与性能平衡点，不为性能降级）。"""
        from auth_logic import hash_password

        hashed = hash_password(_TEST_PASSWORD)

        # bcrypt 格式: $2b$<rounds>$<salt+hash>
        parts = hashed.split("$")
        self.assertEqual(parts[1], "2b", "应使用 bcrypt $2b$ 格式")
        self.assertEqual(parts[2], "12", "rounds 应为 12（不降级）")

    def test_5_3_verify_password_rejects_wrong(self):
        """verify_password 正确拒绝错误密码。"""
        from auth_logic import hash_password, verify_password

        hashed = hash_password(_TEST_PASSWORD)

        self.assertTrue(verify_password(_TEST_PASSWORD, hashed))
        self.assertFalse(verify_password(_WRONG_PASSWORD, hashed))

    def test_5_4_stored_hash_is_bcrypt_not_plaintext(self):
        """登录链路中存储的密码哈希是 bcrypt 格式（非明文）。"""
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        _create_test_user(manager, username="heidi")

        user_data, _ = login_user("heidi", _TEST_PASSWORD, manager)

        stored_hash = user_data["profile"]["password_hash"]
        self.assertTrue(stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"),
                        f"存储的密码哈希应为 bcrypt 格式，实际: {stored_hash[:10]}...")

    def test_5_5_sha256_legacy_migration_to_bcrypt(self):
        """旧版 SHA-256 密码登录后自动迁移到 bcrypt（向后兼容）。"""
        import hashlib
        from auth_logic import login_user

        manager, _ = _make_manager(latency=0.0)
        # 创建使用旧版 SHA-256 哈希的用户
        sha256_hash = hashlib.sha256(_TEST_PASSWORD.encode("utf-8")).hexdigest()
        manager.create_user(
            username="ivan",
            password_hash=sha256_hash,
            role="user",
        )

        # 用旧版密码登录（应触发自动迁移）
        user_data, error = login_user("ivan", _TEST_PASSWORD, manager)

        self.assertIsNone(error, f"旧版 SHA-256 用户应能登录: {error}")
        stored_hash = user_data["profile"]["password_hash"]
        # 迁移后应为 bcrypt 格式
        self.assertTrue(stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"),
                        "SHA-256 密码应在登录后迁移到 bcrypt")

    def test_5_6_no_plaintext_password_in_storage(self):
        """存储中不包含明文密码。"""
        from auth_logic import login_user

        manager, mock_client = _make_manager(latency=0.0)
        _create_test_user(manager, username="judith")

        login_user("judith", _TEST_PASSWORD, manager)

        # 检查 mock 存储中不包含明文密码
        all_data = str(mock_client._record)
        self.assertNotIn(_TEST_PASSWORD, all_data,
                         "存储中不应包含明文密码")


# ═══════════════════════════════════════════════════════════════════
# 标准六：前端 _handle_login 错误展示集成
# ═══════════════════════════════════════════════════════════════════

class TestHandleLoginErrorDisplay(unittest.TestCase):
    """验证 _handle_login 在各种失败场景下正确展示错误信息。"""

    def setUp(self):
        import auth_logic
        auth_logic._login_rate_limiter._records.clear()

    def _make_mock_st(self):
        """构造支持 spinner 上下文管理器的 mock streamlit。"""
        mock_st = MagicMock()
        mock_st.session_state = {}
        return mock_st

    @patch("auth_components.login_user")
    @patch("auth_components._get_or_create_manager")
    def test_6_1_wrong_password_shows_error(self, mock_get_manager, mock_login):
        """密码错误时 _handle_login 调用 st.error 展示。"""
        from auth_components import _handle_login

        mock_get_manager.return_value = MagicMock()
        mock_login.return_value = (None, "密码错误")

        mock_st = self._make_mock_st()
        with patch("auth_components.st", mock_st):
            _handle_login("user", "wrong")

        mock_st.error.assert_called_once()
        error_msg = mock_st.error.call_args[0][0]
        self.assertIn("密码错误", error_msg)

    @patch("auth_components.login_user")
    @patch("auth_components._get_or_create_manager")
    def test_6_2_user_not_found_shows_error(self, mock_get_manager, mock_login):
        """用户不存在时 _handle_login 调用 st.error 展示。"""
        from auth_components import _handle_login

        mock_get_manager.return_value = MagicMock()
        mock_login.return_value = (None, "用户名不存在")

        mock_st = self._make_mock_st()
        with patch("auth_components.st", mock_st):
            _handle_login("ghost", "pass")

        mock_st.error.assert_called_once()
        error_msg = mock_st.error.call_args[0][0]
        self.assertIn("不存在", error_msg)

    @patch("auth_components.login_user")
    @patch("auth_components._get_or_create_manager")
    def test_6_3_storage_unavailable_shows_error(self, mock_get_manager, mock_login):
        """存储不可用时 _handle_login 调用 st.error 展示。"""
        from auth_components import _handle_login

        mock_get_manager.return_value = MagicMock()
        mock_login.return_value = (None, "数据加载失败，请稍后重试")

        mock_st = self._make_mock_st()
        with patch("auth_components.st", mock_st):
            _handle_login("user", "pass")

        mock_st.error.assert_called_once()
        error_msg = mock_st.error.call_args[0][0]
        self.assertIn("存储", error_msg)

    @patch("auth_components.login_user")
    @patch("auth_components._get_or_create_manager")
    def test_6_4_rate_limited_shows_warning(self, mock_get_manager, mock_login):
        """限流场景用 st.warning 展示（区别于凭据错误）。"""
        from auth_components import _handle_login

        mock_get_manager.return_value = MagicMock()
        mock_login.return_value = (None, "登录尝试过于频繁，请稍后重试")

        mock_st = self._make_mock_st()
        with patch("auth_components.st", mock_st):
            _handle_login("user", "pass")

        # 限流场景用 warning 而非 error
        mock_st.warning.assert_called_once()
        mock_st.error.assert_not_called()

    @patch("auth_components.login_user")
    @patch("auth_components._get_or_create_manager")
    def test_6_5_error_displayed_with_hint_caption(self, mock_get_manager, mock_login):
        """错误展示附带补充提示 caption。"""
        from auth_components import _handle_login

        mock_get_manager.return_value = MagicMock()
        mock_login.return_value = (None, "密码错误")

        mock_st = self._make_mock_st()
        with patch("auth_components.st", mock_st):
            _handle_login("user", "wrong")

        # 应调用 caption 展示补充提示
        mock_st.caption.assert_called_once()
        hint_text = mock_st.caption.call_args[0][0]
        self.assertTrue(hint_text, "补充提示不应为空")

    @patch("auth_components.login_user")
    @patch("auth_components._get_or_create_manager")
    def test_6_6_success_no_error_displayed(self, mock_get_manager, mock_login):
        """登录成功时不展示错误信息。"""
        from auth_components import _handle_login

        mock_get_manager.return_value = MagicMock()
        mock_login.return_value = ({"profile": {"username": "user"}}, None)

        mock_st = self._make_mock_st()
        mock_st.session_state = {}
        with patch("auth_components.st", mock_st), \
             patch("auth_components.set_auth_success"):
            _handle_login("user", _TEST_PASSWORD)

        mock_st.error.assert_not_called()
        mock_st.warning.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import warnings

    warnings.filterwarnings("ignore", category=DeprecationWarning)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    )

    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors

    print("\n" + "=" * 70)
    print("📊 登录性能与功能验证测试总结")
    print("=" * 70)
    print(f"   标准一（登录性能 < 2s）:     4 项测试")
    print(f"   标准二（登录功能正确性）:    6 项测试")
    print(f"   标准三（Redis 降级报错）:    3 项测试")
    print(f"   标准四（错误分类映射）:      7 项测试")
    print(f"   标准五（bcrypt 安全不回退）: 6 项测试")
    print(f"   标准六（前端错误展示集成）:  6 项测试")
    print(f"   {'─' * 40}")
    print(f"   总计: {total} 个测试")
    print(f"   ✅ 通过: {passed}")
    if failures:
        print(f"   ❌ 失败: {failures}")
    if errors:
        print(f"   ⚠️  错误: {errors}")
    print("=" * 70)

    if failures or errors:
        if failures:
            print("\n失败详情:")
            for test, traceback in result.failures:
                print(f"  ❌ {test}")
        if errors:
            print("\n错误详情:")
            for test, traceback in result.errors:
                print(f"  ⚠️  {test}")
        sys.exit(1)
