"""医学教材知识库 - 管理员权限区分测试

验证管理员与普通用户的权限边界在数据层正确且不可被绕过：
1. update_user 角色不可变性守卫（防止借全量更新提权为 admin）
2. update_user 保留原角色时正常工作（不破坏密码迁移/状态持久化路径）
3. create_user 特权校验：非管理员上下文不可创建 admin；系统级/管理员上下文可创建
4. register_user 始终以 role="user" 注册（注册路径无提权可能）

用法：
    python test_admin_permissions.py
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

_TEST_HASH_1 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
_TEST_HASH_2 = "60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752"


def _make_manager_with_context(test_root, current_user=None):
    """创建共享同一模拟存储、指定操作上下文(current_user)的 UserDataManager。

    多个 manager 共用同一 mock_client，从而共享同一份 test_root 状态，
    用于模拟“系统级操作 vs 普通用户上下文 vs 管理员上下文”。
    """
    from user_data_manager import UserDataManager

    mock_client = MagicMock()
    mock_client.get_record.return_value = test_root

    def mock_update(record):
        # 全量更新：以新 root 替换当前存储（与真实 JSONBin 行为一致）
        # 注意：get_record 返回的是同一对象引用，原地修改即可反映，这里同步引用以防替换场景
        mock_client.get_record.return_value = record

    mock_client.update_record.side_effect = mock_update
    return UserDataManager(client=mock_client, auto_init=False, current_user=current_user)


class TestUpdateUserRoleImmutability(unittest.TestCase):
    """update_user 角色不可变性守卫测试。"""

    def setUp(self):
        from database_models import create_default_root

        self.test_root = create_default_root()
        self.manager = _make_manager_with_context(self.test_root, current_user=None)
        self.manager.create_user("alice", _TEST_HASH_1)  # 普通用户

    def test_escalation_to_admin_is_blocked(self):
        """通过 update_user 把普通用户提权为 admin 应被拒绝。"""
        from user_data_manager import DataOperationError

        user_data = self.manager.get_user_readonly("alice")
        user_data["profile"]["role"] = "admin"
        with self.assertRaises(DataOperationError) as ctx:
            self.manager.update_user("alice", user_data)
        self.assertIn("角色", str(ctx.exception))

    def test_demotion_from_admin_is_blocked(self):
        """通过 update_user 把 admin 降级也应走受控路径（同样被拒绝）。"""
        from user_data_manager import DataOperationError

        # 系统级创建一个管理员
        self.manager.create_user("theadmin", _TEST_HASH_2, role="admin")
        admin_data = self.manager.get_user_readonly("theadmin")
        admin_data["profile"]["role"] = "user"
        with self.assertRaises(DataOperationError):
            self.manager.update_user("theadmin", admin_data)

    def test_unchanged_role_is_allowed(self):
        """保留原角色的更新应正常通过（回归：密码迁移/状态持久化路径）。"""
        # 模拟 auth_logic 登录迁移：展开原 profile 仅替换 password_hash，role 保持不变
        user_data = self.manager.get_user_readonly("alice")
        new_profile = {**user_data["profile"], "password_hash": _TEST_HASH_2}
        user_data["profile"] = new_profile
        updated = self.manager.update_user("alice", user_data)
        self.assertEqual(updated["profile"]["role"], "user")
        self.assertEqual(updated["profile"]["password_hash"], _TEST_HASH_2)

    def test_extra_data_update_preserves_role(self):
        """模拟 config._persist_model_authed：仅改 extra_data，role 不变，应通过。"""
        user_data = self.manager.get_user_readonly("alice")
        extra = user_data.get("extra_data", {})
        extra["model_authed"] = True
        user_data["extra_data"] = extra
        updated = self.manager.update_user("alice", user_data)
        self.assertEqual(updated["profile"]["role"], "user")
        self.assertTrue(updated["extra_data"]["model_authed"])

    def test_update_without_role_key_is_allowed(self):
        """更新数据未显式携带 role 键时不应误拦。"""
        user_data = self.manager.get_user_readonly("alice")
        # 构造一个不含 role 的 profile（仅用户名+密码哈希）
        user_data["profile"] = {
            "username": "alice",
            "password_hash": _TEST_HASH_1,
        }
        # 不应抛出角色相关异常（校验由 UserData.validate 负责）
        try:
            self.manager.update_user("alice", user_data)
        except Exception as exc:  # 仅角色守卫不应触发
            self.assertNotIn("角色", str(exc))


class TestCreateUserPrivilege(unittest.TestCase):
    """create_user 特权角色创建的权限校验测试。"""

    def setUp(self):
        from database_models import create_default_root

        self.test_root = create_default_root()
        # 系统级 manager：先建好普通用户与管理员
        self.system_manager = _make_manager_with_context(self.test_root, current_user=None)
        self.system_manager.create_user("regular", _TEST_HASH_1)  # 普通用户
        self.system_manager.create_user("boss", _TEST_HASH_2, role="admin")  # 管理员

    def test_system_context_can_create_admin(self):
        """系统级上下文(current_user=None)可创建管理员（脚本场景）。"""
        user_data = self.system_manager.create_user("admin2", _TEST_HASH_1, role="admin")
        self.assertEqual(user_data["profile"]["role"], "admin")

    def test_non_admin_context_cannot_create_admin(self):
        """非管理员上下文创建 admin 应被拒绝。"""
        from user_data_manager import DataOperationError

        regular_mgr = _make_manager_with_context(self.test_root, current_user="regular")
        with self.assertRaises(DataOperationError) as ctx:
            regular_mgr.create_user("sneaky", _TEST_HASH_1, role="admin")
        self.assertIn("权限", str(ctx.exception))

    def test_nonexistent_current_user_cannot_create_admin(self):
        """current_user 指向不存在用户时创建 admin 应被拒绝。"""
        from user_data_manager import DataOperationError

        ghost_mgr = _make_manager_with_context(self.test_root, current_user="ghost_user")
        with self.assertRaises(DataOperationError):
            ghost_mgr.create_user("sneaky2", _TEST_HASH_1, role="admin")

    def test_admin_context_can_create_admin(self):
        """管理员上下文可创建管理员。"""
        boss_mgr = _make_manager_with_context(self.test_root, current_user="boss")
        user_data = boss_mgr.create_user("admin3", _TEST_HASH_1, role="admin")
        self.assertEqual(user_data["profile"]["role"], "admin")

    def test_non_admin_context_can_create_normal_user(self):
        """非管理员上下文创建普通用户应正常（不受特权限制）。"""
        regular_mgr = _make_manager_with_context(self.test_root, current_user="regular")
        user_data = regular_mgr.create_user("normalguy", _TEST_HASH_1, role="user")
        self.assertEqual(user_data["profile"]["role"], "user")


class TestRegistrationRoleHardcoded(unittest.TestCase):
    """注册路径始终以 role="user" 创建，无提权可能。"""

    def test_register_user_creates_user_role(self):
        from auth_logic import register_user

        from database_models import create_default_root
        test_root = create_default_root()
        manager = _make_manager_with_context(test_root, current_user=None)

        user_data, error = register_user("newbie", "StrongPass1", manager)
        self.assertIsNone(error)
        self.assertIsNotNone(user_data)
        self.assertEqual(user_data["profile"]["role"], "user")

    def test_register_user_has_no_role_parameter(self):
        """register_user 签名不接受 role 参数，确保角色由内部硬编码。"""
        import inspect

        from auth_logic import register_user

        params = inspect.signature(register_user).parameters
        self.assertNotIn("role", params, "register_user 不应暴露 role 参数")


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

    print("\n" + "=" * 60)
    print("📊 管理员权限区分测试总结")
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
