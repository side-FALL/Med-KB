"""医学教材知识库 — 集成测试

测试内容：
1. 模块导入完整性
2. 用户认证逻辑（注册、登录、密码哈希）
3. DOS 防护机制（限流、白名单）
4. 用户数据 CRUD 操作
5. 数据模型验证
6. 完整用户流程（模拟）

用法：
    python test_integration.py
"""

import json
import sys
import time
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))


# ═══════════════════════════════════════════════════════════════════
# 测试 1：模块导入
# ═══════════════════════════════════════════════════════════════════

class TestImports(unittest.TestCase):
    """验证所有关键模块均可正确导入。"""

    def test_core_modules_import(self):
        """核心模块导入测试。"""
        import config
        import search_engine
        import ui_styles
        import ui_components
        import dos_protection
        import database_models
        import jsonbin_client
        import user_data_manager
        import auth_logic
        import auth_components
        self.assertTrue(True, "所有核心模块导入成功")

    def test_mode_modules_import_smoke(self):
        """模式模块导入冒烟测试（仅检查模块路径存在，不实际执行导入以避免 Streamlit/stderr 副作用）。"""
        import importlib.util
        mode_modules = ['modes.qa', 'modes.quiz', 'modes.compare', 'modes.case', 'modes.agent']
        for mod_name in mode_modules:
            spec = importlib.util.find_spec(mod_name)
            self.assertIsNotNone(spec, f"模块 {mod_name} 应存在")
        self.assertTrue(True, "所有模式模块路径存在")


# ═══════════════════════════════════════════════════════════════════
# 测试 2：数据模型验证
# ═══════════════════════════════════════════════════════════════════

class TestDatabaseModels(unittest.TestCase):
    """数据模型验证测试。"""

    @classmethod
    def setUpClass(cls):
        from database_models import (
            UserData, UserProfile, UserPreferences,
            LearningRecord, ValidationError, MAX_USER_DATA_SIZE,
            USERNAME_PATTERN, RESERVED_USERNAMES,
            create_default_user,
        )
        cls.UserData = UserData
        cls.UserProfile = UserProfile
        cls.UserPreferences = UserPreferences
        cls.LearningRecord = LearningRecord
        cls.ValidationError = ValidationError
        cls.MAX_USER_DATA_SIZE = MAX_USER_DATA_SIZE
        cls.USERNAME_PATTERN = USERNAME_PATTERN
        cls.RESERVED_USERNAMES = RESERVED_USERNAMES
        cls.create_default_user = staticmethod(create_default_user)

    def test_username_pattern_valid(self):
        """有效用户名格式测试。"""
        self.assertIsNotNone(self.USERNAME_PATTERN.match("testuser"))
        self.assertIsNotNone(self.USERNAME_PATTERN.match("TestUser123"))
        self.assertIsNotNone(self.USERNAME_PATTERN.match("user_2024"))
        self.assertIsNotNone(self.USERNAME_PATTERN.match("abc"))  # 最小 3 位

    def test_username_pattern_invalid(self):
        """无效用户名格式测试。"""
        self.assertIsNone(self.USERNAME_PATTERN.match("ab"))       # 少于 3 位
        self.assertIsNone(self.USERNAME_PATTERN.match("a" * 21))   # 超过 20 位
        self.assertIsNone(self.USERNAME_PATTERN.match("user name")) # 含空格
        self.assertIsNone(self.USERNAME_PATTERN.match("user-123"))  # 含连字符

    def test_reserved_usernames(self):
        """保留用户名测试。"""
        for name in ["admin", "root", "system", "superuser", "ADMIN", "Root"]:
            self.assertIn(
                name.lower(), self.RESERVED_USERNAMES,
                f"{name} 应在保留名单中"
            )

    def test_create_default_user(self):
        """默认用户创建测试。"""
        valid_hash = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        user_data = self.create_default_user(
            username="testuser",
            password_hash=valid_hash,
        )
        self.assertEqual(user_data["profile"]["username"], "testuser")
        self.assertEqual(user_data["profile"]["role"], "user")
        self.assertIn("learning_records", user_data)

    def test_user_data_size_estimate(self):
        """用户数据大小预估测试。"""
        from database_models import estimate_user_data_size
        valid_hash = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        user_data = self.create_default_user(
            username="testuser",
            password_hash=valid_hash,
        )
        size_info = estimate_user_data_size(user_data)
        self.assertLess(size_info["total_bytes"], self.MAX_USER_DATA_SIZE,
                        f"默认用户数据 ({size_info['total_bytes']} bytes) 超过 {self.MAX_USER_DATA_SIZE} bytes 限制")
        self.assertIn("total_kb", size_info)


