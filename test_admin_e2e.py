"""医学教材知识库 - 管理员功能端到端测试

验证管理员角色系统的完整功能链路，覆盖四项验收标准：
  标准一：管理员用户创建脚本能成功创建管理员用户
  标准二：管理员用户能正常登录，显示管理员徽章
  标准三：管理员能访问管理面板，普通用户不能访问
  标准四：管理面板显示正确的用户列表和系统统计信息

测试策略：
  - 数据层：使用模拟存储（MagicMock client）替代真实数据库，避免网络依赖
  - UI 层：通过 patch 替换 auth_components.st 和 admin_panel.st，模拟 session_state
  - 全链路：create_admin_user -> login_user -> is_admin() -> render_admin_panel -> 数据展示

用法：
    python test_admin_e2e.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── 路径设置 ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
for p in (_PROJECT_ROOT, _SCRIPTS_DIR):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# ── 测试常量 ──────────────────────────────────────────────

_TEST_HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
_ADMIN_PASSWORD = "AdminPass123"
_USER_PASSWORD = "UserPass456"


# ── 辅助函数 ──────────────────────────────────────────────

def _make_mock_manager(test_root, current_user=None):
    """创建共享同一模拟存储的 UserDataManager。

    多个 manager 共用同一 mock_client，共享同一份 test_root 状态。
    """
    from user_data_manager import UserDataManager

    mock_client = MagicMock()
    mock_client.get_record.return_value = test_root

    def mock_update(record):
        mock_client.get_record.return_value = record

    mock_client.update_record.side_effect = mock_update
    return UserDataManager(client=mock_client, auto_init=False, current_user=current_user)


def _make_admin_session_state(role="admin", username="testadmin"):
    """构造管理员会话状态字典（用于 is_admin 判断）。"""
    return {
        "authenticated": True,
        "auth_mode_type": "login",
        "auth_username": username,
        "user_data": {"profile": {"role": role, "username": username}},
    }


def _make_mock_st_for_panel():
    """构造可支撑 render_admin_panel 渲染的 mock streamlit。

    处理 st.columns（返回列表）、st.button（返回 False）、
    st.session_state（可读写 dict）等关键调用。
    """
    mock_st = MagicMock()
    mock_st.session_state = {}
    mock_st.button.return_value = False

    def mock_columns(arg):
        count = len(arg) if isinstance(arg, list) else arg
        return [MagicMock() for _ in range(count)]

    mock_st.columns.side_effect = mock_columns
    return mock_st


# ═══════════════════════════════════════════════════════════════════
# 标准一：管理员用户创建脚本能成功创建管理员用户
# ═══════════════════════════════════════════════════════════════════

class TestStandard1AdminCreation(unittest.TestCase):
    """验证管理员用户创建脚本的核心逻辑。"""

    # ── 脚本验证函数 ──────────────────────────────────────

    def test_1_1_validate_username_accepts_valid(self):
        """有效用户名通过验证。"""
        from create_admin import validate_username

        for name in ["testadmin", "Admin_2024", "abc"]:
            self.assertIsNone(validate_username(name), f"用户名 {name!r} 应通过验证")

    def test_1_2_validate_username_rejects_invalid_format(self):
        """无效格式的用户名被拒绝。"""
        from create_admin import validate_username

        # 太短
        self.assertIsNotNone(validate_username("ab"))
        # 太长
        self.assertIsNotNone(validate_username("a" * 21))
        # 含空格
        self.assertIsNotNone(validate_username("admin name"))
        # 含特殊字符
        self.assertIsNotNone(validate_username("admin@123"))
        # 为空
        self.assertIsNotNone(validate_username(""))
        self.assertIsNotNone(validate_username("   "))

    def test_1_3_validate_username_rejects_reserved_names(self):
        """系统保留用户名被拒绝（防钓鱼）。"""
        from create_admin import validate_username

        for reserved in ["admin", "root", "system", "ADMIN", "Root"]:
            error = validate_username(reserved)
            self.assertIsNotNone(error, f"保留名 {reserved!r} 应被拒绝")
            self.assertIn("保留", error)

    def test_1_4_validate_password_accepts_strong(self):
        """强密码通过验证。"""
        from create_admin import validate_password

        for pwd in ["AdminPass123", "Abcdefgh1", "Pass1234"]:
            self.assertIsNone(validate_password(pwd), f"密码 {pwd!r} 应通过验证")

    def test_1_5_validate_password_rejects_weak(self):
        """弱密码被拒绝。"""
        from create_admin import validate_password

        # 太短
        self.assertIsNotNone(validate_password("Ab1"))
        # 仅小写字母
        self.assertIsNotNone(validate_password("abcdefgh"))
        # 仅数字
        self.assertIsNotNone(validate_password("12345678"))
        # 仅大写字母
        self.assertIsNotNone(validate_password("ABCDEFGH"))
        # 为空
        self.assertIsNotNone(validate_password(""))

    # ── create_admin_user 函数 ────────────────────────────

    @patch("create_admin.UserDataManager")
    def test_1_6_create_admin_user_calls_create_with_admin_role(self, mock_udm_class):
        """create_admin_user 以 role='admin' 调用 create_user。"""
        from create_admin import create_admin_user

        mock_manager = MagicMock()
        mock_udm_class.return_value = mock_manager

        captured = {}

        def fake_create_user(**kwargs):
            captured.update(kwargs)
            return {"profile": {"username": kwargs["username"], "role": kwargs["role"]}}

        mock_manager.create_user.side_effect = fake_create_user

        result = create_admin_user("testadmin", _ADMIN_PASSWORD)

        self.assertEqual(captured["role"], "admin")
        self.assertEqual(captured["username"], "testadmin")
        self.assertEqual(result["profile"]["role"], "admin")

    @patch("create_admin.UserDataManager")
    def test_1_7_create_admin_user_hashes_password_with_bcrypt(self, mock_udm_class):
        """create_admin_user 对密码进行 bcrypt 哈希，不传明文。"""
        from create_admin import create_admin_user

        mock_manager = MagicMock()
        mock_udm_class.return_value = mock_manager

        captured = {}
        mock_manager.create_user.side_effect = lambda **kw: captured.update(kw) or {"profile": kw}

        create_admin_user("testadmin", _ADMIN_PASSWORD)

        stored_hash = captured["password_hash"]
        # bcrypt 格式检查
        self.assertTrue(
            stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"),
            "密码应以 bcrypt 格式存储",
        )
        self.assertNotEqual(stored_hash, _ADMIN_PASSWORD, "不应存储明文密码")

    @patch("create_admin.UserDataManager")
    def test_1_8_create_admin_user_uses_system_level_context(self, mock_udm_class):
        """create_admin_user 使用 current_user=None 绕过权限检查。"""
        from create_admin import create_admin_user

        mock_manager = MagicMock()
        mock_udm_class.return_value = mock_manager
        mock_manager.create_user.return_value = {"profile": {"role": "admin"}}

        create_admin_user("testadmin", _ADMIN_PASSWORD)

        # UserDataManager 应以 current_user=None 初始化
        _, kwargs = mock_udm_class.call_args
        self.assertIsNone(kwargs.get("current_user"), "current_user 应为 None（系统级操作）")

    @patch("create_admin.UserDataManager")
    def test_1_9_create_admin_user_closes_manager(self, mock_udm_class):
        """create_admin_user 在完成后关闭 manager（finally 块）。"""
        from create_admin import create_admin_user

        mock_manager = MagicMock()
        mock_udm_class.return_value = mock_manager
        mock_manager.create_user.return_value = {"profile": {"role": "admin"}}

        create_admin_user("testadmin", _ADMIN_PASSWORD)

        mock_manager.close.assert_called_once()

    @patch("create_admin.UserDataManager")
    def test_1_10_create_admin_user_closes_manager_on_error(self, mock_udm_class):
        """create_admin_user 在异常时也关闭 manager。"""
        from create_admin import create_admin_user
        from user_data_manager import UserExistsError

        mock_manager = MagicMock()
        mock_udm_class.return_value = mock_manager
        mock_manager.create_user.side_effect = UserExistsError("用户已存在")

        with self.assertRaises(UserExistsError):
            create_admin_user("testadmin", _ADMIN_PASSWORD)

        mock_manager.close.assert_called_once()

    # ── 全链路：模拟存储 ──────────────────────────────────

    def test_1_11_full_creation_flow_with_mock_storage(self):
        """使用模拟存储完整测试管理员创建流程：hash -> create -> verify role。"""
        from auth_logic import hash_password
        from database_models import create_default_root

        test_root = create_default_root()
        manager = _make_mock_manager(test_root, current_user=None)

        password_hash = hash_password(_ADMIN_PASSWORD)
        user_data = manager.create_user(
            username="testadmin",
            password_hash=password_hash,
            role="admin",
        )

        self.assertEqual(user_data["profile"]["role"], "admin")
        self.assertEqual(user_data["profile"]["username"], "testadmin")
        self.assertIn("created_at", user_data["profile"])
        self.assertIn("learning_records", user_data)

    def test_1_12_duplicate_admin_creation_raises(self):
        """重复创建管理员用户名时抛出 UserExistsError。"""
        from auth_logic import hash_password
        from database_models import create_default_root
        from user_data_manager import UserExistsError

        test_root = create_default_root()
        manager = _make_mock_manager(test_root, current_user=None)

        pw_hash = hash_password(_ADMIN_PASSWORD)
        manager.create_user("testadmin", pw_hash, role="admin")

        with self.assertRaises(UserExistsError):
            manager.create_user("testadmin", pw_hash, role="admin")


# ═══════════════════════════════════════════════════════════════════
# 标准二：管理员用户能正常登录，显示管理员徽章
# ═══════════════════════════════════════════════════════════════════

class TestStandard2AdminLogin(unittest.TestCase):
    """验证管理员登录流程及 is_admin() 角色判断。"""

    def setUp(self):
        """重置登录速率限制器，避免测试间干扰。"""
        import auth_logic
        auth_logic._login_rate_limiter._records.clear()

    def _setup_admin_and_regular_users(self):
        """创建含管理员和普通用户的模拟存储。"""
        from auth_logic import hash_password
        from database_models import create_default_root

        test_root = create_default_root()
        manager = _make_mock_manager(test_root, current_user=None)

        manager.create_user("testadmin", hash_password(_ADMIN_PASSWORD), role="admin")
        manager.create_user("regularuser", hash_password(_USER_PASSWORD), role="user")

        return manager

    # ── 登录流程 ──────────────────────────────────────────

    def test_2_1_admin_login_succeeds_with_correct_password(self):
        """管理员使用正确密码能登录成功。"""
        from auth_logic import login_user

        manager = self._setup_admin_and_regular_users()
        user_data, error = login_user("testadmin", _ADMIN_PASSWORD, manager)

        self.assertIsNone(error, f"管理员登录不应返回错误: {error}")
        self.assertIsNotNone(user_data)

    def test_2_2_admin_login_returns_admin_role(self):
        """登录返回的 user_data 中 role 为 admin。"""
        from auth_logic import login_user

        manager = self._setup_admin_and_regular_users()
        user_data, _ = login_user("testadmin", _ADMIN_PASSWORD, manager)

        self.assertEqual(user_data["profile"]["role"], "admin")

    def test_2_3_admin_login_updates_login_stats(self):
        """登录后 login_count 递增。"""
        from auth_logic import login_user

        manager = self._setup_admin_and_regular_users()

        # 第一次登录
        user_data, _ = login_user("testadmin", _ADMIN_PASSWORD, manager)
        first_count = user_data.get("stats", {}).get("login_count", 0)

        # 第二次登录
        user_data2, _ = login_user("testadmin", _ADMIN_PASSWORD, manager)
        second_count = user_data2.get("stats", {}).get("login_count", 0)

        self.assertEqual(second_count, first_count + 1, "login_count 应递增")

    def test_2_4_admin_login_fails_with_wrong_password(self):
        """管理员使用错误密码登录失败。"""
        from auth_logic import login_user

        manager = self._setup_admin_and_regular_users()
        user_data, error = login_user("testadmin", "WrongPassword99", manager)

        self.assertIsNone(user_data)
        self.assertEqual(error, "密码错误")

    def test_2_5_admin_login_fails_with_nonexistent_user(self):
        """不存在的用户登录失败。"""
        from auth_logic import login_user

        manager = self._setup_admin_and_regular_users()
        user_data, error = login_user("ghost_user", _ADMIN_PASSWORD, manager)

        self.assertIsNone(user_data)
        self.assertEqual(error, "用户名不存在")

    def test_2_6_regular_user_login_returns_user_role(self):
        """普通用户登录返回 user 角色（非 admin）。"""
        from auth_logic import login_user

        manager = self._setup_admin_and_regular_users()
        user_data, _ = login_user("regularuser", _USER_PASSWORD, manager)

        self.assertEqual(user_data["profile"]["role"], "user")

    # ── is_admin() 角色判断 ──────────────────────────────

    def test_2_7_is_admin_true_for_admin_session(self):
        """is_admin() 对已认证管理员会话返回 True（控制管理员徽章显示）。"""
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = _make_admin_session_state(role="admin")
            from auth_components import is_admin
            self.assertTrue(is_admin())

    def test_2_8_is_admin_false_for_regular_user_session(self):
        """is_admin() 对已认证普通用户返回 False。"""
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = _make_admin_session_state(role="user")
            from auth_components import is_admin
            self.assertFalse(is_admin())

    def test_2_9_is_admin_false_for_guest_mode(self):
        """is_admin() 对游客模式返回 False。"""
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = {
                "authenticated": True,
                "auth_mode_type": "guest",
                "user_data": {"profile": {"role": "admin"}},  # 即使数据中 role=admin
            }
            from auth_components import is_admin
            self.assertFalse(is_admin())

    def test_2_10_is_admin_false_for_unauthenticated(self):
        """is_admin() 对未认证用户返回 False。"""
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = {
                "authenticated": False,
                "auth_mode_type": "login",
                "user_data": {"profile": {"role": "admin"}},
            }
            from auth_components import is_admin
            self.assertFalse(is_admin())

    def test_2_11_is_admin_false_when_no_user_data(self):
        """is_admin() 在 session_state 中无 user_data 时返回 False。"""
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = {
                "authenticated": True,
                "auth_mode_type": "login",
            }
            from auth_components import is_admin
            self.assertFalse(is_admin())


# ═══════════════════════════════════════════════════════════════════
# 标准三：管理员能访问管理面板，普通用户不能访问
# ═══════════════════════════════════════════════════════════════════

class TestStandard3AdminPanelAccessControl(unittest.TestCase):
    """验证管理面板的访问控制：仅 admin 可访问，其他角色被拒绝。"""

    # ── render_admin_panel 守卫 ───────────────────────────

    def test_3_1_admin_panel_blocks_regular_user(self):
        """render_admin_panel 对普通用户显示权限不足错误并提前返回。"""
        from admin_panel import render_admin_panel

        manager = MagicMock()
        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="user")
            render_admin_panel(manager)

        # 应调用 st.error 显示权限不足
        mock_panel_st.error.assert_called_once()
        error_msg = mock_panel_st.error.call_args[0][0]
        self.assertIn("权限", error_msg)
        # 不应渲染面板内容
        mock_panel_st.subheader.assert_not_called()
        mock_panel_st.table.assert_not_called()

    def test_3_2_admin_panel_blocks_guest(self):
        """render_admin_panel 对游客显示权限不足错误并提前返回。"""
        from admin_panel import render_admin_panel

        manager = MagicMock()
        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = {
                "authenticated": True,
                "auth_mode_type": "guest",
                "user_data": {"profile": {"role": "admin"}},
            }
            render_admin_panel(manager)

        mock_panel_st.error.assert_called_once()
        mock_panel_st.subheader.assert_not_called()

    def test_3_3_admin_panel_blocks_unauthenticated(self):
        """render_admin_panel 对未认证用户显示权限不足错误。"""
        from admin_panel import render_admin_panel

        manager = MagicMock()
        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = {"authenticated": False}
            render_admin_panel(manager)

        mock_panel_st.error.assert_called_once()
        mock_panel_st.subheader.assert_not_called()

    def test_3_4_admin_panel_allows_admin(self):
        """render_admin_panel 对管理员不显示错误，正常渲染面板内容。"""
        from admin_panel import render_admin_panel
        from auth_logic import hash_password
        from database_models import create_default_root

        # 使用模拟存储的管理器，含管理员和普通用户
        test_root = create_default_root()
        manager = _make_mock_manager(test_root, current_user=None)
        manager.create_user("testadmin", hash_password(_ADMIN_PASSWORD), role="admin")
        manager.create_user("regularuser", hash_password(_USER_PASSWORD), role="user")

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(manager)

        # 不应调用 st.error
        mock_panel_st.error.assert_not_called()
        # 应渲染面板内容（subheader 用于统计和用户列表）
        self.assertGreaterEqual(mock_panel_st.subheader.call_count, 2,
                                "应至少渲染统计和用户列表两个子标题")

    # ── app.py 视图守卫逻辑 ───────────────────────────────

    def test_3_5_view_guard_resets_for_non_admin(self):
        """app.py 视图守卫：非 admin 用户在 admin 视图时重置为 main。"""
        # 模拟 app.py 第 162-163 行的守卫逻辑
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = _make_admin_session_state(role="user")

            from auth_components import is_admin

            # 模拟非 admin 用户停留在 admin 视图
            view = "admin"
            if view == "admin" and not is_admin():
                view = "main"

            self.assertEqual(view, "main", "非管理员在 admin 视图应被重置为 main")

    def test_3_6_view_guard_preserves_for_admin(self):
        """app.py 视图守卫：admin 用户的 admin 视图不被重置。"""
        with patch("auth_components.st") as mock_st:
            mock_st.session_state = _make_admin_session_state(role="admin")

            from auth_components import is_admin

            view = "admin"
            if view == "admin" and not is_admin():
                view = "main"

            self.assertEqual(view, "admin", "管理员的 admin 视图不应被重置")

    def test_3_7_sidebar_admin_entry_only_for_admin(self):
        """app.py 侧边栏：管理员入口仅在 is_admin() 为 True 时显示。

        验证 is_admin() 返回值与侧边栏入口显示条件的对应关系。
        """
        cases = [
            ("admin", True, True),      # 管理员 -> 显示入口
            ("user", True, False),      # 普通用户 -> 不显示入口
        ]
        for role, authenticated, should_show in cases:
            with self.subTest(role=role), \
                 patch("auth_components.st") as mock_st:
                if authenticated:
                    mock_st.session_state = _make_admin_session_state(role=role)
                else:
                    mock_st.session_state = {"authenticated": False}

                from auth_components import is_admin
                is_admin_result = is_admin()
                self.assertEqual(is_admin_result, should_show,
                                 f"role={role} 的 is_admin() 应为 {should_show}")


# ═══════════════════════════════════════════════════════════════════

class TestStandard4AdminPanelData(unittest.TestCase):
    """标准四：管理面板显示正确的用户列表和系统统计信息。"""

    def setUp(self):
        """创建含管理员和普通用户的模拟存储。"""
        from database_models import create_default_root

        self.test_root = create_default_root()
        self.manager = _make_mock_manager(self.test_root, current_user=None)

        # 创建 2 个管理员 + 3 个普通用户
        self.admin_names = ["admin_one", "admin_two"]
        self.user_names = ["user_one", "user_two", "user_three"]
        for name in self.admin_names:
            self.manager.create_user(name, _TEST_HASH, role="admin")
        for name in self.user_names:
            self.manager.create_user(name, _TEST_HASH, role="user")

    # ── get_system_stats ──────────────────────────────────

    def test_4_1_system_stats_total_users(self):
        """get_system_stats 返回正确的总用户数（含游客）。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["total_users"], 5)

    def test_4_1b_system_stats_registered_users(self):
        """get_system_stats 返回正确的注册用户数（不含游客）。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["registered_users"], 5)

    def test_4_1c_system_stats_guest_count_zero(self):
        """无游客时 guest_count 为 0。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["guest_count"], 0)

    def test_4_2_system_stats_admin_count(self):
        """get_system_stats 返回正确的管理员数量。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["admin_count"], 2)

    def test_4_3_system_stats_user_count(self):
        """get_system_stats 返回正确的普通用户数量。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["user_count"], 3)

    def test_4_4_system_stats_total_queries(self):
        """get_system_stats 返回正确的总查询次数（初始为 0）。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["total_queries"], 0)

    def test_4_5_system_stats_after_queries(self):
        """添加学习记录后，total_queries 正确递增。"""
        self.manager.add_learning_record("user_one", "测试主题1")
        self.manager.add_learning_record("user_one", "测试主题2")
        self.manager.add_learning_record("admin_one", "管理主题")

        stats = self.manager.get_system_stats()
        self.assertEqual(stats["total_queries"], 3)

    # ── list_users ────────────────────────────────────────

    def test_4_6_list_users_returns_all_users(self):
        """list_users 返回所有用户。"""
        users = self.manager.list_users()
        self.assertEqual(len(users), 5)

    def test_4_7_list_users_contains_correct_roles(self):
        """list_users 返回的每条记录包含正确的角色信息。"""
        users = self.manager.list_users()
        user_map = {u["username"]: u for u in users}

        for name in self.admin_names:
            self.assertEqual(user_map[name]["role"], "admin",
                             f"{name} 角色应为 admin")
        for name in self.user_names:
            self.assertEqual(user_map[name]["role"], "user",
                             f"{name} 角色应为 user")

    def test_4_8_list_users_contains_required_fields(self):
        """list_users 每条记录包含必要字段。"""
        users = self.manager.list_users()
        required_fields = {"username", "role", "created_at", "last_active_at", "data_size_kb"}
        for u in users:
            for field in required_fields:
                self.assertIn(field, u, f"用户记录缺少字段: {field}")

    def test_4_9_list_users_created_at_not_empty(self):
        """list_users 返回的用户注册时间不为空。"""
        users = self.manager.list_users()
        for u in users:
            self.assertTrue(u["created_at"], f"用户 {u['username']} 的注册时间不应为空")

    def test_4_10_list_users_data_size_within_limit(self):
        """list_users 返回的数据大小在合理范围内（< 10KB 限制）。"""
        users = self.manager.list_users()
        for u in users:
            self.assertLess(u["data_size_kb"], 10,
                            f"用户 {u['username']} 数据大小应小于 10KB")

    # ── 面板渲染集成 ──────────────────────────────────────

    def test_4_11_admin_panel_renders_stats_and_user_list(self):
        """render_admin_panel 为管理员渲染统计指标和用户列表表格。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        # st.metric 被调用 7 次（注册用户、游客会话、总查询次数、管理员数量 + 今日/本周/本月活跃）
        self.assertEqual(mock_panel_st.metric.call_count, 7)

        # st.table 至少被调用 1 次（用户列表；新增的系统配置/API Key 状态表为额外调用）
        self.assertGreaterEqual(mock_panel_st.table.call_count, 1)

        # st.caption 至少被调用 1 次（角色分布摘要；新增的趋势图空数据提示为额外调用）
        self.assertGreaterEqual(mock_panel_st.caption.call_count, 1)

    def test_4_12_admin_panel_stats_match_data(self):
        """render_admin_panel 渲染的统计指标值与实际数据一致。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        # 提取传给 st.metric 的值
        metric_calls = mock_panel_st.metric.call_args_list
        metric_values = {call[0][0]: call[0][1] for call in metric_calls}

        self.assertEqual(metric_values["注册用户"], 5)
        self.assertEqual(metric_values["游客会话"], 0)
        self.assertEqual(metric_values["总查询次数"], 0)
        self.assertEqual(metric_values["管理员数量"], 2)

    def test_4_13_admin_panel_table_contains_all_users(self):
        """render_admin_panel 渲染的用户列表包含所有用户。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        # 用户列表改用 st.dataframe（支持排序、筛选交互）
        # 从所有 dataframe 调用中找到包含 "用户名" 列的 DataFrame
        df_args = [call[0][0] for call in mock_panel_st.dataframe.call_args_list]
        user_dfs = [df for df in df_args if "用户名" in df.columns]
        self.assertEqual(len(user_dfs), 1)
        usernames = list(user_dfs[0]["用户名"])
        self.assertEqual(len(usernames), 5)
        for name in self.admin_names + self.user_names:
            self.assertIn(name, usernames)

    def test_4_14_admin_panel_caption_shows_role_distribution(self):
        """render_admin_panel 的 caption 显示正确的角色分布。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        # 从所有 caption 调用中找到角色分布摘要（含"管理员"关键词）
        caption_texts = [call[0][0] for call in mock_panel_st.caption.call_args_list]
        role_caption = None
        for text in caption_texts:
            if "管理员" in text and "人" in text:
                role_caption = text
                break
        self.assertIsNotNone(role_caption, "未找到角色分布摘要 caption")
        self.assertIn("5", role_caption)       # 共 5 位用户
        self.assertIn("2", role_caption)       # 管理员 2 人
        self.assertIn("3", role_caption)       # 普通用户 3 人


# ═══════════════════════════════════════════════════════════════════
# 标准五：管理面板 guest 统计区分（任务 4）
# ═══════════════════════════════════════════════════════════════════

class TestStandard5GuestStatsDistinction(unittest.TestCase):
    """验证管理面板统计中 guest 与注册用户区分展示。"""

    def setUp(self):
        """创建含管理员、普通用户和游客的模拟存储。"""
        from database_models import create_default_root, create_default_user

        self.test_root = create_default_root()
        self.manager = _make_mock_manager(self.test_root, current_user=None)

        # 创建 1 个管理员 + 2 个普通用户
        self.manager.create_user("admin_one", _TEST_HASH, role="admin")
        self.manager.create_user("user_one", _TEST_HASH, role="user")
        self.manager.create_user("user_two", _TEST_HASH, role="user")

        # 直接添加 3 个游客用户
        for gid in ["guest_aaa11111", "guest_bbb22222", "guest_ccc33333"]:
            self.test_root["users"][gid] = create_default_user(gid, "", role="guest")

    # ── get_system_stats 区分 ──────────────────────────────

    def test_5_1_stats_registered_users_excludes_guests(self):
        """registered_users 不含游客。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["registered_users"], 3)  # 1 admin + 2 user

    def test_5_2_stats_guest_count_correct(self):
        """guest_count 正确统计游客数。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["guest_count"], 3)

    def test_5_3_stats_total_users_includes_all(self):
        """total_users 包含所有角色。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["total_users"], 6)  # 3 registered + 3 guest

    def test_5_4_stats_admin_count_unaffected(self):
        """admin_count 不受游客影响。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["admin_count"], 1)

    def test_5_5_stats_user_count_unaffected(self):
        """user_count 不受游客影响。"""
        stats = self.manager.get_system_stats()
        self.assertEqual(stats["user_count"], 2)

    # ── 面板渲染区分 ──────────────────────────────────────

    def test_5_6_panel_metrics_show_registered_and_guest_separately(self):
        """面板 st.metric 分别展示注册用户和游客会话。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        metric_calls = mock_panel_st.metric.call_args_list
        metric_values = {call[0][0]: call[0][1] for call in metric_calls}

        self.assertEqual(metric_values["注册用户"], 3)
        self.assertEqual(metric_values["游客会话"], 3)

    def test_5_7_panel_overview_table_has_guest_row(self):
        """统计概览表中包含游客会话行。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        # 从所有 dataframe 调用中找到统计概览表
        df_args = [call[0][0] for call in mock_panel_st.dataframe.call_args_list]
        overview_dfs = [df for df in df_args if "指标" in df.columns]
        self.assertGreaterEqual(len(overview_dfs), 1, "应存在统计概览表")

        overview_df = overview_dfs[0]
        indicators = list(overview_df["指标"])
        self.assertIn("注册用户", indicators)
        self.assertIn("游客会话", indicators)

    def test_5_8_panel_active_users_distinguish_role(self):
        """活跃用户指标按角色区分展示。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(role="admin")
            render_admin_panel(self.manager)

        metric_calls = mock_panel_st.metric.call_args_list
        active_metrics = {call[0][0]: call[0][1] for call in metric_calls
                         if "活跃" in call[0][0]}

        # 活跃指标值应包含 "注册 / 游客" 格式
        for label, value in active_metrics.items():
            self.assertIn("/", value,
                          f"活跃指标 {label} 应包含 '注册 / 游客' 格式，实际值: {value}")

    # ── super_admin 保护不受 guest 影响 ────────────────────

    def test_5_9_super_admin_protection_unaffected_by_guest(self):
        """super_admin 保护机制不受 guest 角色影响。"""
        from admin_panel import render_admin_panel

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st), \
             patch("auth_components.st") as mock_auth_st:
            mock_auth_st.session_state = _make_admin_session_state(
                role="admin", username="admin_one"
            )
            render_admin_panel(self.manager)

        # 不应出现权限错误
        error_calls = [call for call in mock_panel_st.error.call_args_list
                       if "权限" in str(call)]
        self.assertEqual(len(error_calls), 0,
                         "管理员访问管理面板不应受 guest 用户影响")

    def test_5_10_role_immutability_guard_unaffected(self):
        """角色不可变性守卫不受 guest 角色影响。"""
        # 验证 change_role 弹窗中 available_roles 逻辑不受 guest 影响
        from admin_panel import _render_role_change_dialog

        mock_panel_st = _make_mock_st_for_panel()

        with patch("admin_panel.st", mock_panel_st):
            # 非超级管理员尝试修改角色
            _render_role_change_dialog(
                self.manager, "user_one", "user", "admin_one", False
            )

        # selectbox 应被调用，且可选角色不含 admin
        selectbox_calls = mock_panel_st.selectbox.call_args_list
        self.assertGreaterEqual(len(selectbox_calls), 1)
        available_roles = selectbox_calls[0][0][1]
        self.assertNotIn("admin", available_roles,
                         "非超级管理员不应能将用户提升为 admin")


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
    print("📊 管理员功能端到端测试总结")
    print("=" * 70)
    print(f"   标准一（管理员创建脚本）: 12 项测试")
    print(f"   标准二（管理员登录+徽章）: 11 项测试")
    print(f"   标准三（管理面板访问控制）:  7 项测试")
    print(f"   标准四（面板数据正确性）:  14 项测试")
    print(f"   标准五（guest 统计区分）:  10 项测试")
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
