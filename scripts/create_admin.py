#!/usr/bin/env python3
"""管理员用户创建脚本

创建第一个管理员用户，解决当前无法创建管理员的问题。

用法:
    # 交互式输入（推荐，密码不会在终端显示）
    python scripts/create_admin.py

    # 命令行参数
    python scripts/create_admin.py --username myadmin --password MyPass123

    # 混合模式（只提供用户名，密码交互式输入）
    python scripts/create_admin.py --username myadmin

技术细节:
    - 使用 UserDataManager(current_user=None) 创建系统级操作实例，绕过管理员权限检查
    - 密码使用 bcrypt 哈希处理（auth_logic.hash_password）
    - 用户名验证规则：3-20 位字母、数字或下划线，不可使用保留名称
    - 密码验证规则：至少 8 位，需包含大写字母、小写字母、数字中的至少两种
"""

import argparse
import getpass
import logging
import os
import re
import sys
from pathlib import Path

# ── 环境初始化 ────────────────────────────────────────────

# 确保项目根目录在 sys.path 中，以便导入项目模块
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 加载 .env 文件（确保数据库连接配置可用）
try:
    from dotenv import load_dotenv
    _env_path = _PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
    else:
        print(f"[警告] 未找到 .env 文件（{_env_path}），将使用系统环境变量")
except ImportError:
    print("[警告] python-dotenv 未安装，将使用系统环境变量")

# ── 项目模块导入（需在 .env 加载之后） ────────────────────

from auth_logic import hash_password
from user_data_manager import (
    UserDataManager,
    UserExistsError,
    DataOperationError,
    StorageLimitExceededError,
)
from database_models import (
    ValidationError,
    USERNAME_PATTERN,
    RESERVED_USERNAMES,
)

# ── 日志配置 ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("create_admin")

# ── 常量 ──────────────────────────────────────────────────

USERNAME_MIN = 3
USERNAME_MAX = 20
PASSWORD_MIN = 8


# ── 验证函数 ──────────────────────────────────────────────

def validate_username(username: str) -> str | None:
    """验证用户名格式，返回错误信息或 None（通过验证）。

    规则：
    - 3-20 位字符
    - 仅包含字母、数字和下划线
    - 不能使用系统保留名称
    """
    if not username or not username.strip():
        return "用户名不能为空"

    username = username.strip()

    if len(username) < USERNAME_MIN:
        return f"用户名至少 {USERNAME_MIN} 个字符，当前 {len(username)} 个字符"

    if len(username) > USERNAME_MAX:
        return f"用户名最多 {USERNAME_MAX} 个字符，当前 {len(username)} 个字符"

    if not USERNAME_PATTERN.match(username):
        return "用户名只能包含字母、数字和下划线（a-z, A-Z, 0-9, _）"

    if username.lower() in RESERVED_USERNAMES:
        return f"用户名 '{username}' 为系统保留名称，请选择其他名称"

    return None


def validate_password(password: str) -> str | None:
    """验证密码强度，返回错误信息或 None（通过验证）。

    规则：
    - 至少 8 位字符
    - 需包含大写字母、小写字母、数字中的至少两种
    """
    if not password:
        return "密码不能为空"

    if len(password) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 个字符，当前 {len(password)} 个字符"

    # 复杂度要求：至少包含大写字母、小写字母、数字中的两种
    categories = 0
    if re.search(r"[A-Z]", password):
        categories += 1
    if re.search(r"[a-z]", password):
        categories += 1
    if re.search(r"[0-9]", password):
        categories += 1

    if categories < 2:
        return "密码需包含大写字母、小写字母、数字中的至少两种"

    return None


# ── 交互式输入 ────────────────────────────────────────────

def prompt_username() -> str:
    """交互式获取并验证用户名。"""
    while True:
        username = input("请输入管理员用户名: ").strip()
        error = validate_username(username)
        if error is None:
            return username
        print(f"  [错误] {error}")