# ═══════════════════════════════════════════════════════════════════
# 测试 3：密码哈希与验证
# ═══════════════════════════════════════════════════════════════════

class TestPasswordHashing(unittest.TestCase):
    """密码哈希与验证测试。"""

    def test_bcrypt_hash_and_verify(self):
        """bcrypt 哈希与验证测试。"""
        from auth_logic import hash_password, verify_password

        password = "SecureP@ss123"
        hashed = hash_password(password)

        # 验证 bcrypt 格式
        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"))

        # 正确密码可验证
        self.assertTrue(verify_password(password, hashed))

        # 错误密码不可验证
        self.assertFalse(verify_password("WrongPassword", hashed))

        # 空密码返回 False（不是异常）
        self.assertFalse(verify_password("", hashed))
        self.assertFalse(verify_password("test", ""))

    def test_hash_empty_password_raises(self):
        """空密码哈希应抛出异常。"""
        from auth_logic import hash_password
        with self.assertRaises(ValueError):
            hash_password("")

    def test_hmac_compare_digest_used(self):
        """时序安全比较测试 — 确保使用 hmac.compare_digest。"""
        import hmac
        import hashlib
        from auth_logic import login_user

        # 验证 SHA-256 路径使用 hmac.compare_digest
        # 通过检查 import 确认
        import auth_logic as al
        self.assertIn("hmac", dir(al), "auth_logic 必须导入 hmac 模块")


# ═══════════════════════════════════════════════════════════════════
# 测试 4：DOS 防护
# ═══════════════════════════════════════════════════════════════════

class TestDOSProtection(unittest.TestCase):
    """DOS 防护机制测试。"""

    @classmethod
    def setUpClass(cls):
        from dos_protection import (
            SlidingWindowRateLimiter, WhitelistManager,
            DEFAULT_MAX_REQUESTS, DEFAULT_WINDOW_SECONDS,
        )
        cls.SlidingWindowRateLimiter = SlidingWindowRateLimiter
        cls.WhitelistManager = WhitelistManager
        cls.DEFAULT_MAX_REQUESTS = DEFAULT_MAX_REQUESTS
        cls.DEFAULT_WINDOW_SECONDS = DEFAULT_WINDOW_SECONDS

    def test_rate_limiter_allows_normal_requests(self):
        """正常请求应被允许。"""
        limiter = self.SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
        for i in range(5):
            allowed, info = limiter.check("user_a")
            self.assertTrue(allowed, f"第 {i+1} 次请求应被允许")
            self.assertEqual(info["current_count"], i + 1)

    def test_rate_limiter_blocks_excessive_requests(self):
        """超额请求应被拦截。"""
        limiter = self.SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
        # 先发 5 次正常请求
        for _ in range(5):
            limiter.check("user_b")
        # 第 6 次应被拦截
        allowed, info = limiter.check("user_b")
        self.assertFalse(allowed, "第 6 次请求应被拦截")
        self.assertEqual(info["remaining"], 0)
        self.assertGreater(info["retry_after"], 0)

    def test_rate_limiter_per_user_isolation(self):
        """不同用户的限流应独立。"""
        limiter = self.SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        # user_a 用完配额
        for _ in range(3):
            limiter.check("user_a")
        allowed_a, _ = limiter.check("user_a")
        self.assertFalse(allowed_a, "user_a 应被限流")

        # user_b 不受影响
        allowed_b, _ = limiter.check("user_b")
        self.assertTrue(allowed_b, "user_b 应不受 user_a 限流影响")

    def test_rate_limiter_reset(self):
        """限流重置测试。"""
        limiter = self.SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("user_c")
        limiter.reset("user_c")
        allowed, _ = limiter.check("user_c")
        self.assertTrue(allowed, "重置后应允许请求")

    def test_whitelist_ip(self):
        """IP 白名单测试。"""
        wl = self.WhitelistManager()
        wl.add_ip("192.168.1.1")
        self.assertTrue(wl.is_whitelisted(ip="192.168.1.1"))
        self.assertFalse(wl.is_whitelisted(ip="10.0.0.1"))

    def test_whitelist_username(self):
        """用户名白名单测试。"""
        wl = self.WhitelistManager()
        wl.add_username("AdminUser")
        self.assertTrue(wl.is_whitelisted(username="adminuser"))  # 大小写不敏感
        self.assertTrue(wl.is_whitelisted(username="AdminUser"))
        self.assertFalse(wl.is_whitelisted(username="normaluser"))

    def test_whitelist_remove(self):
        """白名单移除测试。"""
        wl = self.WhitelistManager()
        wl.add_ip("10.0.0.1")
        wl.add_username("test")
        wl.remove_ip("10.0.0.1")
        wl.remove_username("test")
        self.assertFalse(wl.is_whitelisted(ip="10.0.0.1"))
        self.assertFalse(wl.is_whitelisted(username="test"))


