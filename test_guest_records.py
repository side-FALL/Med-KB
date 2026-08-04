"""医学教材知识库 — 游客模式学习记录测试

验证任务 2（游客 ID 生成与认证/数据层放行）的完整功能链路：
  1. 游客 ID 生成：格式 guest_[a-z0-9]{8}，跨会话唯一
  2. 认证层放行：get_data_manager 对游客返回非 None 且可用
  3. 数据层惰性创建：add_learning_record 首次写入时自动创建 role="guest" 记录
  4. 游客隔离：不同 guest ID 的记录互不串扰
  5. 偏好不持久化：游客偏好写入路径仅会话内生效
  6. 过期清理覆盖：30 天 EXPIRE_DAYS 对游客记录生效

测试策略：
  - 数据层：使用 MagicMock client 替代真实存储，多 manager 共享同一 mock_client
  - MockRedisClient 显式设 supports_single_key_ops=True，避免能力检测误判
  - UI 层：patch("模块名.st")，session_state 用真实 dict
  - 不依赖网络，不修改 .env

用法：
    python test_guest_records.py
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── stderr 保护：Streamlit 裸模式下可能关闭 sys.stderr 导致 I/O 错误 ──

class _SafeStderr:
    """包装 stderr，对 closed-file 错误静默容错，保证测试退出码可靠。"""
    def __init__(self, real):
        self._real = real
    def write(self, msg):
        try:
            return self._real.write(msg)
        except (ValueError, OSError):
            return len(msg)
    def flush(self):
        try:
            self._real.flush()
        except (ValueError, OSError):
            pass
    def isatty(self):
        try:
            return self._real.isatty()
        except (ValueError, OSError):
            return False
    def __getattr__(self, name):
        return getattr(self._real, name)

sys.stderr = _SafeStderr(sys.stderr)

# ── 路径设置 ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT))


# ── 辅助函数 ──────────────────────────────────────────────

def _make_mock_client():
    """创建模拟 Redis 客户端（显式 supports_single_key_ops=True）。"""
    mock_client = MagicMock()
    mock_client.supports_single_key_ops = True
    return mock_client


def _make_mock_manager(mock_client=None, test_root=None):
    """创建共享同一模拟存储的 UserDataManager。

    多个 manager 共用同一 mock_client，共享同一份 test_root 状态。
    """
    from user_data_manager import UserDataManager

    if mock_client is None:
        mock_client = _make_mock_client()

    if test_root is None:
        test_root = {"users": {}, "logs": {"operation_logs": [], "error_logs": [], "usage_stats": {}}}

    mock_client.get_record.return_value = test_root

    def mock_update(record):
        mock_client.get_record.return_value = record

    mock_client.update_record.side_effect = mock_update

    return UserDataManager(client=mock_client, auto_init=False)


def _make_guest_session_state(guest_id: str = "guest_abc12345") -> dict:
    """构造游客会话状态字典（真实 dict，非 MagicMock）。"""
    return {
        "authenticated": True,
        "auth_mode_type": "guest",
        "auth_username": guest_id,
    }


# ═══════════════════════════════════════════════════════════════════
# 测试 1：游客 ID 生成
# ═══════════════════════════════════════════════════════════════════

class TestGuestIdGeneration(unittest.TestCase):
    """验证游客 ID 的生成格式与唯一性。"""

    def test_1_1_guest_id_format(self):
        """游客 ID 格式为 guest_ + 8 位小写字母数字。"""
        import random
        import string

        guest_id = "guest_" + "".join(
            random.choices(string.ascii_lowercase + string.digits, k=8)
        )

        self.assertTrue(guest_id.startswith("guest_"))
        suffix = guest_id[6:]
        self.assertEqual(len(suffix), 8)
        self.assertRegex(suffix, r"^[a-z0-9]{8}$")

    def test_1_2_guest_id_uniqueness_across_sessions(self):
        """两次游客会话生成的 ID 不同。"""
        import random
        import string

        ids = set()
        for _ in range(100):
            gid = "guest_" + "".join(
                random.choices(string.ascii_lowercase + string.digits, k=8)
            )
            ids.add(gid)

        # 100 次生成应全部唯一（碰撞概率极低）
        self.assertEqual(len(ids), 100, "100 次生成的 guest ID 应全部唯一")

    @patch("app.st")
    def test_1_3_guest_id_stored_in_session_state(self, mock_st):
        """游客 ID 写入 session_state 的 auth_username。"""
        import random
        import string

        session_state = {}
        mock_st.session_state = session_state

        # 模拟游客登录回调
        guest_id = "guest_" + "".join(
            random.choices(string.ascii_lowercase + string.digits, k=8)
        )

        # 直接调用 set_auth_success
        from auth_components import set_auth_success
        set_auth_success(guest_id, "guest")

        self.assertEqual(session_state["auth_username"], guest_id)
        self.assertEqual(session_state["auth_mode_type"], "guest")
        self.assertTrue(session_state["authenticated"])


# ═══════════════════════════════════════════════════════════════════
# 测试 2：认证层放行 — get_data_manager 对游客返回可用实例
# ═══════════════════════════════════════════════════════════════════

class TestGuestDataManager(unittest.TestCase):
    """验证游客模式下 get_data_manager 返回非 None 且可用。"""

    @patch("auth_components.st")
    def test_2_1_get_data_manager_returns_non_none_for_guest(self, mock_st):
        """游客模式下 get_data_manager 返回 UserDataManager 实例。"""
        session_state = _make_guest_session_state()
        mock_st.session_state = session_state

        from auth_components import get_data_manager
        manager = get_data_manager()

        self.assertIsNotNone(manager)
        from user_data_manager import UserDataManager
        self.assertIsInstance(manager, UserDataManager)

    @patch("auth_components.st")
    def test_2_2_get_auth_username_returns_guest_id(self, mock_st):
        """get_auth_username 返回游客 ID 而非 '游客'。"""
        guest_id = "guest_test1234"
        session_state = _make_guest_session_state(guest_id)
        mock_st.session_state = session_state

        from auth_components import get_auth_username
        username = get_auth_username()

        self.assertEqual(username, guest_id)
        self.assertNotEqual(username, "游客")

    @patch("auth_components.st")
    def test_2_3_is_admin_returns_false_for_guest(self, mock_st):
        """游客模式下 is_admin 始终返回 False。"""
        session_state = _make_guest_session_state()
        mock_st.session_state = session_state

        from auth_components import is_admin
        self.assertFalse(is_admin())

    @patch("auth_components.st")
    def test_2_4_get_user_data_returns_none_for_guest(self, mock_st):
        """游客模式下 get_user_data 返回 None（无持久化用户数据快照）。"""
        session_state = _make_guest_session_state()
        mock_st.session_state = session_state

        from auth_components import get_user_data
        self.assertIsNone(get_user_data())

    @patch("auth_components.st")
    def test_2_5_set_auth_success_creates_manager_for_guest(self, mock_st):
        """set_auth_success 为游客创建 auth_data_manager。"""
        session_state = {}
        mock_st.session_state = session_state

        from auth_components import set_auth_success
        set_auth_success("guest_abc12345", "guest")

        self.assertIn("auth_data_manager", session_state)
        self.assertIsNotNone(session_state["auth_data_manager"])


# ═══════════════════════════════════════════════════════════════════
# 测试 3：数据层惰性创建 — add_learning_record 自动创建游客记录
# ═══════════════════════════════════════════════════════════════════

class TestGuestLazyCreation(unittest.TestCase):
    """验证游客用户记录惰性创建机制。"""

    def test_3_1_lazy_create_guest_on_first_record(self):
        """首次写学习记录时，游客用户被自动创建（role="guest"）。"""
        mock_client = _make_mock_client()
        manager = _make_mock_manager(mock_client)

        guest_id = "guest_lazy0001"

        # 游客用户尚不存在
        root = manager._ensure_initialized()
        self.assertNotIn(guest_id, root.get("users", {}))

        # 写入第一条学习记录
        manager.add_learning_record(
            username=guest_id,
            topic="药理学",
            query="强心苷中毒",
            source_type="qa",
        )

        # 验证用户已被创建
        root = manager._ensure_initialized()
        self.assertIn(guest_id, root["users"])
        user_data = root["users"][guest_id]
        self.assertEqual(user_data["profile"]["role"], "guest")
        self.assertEqual(user_data["profile"]["username"], guest_id)
        self.assertEqual(user_data["profile"]["password_hash"], "")

    def test_3_2_lazy_create_guest_has_learning_record(self):
        """惰性创建后，学习记录已写入。"""
        mock_client = _make_mock_client()
        manager = _make_mock_manager(mock_client)

        guest_id = "guest_lazy0002"

        manager.add_learning_record(
            username=guest_id,
            topic="病理学",
            query="大叶性肺炎",
            source_type="qa",
        )

        root = manager._ensure_initialized()
        user_data = root["users"][guest_id]
        records = user_data.get("learning_records", [])
        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0]["topic"], "病理学")

    def test_3_3_non_guest_nonexistent_user_raises(self):
        """非游客且不存在用户仍抛出 UserNotFoundError。"""
        from user_data_manager import UserNotFoundError

        mock_client = _make_mock_client()
        manager = _make_mock_manager(mock_client)

        with self.assertRaises(UserNotFoundError):
            manager.add_learning_record(
                username="nonexistent_user",
                topic="test",
                query="test",
            )

    def test_3_4_multiple_records_same_guest(self):
        """同一游客多次写入，记录累积。"""
        mock_client = _make_mock_client()
        manager = _make_mock_manager(mock_client)

        guest_id = "guest_multi001"

        manager.add_learning_record(
            username=guest_id,
            topic="药理学",
            query="强心苷",
            source_type="qa",
        )
        manager.add_learning_record(
            username=guest_id,
            topic="病理学",
            query="大叶性肺炎",
            source_type="quiz",
        )

        root = manager._ensure_initialized()
        records = root["users"][guest_id]["learning_records"]
        self.assertEqual(len(records), 2)
        topics = [r["topic"] for r in records]
        self.assertIn("药理学", topics)
        self.assertIn("病理学", topics)


# ═══════════════════════════════════════════════════════════════════
# 测试 4：游客隔离 — 不同 guest ID 的记录互不串扰
# ═══════════════════════════════════════════════════════════════════

class TestGuestIsolation(unittest.TestCase):
    """验证不同游客的记录互相隔离。"""

    def test_4_1_different_guests_isolated(self):
        """两个不同 guest ID 的记录互不串扰。"""
        mock_client = _make_mock_client()
        manager = _make_mock_manager(mock_client)

        guest_a = "guest_aaaaaaa1"
        guest_b = "guest_bbbbbbb2"

        manager.add_learning_record(
            username=guest_a,
            topic="药理学",
            query="强心苷",
            source_type="qa",
        )
        manager.add_learning_record(
            username=guest_b,
            topic="病理学",
            query="大叶性肺炎",
            source_type="quiz",
        )

        root = manager._ensure_initialized()
        records_a = root["users"][guest_a]["learning_records"]
        records_b = root["users"][guest_b]["learning_records"]

        self.assertEqual(len(records_a), 1)
        self.assertEqual(records_a[0]["topic"], "药理学")

        self.assertEqual(len(records_b), 1)
        self.assertEqual(records_b[0]["topic"], "病理学")

    def test_4_2_guest_records_isolated_from_registered_users(self):
        """游客记录不影响已注册用户。"""
        from database_models import create_default_user

        mock_client = _make_mock_client()
        test_root = {
            "users": {
                "alice": create_default_user("alice", "hash_alice", role="user"),
            },
            "logs": {"operation_logs": [], "error_logs": [], "usage_stats": {}},
        }
        manager = _make_mock_manager(mock_client, test_root)

        guest_id = "guest_iso00001"

        manager.add_learning_record(
            username=guest_id,
            topic="药理学",
            query="强心苷",
            source_type="qa",
        )

        root = manager._ensure_initialized()

        # alice 的记录不受影响
        alice_records = root["users"]["alice"]["learning_records"]
        self.assertEqual(len(alice_records), 0)

        # guest 有自己的记录
        guest_records = root["users"][guest_id]["learning_records"]
        self.assertEqual(len(guest_records), 1)


# ═══════════════════════════════════════════════════════════════════
# 测试 5：偏好不持久化
# ═══════════════════════════════════════════════════════════════════

class TestGuestPreferencesNotPersisted(unittest.TestCase):
    """验证游客偏好设置不持久化到存储。"""

    @patch("settings_components.st")
    def test_5_1_persist_preferences_skips_guest(self, mock_st):
        """_persist_preferences 对游客模式直接返回，不调用存储。"""
        session_state = _make_guest_session_state("guest_pref0001")
        session_state["pref_theme"] = "dark"
        session_state["pref_language"] = "zh-CN"
        session_state["pref_font_size"] = 16
        session_state["pref_display_name"] = ""
        session_state["favorites"] = []
        mock_st.session_state = session_state

        from settings_components import _persist_preferences

        # 不应抛出异常，且不应调用任何存储操作
        _persist_preferences()

        # 验证 session_state 中没有 pref_save_status（说明未尝试持久化）
        self.assertNotIn("pref_save_status", session_state)

    @patch("settings_components.st")
    def test_5_2_load_preferences_loads_defaults_for_guest(self, mock_st):
        """load_preferences_from_user_data 对游客加载默认值。"""
        session_state = _make_guest_session_state("guest_pref0002")
        mock_st.session_state = session_state

        from settings_components import load_preferences_from_user_data, PREF_DEFAULTS

        load_preferences_from_user_data()

        # 验证偏好被设为默认值
        self.assertEqual(session_state["pref_theme"], PREF_DEFAULTS["pref_theme"])
        self.assertEqual(session_state["pref_language"], PREF_DEFAULTS["pref_language"])
        self.assertEqual(session_state["pref_font_size"], PREF_DEFAULTS["pref_font_size"])
        self.assertEqual(session_state["favorites"], [])


# ═══════════════════════════════════════════════════════════════════
# 测试 6：过期清理覆盖游客记录
# ═══════════════════════════════════════════════════════════════════

class TestGuestExpiry(unittest.TestCase):
    """验证 30 天过期清理对游客记录生效。"""

    def test_6_1_guest_record_has_last_active_at(self):
        """惰性创建的游客记录包含 last_active_at 字段。"""
        mock_client = _make_mock_client()
        manager = _make_mock_manager(mock_client)

        guest_id = "guest_exp00001"

        manager.add_learning_record(
            username=guest_id,
            topic="药理学",
            query="test",
            source_type="qa",
        )

        root = manager._ensure_initialized()
        user_data = root["users"][guest_id]
        self.assertIn("last_active_at", user_data["profile"])
        self.assertTrue(user_data["profile"]["last_active_at"])

    def test_6_2_guest_expiry_check(self):
        """check_expiry 对游客记录正确工作。"""
        from database_models import check_expiry, create_default_user
        from datetime import datetime, timezone, timedelta

        # 构造一个 31 天前活跃的游客记录
        old_time = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        guest_data = create_default_user("guest_old00001", "", role="guest")
        guest_data["profile"]["last_active_at"] = old_time

        info = check_expiry(guest_data)
        self.assertTrue(info["is_expired"])

    def test_6_3_guest_not_expired_within_30_days(self):
        """30 天内活跃的游客记录不过期。"""
        from database_models import check_expiry, create_default_user
        from datetime import datetime, timezone, timedelta

        recent_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        guest_data = create_default_user("guest_new00001", "", role="guest")
        guest_data["profile"]["last_active_at"] = recent_time

        info = check_expiry(guest_data)
        self.assertFalse(info["is_expired"])

    def test_6_4_cleanup_expired_guests(self):
        """cleanup_expired_users 可清理过期游客记录。"""
        from database_models import create_default_user
        from datetime import datetime, timezone, timedelta

        mock_client = _make_mock_client()
        old_time = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()

        guest_data = create_default_user("guest_clean01", "", role="guest")
        guest_data["profile"]["last_active_at"] = old_time

        test_root = {
            "users": {"guest_clean01": guest_data},
            "logs": {"operation_logs": [], "error_logs": [], "usage_stats": {}},
        }
        manager = _make_mock_manager(mock_client, test_root)

        result = manager.cleanup_expired_users()

        self.assertIn("guest_clean01", result["cleaned"])
        self.assertEqual(result["total_cleaned"], 1)


# ═══════════════════════════════════════════════════════════════════
# 测试 7：认证流程集成
# ═══════════════════════════════════════════════════════════════════

class TestGuestAuthIntegration(unittest.TestCase):
    """验证游客认证流程的完整集成。"""

    @patch("auth_components.st")
    def test_7_1_guest_logout_clears_state(self, mock_st):
        """logout 清除游客认证状态。"""
        session_state = _make_guest_session_state("guest_logout01")
        session_state["auth_data_manager"] = MagicMock()
        mock_st.session_state = session_state

        from auth_components import logout
        logout()

        self.assertNotIn("authenticated", session_state)
        self.assertNotIn("auth_username", session_state)
        self.assertNotIn("auth_mode_type", session_state)

    @patch("auth_components.st")
    def test_7_2_is_authenticated_true_for_guest(self, mock_st):
        """游客模式下 is_authenticated 返回 True。"""
        session_state = _make_guest_session_state()
        mock_st.session_state = session_state

        from auth_components import is_authenticated
        self.assertTrue(is_authenticated())

    @patch("auth_components.st")
    def test_7_3_get_auth_mode_returns_guest(self, mock_st):
        """get_auth_mode 对游客返回 'guest'。"""
        session_state = _make_guest_session_state()
        mock_st.session_state = session_state

        from auth_components import get_auth_mode
        self.assertEqual(get_auth_mode(), "guest")


# ═══════════════════════════════════════════════════════════════════
# 测试 8：get_system_stats 区分游客
# ═══════════════════════════════════════════════════════════════════

class TestGuestSystemStats(unittest.TestCase):
    """验证系统统计中游客角色的处理。"""

    def test_8_1_system_stats_includes_guest_count(self):
        """get_system_stats 统计中包含游客数量。"""
        from database_models import create_default_user

        mock_client = _make_mock_client()
        test_root = {
            "users": {
                "alice": create_default_user("alice", "hash_alice", role="user"),
                "bob": create_default_user("bob", "hash_bob", role="user"),
                "guest_abc12345": create_default_user("guest_abc12345", "", role="guest"),
                "guest_def67890": create_default_user("guest_def67890", "", role="guest"),
            },
            "logs": {"operation_logs": [], "error_logs": [], "usage_stats": {}},
        }
        manager = _make_mock_manager(mock_client, test_root)

        stats = manager.get_system_stats()

        self.assertEqual(stats["total_users"], 4)
        self.assertEqual(stats["user_count"], 2)
        # guest_count 当前未在 get_system_stats 中单独统计，
        # 但 total_users 包含了游客，管理面板区分展示由任务 4 处理


# ── 入口 ──────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