def prompt_password() -> str:
    """交互式获取并验证密码（输入时不显示）。"""
    while True:
        password = getpass.getpass("请输入管理员密码: ")
        error = validate_password(password)
        if error is not None:
            print(f"  [错误] {error}")
            continue

        confirm = getpass.getpass("请再次输入密码确认: ")
        if password != confirm:
            print("  [错误] 两次密码不一致，请重新输入")
            continue

        return password


# ── 核心逻辑 ──────────────────────────────────────────────

def create_admin_user(username: str, password: str) -> dict:
    """创建管理员用户。

    Args:
        username: 用户名（已验证）
        password: 明文密码（已验证）

    Returns:
        创建的用户数据字典

    Raises:
        UserExistsError: 用户名已存在
        DataOperationError: 数据操作失败
        StorageLimitExceededError: 存储空间不足
        ValidationError: 数据校验失败
    """
    # 密码 bcrypt 哈希
    password_hash = hash_password(password)

    # 使用 current_user=None 创建系统级操作实例，绕过管理员权限检查
    manager = UserDataManager(current_user=None)

    try:
        user_data = manager.create_user(
            username=username,
            password_hash=password_hash,
            role="admin",
        )
        return user_data
    finally:
        manager.close()


# ── 主入口 ────────────────────────────────────────────────

def main() -> int:
    """主函数，返回退出码（0 成功，1 失败）。"""
    parser = argparse.ArgumentParser(
        description="创建管理员用户",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python scripts/create_admin.py\n"
            "  python scripts/create_admin.py --username myadmin\n"
            "  python scripts/create_admin.py --username myadmin --password MyPass123\n"
        ),
    )
    parser.add_argument(
        "--username", "-u",
        help="管理员用户名（3-20 位字母、数字或下划线）",
    )
    parser.add_argument(
        "--password", "-p",
        help="管理员密码（至少 8 位，需包含大小写字母/数字中的至少两种）",
    )

    args = parser.parse_args()

    print("=" * 50)
    print("  医学教材知识库 — 管理员用户创建工具")
    print("=" * 50)
    print()

    # 获取用户名
    if args.username:
        username = args.username.strip()
        error = validate_username(username)
        if error:
            print(f"[错误] {error}")
            return 1
    else:
        username = prompt_username()

    # 获取密码
    if args.password:
        password = args.password
        error = validate_password(password)
        if error:
            print(f"[错误] {error}")
            return 1
    else:
        password = prompt_password()

    # 确认创建
    print()
    print(f"即将创建管理员用户: {username}")
    if not args.username and not args.password:
        confirm = input("确认创建？(y/N): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("已取消创建。")
            return 0

    # 执行创建
    print()
    try:
        user_data = create_admin_user(username, password)
        profile = user_data.get("profile", {})
        print("[成功] 管理员用户创建成功！")
        print(f"  用户名: {profile.get('username', username)}")
        print(f"  角色:   {profile.get('role', 'admin')}")
        print(f"  创建时间: {profile.get('created_at', 'N/A')}")
        print()
        print("请使用该账号登录应用，即可看到管理员徽章和管理面板入口。")
        return 0

    except UserExistsError:
        print(f"[错误] 用户名 '{username}' 已存在，请选择其他用户名。")
        return 1

    except DataOperationError as exc:
        print(f"[错误] 创建失败: {exc}")
        print("请检查网络连接和数据库配置后重试。")
        return 1

    except StorageLimitExceededError:
        print("[错误] 存储空间不足，无法创建用户。")
        return 1

    except ValidationError as exc:
        print(f"[错误] 数据校验失败: {exc}")
        return 1

    except Exception as exc:
        logger.error("创建管理员用户时发生未预期错误: %s", exc, exc_info=True)
        print(f"[错误] 系统异常: {exc}")
        print("请检查日志获取详细信息。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