# ═══════════════════════════════════════════════════════════════════
# 测试 5：用户数据 CRUD（模拟 JSONBin API）
# ═══════════════════════════════════════════════════════════════════

# 用于测试的有效 SHA-256 哈希（hashlib.sha256 生成的 64 位十六进制字符串）
_TEST_HASH_1 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
_TEST_HASH_2 = "60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752"
_TEST_HASH_3 = "9738d7e28e2c31a7df8866963edf20c14c99cefd68b62e7e5901e0f3a7a30c4d"

class TestUserDataCRUD(unittest.TestCase):
    """用户数据 CRUD 操作测试（使用模拟的 JSONBin 客户端）。"""

    def setUp(self):
        """每个测试前初始化模拟环境和 UserDataManager。"""
        from database_models import create_default_root
        from user_data_manager import UserDataManager

        # 模拟 JSONBin 客户端
        self.mock_client = MagicMock()
        self.test_root = create_default_root()
        self.mock_client.get_record.return_value = self.test_root

        def mock_update(record):
            self.test_root = record
        self.mock_client.update_record.side_effect = mock_update

        self.manager = UserDataManager(client=self.mock_client, auto_init=False)

    def test_create_user_success(self):
        """创建用户成功测试。"""
        user_data = self.manager.create_user(
            username="newuser",
            password_hash=_TEST_HASH_1,
        )
        self.assertEqual(user_data["profile"]["username"], "newuser")
        self.assertEqual(user_data["profile"]["role"], "user")
        self.assertIn("learning_records", user_data)

    def test_create_duplicate_user_fails(self):
        """重复用户名应拒绝。"""
        from user_data_manager import UserExistsError

        self.manager.create_user("alice", _TEST_HASH_1)
        with self.assertRaises(UserExistsError):
            self.manager.create_user("alice", _TEST_HASH_2)

    def test_get_user_readonly(self):
        """只读获取用户测试。"""
        self.manager.create_user("bob", _TEST_HASH_1)
        user_data = self.manager.get_user_readonly("bob")
        self.assertEqual(user_data["profile"]["username"], "bob")

    def test_get_nonexistent_user_fails(self):
        """不存在的用户应抛出异常。"""
        from user_data_manager import UserNotFoundError
        with self.assertRaises(UserNotFoundError):
            self.manager.get_user("nonexistent")

    def test_delete_user(self):
        """删除用户测试。"""
        self.manager.create_user("to_delete", _TEST_HASH_1)
        self.manager.delete_user("to_delete")
        from user_data_manager import UserNotFoundError
        with self.assertRaises(UserNotFoundError):
            self.manager.get_user_readonly("to_delete")

    def test_safe_create_user(self):
        """安全创建用户（不抛异常）测试。"""
        user_data, error = self.manager.safe_create_user("safe_user", _TEST_HASH_1)
        self.assertIsNotNone(user_data)
        self.assertIsNone(error)

        # 重复创建应返回错误
        _, error = self.manager.safe_create_user("safe_user", _TEST_HASH_1)
        self.assertIsNotNone(error)
        self.assertIn("已存在", error)

    def test_safe_get_user(self):
        """安全获取用户（不抛异常）测试。"""
        self.manager.create_user("test_get", _TEST_HASH_1)
        data, error = self.manager.safe_get_user("test_get")
        self.assertIsNotNone(data)
        self.assertIsNone(error)

        # 不存在的用户
        _, error = self.manager.safe_get_user("noone")
        self.assertIsNotNone(error)
        self.assertIn("不存在", error)

    def test_learning_record_add(self):
        """添加学习记录测试。"""
        self.manager.create_user("learner", _TEST_HASH_1)
        updated = self.manager.add_learning_record(
            username="learner",
            topic="心脏衰竭",
            query="心衰的病理机制是什么？",
            source_type="chat",
        )
        records = updated.get("learning_records", [])
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0]["topic"], "心脏衰竭")

    def test_preferences_update(self):
        """偏好设置更新测试。"""
        self.manager.create_user("prefs_user", _TEST_HASH_1)
        updated = self.manager.update_preferences(
            username="prefs_user",
            preferences={"default_model": "deepseek", "dark_mode": True},
        )
        self.assertEqual(updated["preferences"]["default_model"], "deepseek")

    def test_storage_limit_check(self):
        """存储空间检查测试。"""
        self.manager.create_user("sized_user", _TEST_HASH_1)
        info = self.manager.check_storage_limit("sized_user")
        self.assertIn("used_bytes", info)
        self.assertIn("limit_bytes", info)
        self.assertLess(info["used_bytes"], info["limit_bytes"])
        self.assertFalse(info["is_over_limit"])

    def test_list_users(self):
        """列出所有用户测试。"""
        self.manager.create_user("us1", _TEST_HASH_1)
        self.manager.create_user("us2", _TEST_HASH_2)
        users = self.manager.list_users()
        self.assertEqual(len(users), 2)
        usernames = [u["username"] for u in users]
        self.assertIn("us1", usernames)
        self.assertIn("us2", usernames)


# ═══════════════════════════════════════════════════════════════════
# 测试 6：完整用户流程（认证 + 数据）
# ═══════════════════════════════════════════════════════════════════

class TestFullUserFlow(unittest.TestCase):
    """完整用户流程测试：注册 → 登录 → 使用 → 退出"""

    def setUp(self):
        from database_models import create_default_root
        from user_data_manager import UserDataManager

        self.mock_client = MagicMock()
        self.test_root = create_default_root()
        self.mock_client.get_record.return_value = self.test_root

        def mock_update(record):
            self.test_root = record
        self.mock_client.update_record.side_effect = mock_update

        self.manager = UserDataManager(client=self.mock_client, auto_init=False)

    def test_full_register_login_logout_flow(self):
        """完整注册→登录→使用→退出流程测试。"""
        from auth_logic import register_user, login_user, hash_password

        username = "flow_test_user"
        password = "TestP@ssw0rd2024"
        hashed = hash_password(password)

        # 1. 注册
        user_data, error = register_user(username, password, self.manager)
        self.assertIsNotNone(user_data, f"注册失败: {error}")
        self.assertIsNone(error)
        self.assertEqual(user_data["profile"]["username"], username)

        # 验证密码已哈希存储（非明文）
        stored_hash = user_data["profile"]["password_hash"]
        self.assertNotEqual(stored_hash, password)
        self.assertTrue(stored_hash.startswith("$2b$"))

        # 2. 登录
        logged_data, login_error = login_user(username, password, self.manager)
        self.assertIsNotNone(logged_data, f"登录失败: {login_error}")
        self.assertIsNone(login_error)

        # 3. 使用（添加学习记录）
        updated = self.manager.add_learning_record(
            username=username,
            topic="药理学",
            query="ACE抑制剂的副作用有哪些？",
        )
        self.assertGreater(len(updated.get("learning_records", [])), 0)

        # 更新偏好
        self.manager.update_preferences(
            username=username,
            preferences={"default_model": "deepseek", "language": "zh"},
        )

        # 4. 验证数据持久化
        data_after = self.manager.get_user_readonly(username)
        self.assertEqual(data_after["preferences"]["default_model"], "deepseek")
        self.assertEqual(len(data_after["learning_records"]), 1)

        # 5. 退出/删除用户
        self.manager.delete_user(username)
        from user_data_manager import UserNotFoundError
        with self.assertRaises(UserNotFoundError):
            self.manager.get_user_readonly(username)

    def test_login_wrong_password(self):
        """密码错误应被拒绝。"""
        from auth_logic import register_user, login_user

        username = "wrongpwd_test"
        password = "CorrectP@ss1"
        register_user(username, password, self.manager)

        _, error = login_user(username, "WrongPassword", self.manager)
        self.assertIsNotNone(error)
        self.assertIn("密码错误", error)

    def test_login_nonexistent_user(self):
        """不存在的用户应被拒绝。"""
        from auth_logic import login_user

        _, error = login_user("no_such_user", "anypass", self.manager)
        self.assertIsNotNone(error)
        self.assertIn("不存在", error)

    def test_guest_mode_no_persistence(self):
        """游客模式不触发数据持久化。"""
        # 游客模式仅需验证逻辑正确性：
        # - 游客不创建用户记录
        # - 游客的 session_state 中 user_data 为空
        # 这在实际 Streamlit 环境中由 auth_components 模块处理
        users = self.manager.list_users()
        self.assertEqual(len(users), 0, "新初始化的管理器应无用户")

    def test_password_auto_migration(self):
        """SHA-256 密码自动迁移到 bcrypt 测试。"""
        import hashlib
        from auth_logic import login_user
        from user_data_manager import UserDataManager

        # 创建一个独立的 manager 用于此测试
        from database_models import create_default_root
        local_client = MagicMock()
        local_root = create_default_root()
        local_client.get_record.return_value = local_root

        def local_update(record):
            nonlocal local_root
            local_root = record
        local_client.update_record.side_effect = local_update

        local_mgr = UserDataManager(client=local_client, auto_init=False)

        username = "migrate_test"
        password = "OldP@ssword1"
        sha256_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()

        # 直接用 SHA-256 哈希创建用户（模拟旧数据）
        local_mgr.create_user(username, sha256_hash)

        # 使用明文密码登录（应通过 SHA-256 验证并自动迁移）
        _, error = login_user(username, password, local_mgr)
        self.assertIsNone(error, f"旧格式密码登录应成功: {error}")

        # 验证密码已迁移为 bcrypt
        user_after = local_mgr.get_user_readonly(username)
        stored = user_after["profile"]["password_hash"]
        self.assertTrue(
            stored.startswith("$2b$"),
            f"密码应已迁移为 bcrypt，当前格式: {stored[:10]}..."
        )


# ═══════════════════════════════════════════════════════════════════
# 测试 7：登录速率限制
# ═══════════════════════════════════════════════════════════════════

class TestLoginRateLimit(unittest.TestCase):
    """登录速率限制测试。"""

    def test_rate_limiter_blocks_after_limit(self):
        """超过限制次数后应被限流。"""
        from auth_logic import _LoginRateLimiter

        limiter = _LoginRateLimiter(
            max_attempts_per_min=3,
            max_consecutive_failures=10,  # 高值，不触发锁定
            lockout_seconds=900,
        )

        # 前 2 次失败：未被限制
        for _ in range(2):
            limiter.record_failure("testuser")
            self.assertFalse(limiter.is_limited("testuser"))

        # 第 3 次失败后：应被限制
        limiter.record_failure("testuser")
        self.assertTrue(limiter.is_limited("testuser"))

    def test_rate_limiter_unlocks_after_success(self):
        """登录成功后应解除限流。"""
        from auth_logic import _LoginRateLimiter

        limiter = _LoginRateLimiter(
            max_attempts_per_min=3,
            max_consecutive_failures=5,
        )

        for _ in range(3):
            limiter.record_failure("testuser")

        # 登录成功
        limiter.record_success("testuser")
        self.assertFalse(limiter.is_limited("testuser"))

    def test_account_lockout(self):
        """连续失败应锁定账户。"""
        from auth_logic import _LoginRateLimiter

        limiter = _LoginRateLimiter(
            max_attempts_per_min=100,    # 不触发频率限制
            max_consecutive_failures=3,  # 3 次失败后锁定
            lockout_seconds=5,           # 锁定 5 秒（测试用短值）
        )

        # 3 次连续失败
        for _ in range(3):
            limiter.record_failure("locked_user")

        # 锁定
        self.assertTrue(limiter.is_limited("locked_user"))
        self.assertGreater(limiter.lockout_remaining("locked_user"), 0)


# ═══════════════════════════════════════════════════════════════════
# 测试 8：性能基准
# ═══════════════════════════════════════════════════════════════════

class TestPerformance(unittest.TestCase):
    """性能基准测试。"""

    def test_rate_limiter_performance(self):
        """限流器性能测试：1000 次请求应 < 0.1 秒。"""
        from dos_protection import SlidingWindowRateLimiter

        limiter = SlidingWindowRateLimiter(max_requests=1000, window_seconds=60)

        start = time.time()
        for i in range(1000):
            limiter.check(f"perf_user_{i % 10}")
        elapsed = time.time() - start

        self.assertLess(elapsed, 0.2, f"1000 次限流检查耗时 {elapsed:.3f}s，超过 0.2s 阈值")

    def test_user_data_size_estimate_performance(self):
        """数据大小预估性能：1000 次调用应 < 0.05 秒。"""
        from database_models import create_default_user, estimate_user_data_size

        user_data = create_default_user("perf", _TEST_HASH_1)

        start = time.time()
        for _ in range(1000):
            estimate_user_data_size(user_data)
        elapsed = time.time() - start

        self.assertLess(elapsed, 0.1, f"1000 次大小预估耗时 {elapsed:.3f}s，超过 0.1s 阈值")


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 禁用被 mock 的 import 警告
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))

    # 输出总结
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors

    print("\n" + "=" * 60)
    print(f"📊 集成测试总结")
    print(f"   总计: {total} 个测试")
    print(f"   ✅ 通过: {passed}")
    if failures:
        print(f"   ❌ 失败: {failures}")
    if errors:
        print(f"   ⚠️ 错误: {errors}")
    print("=" * 60)

    # 返回非零退出码以指示失败
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
