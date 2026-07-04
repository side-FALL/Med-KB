"""医学教材知识库 — 用户认证逻辑

实现用户注册、登录的核心业务逻辑，包含：
- 密码 bcrypt 哈希处理
- 用户名唯一性检查
- 数据初始化
- 自动登录

依赖：user_data_manager.py
"""

import hashlib
import hmac
import logging
import time
from typing import Optional, Tuple

import bcrypt

from user_data_manager import (
    UserDataManager,
    UserExistsError,
    UserNotFoundError,
    DataOperationError,
    StorageLimitExceededError,
)
from database_models import ValidationError

logger = logging.getLogger(__name__)


# ── 登录速率限制 ──────────────────────────────────────────

class _LoginRateLimiter:
    """基于用户名的登录速率限制器（内存级）。

    双重防护：
    - 滑动窗口：每分钟最多 max_attempts_per_min 次失败尝试
    - 账户锁定：连续 max_consecutive_failures 次失败后锁定 lockout_seconds 秒
    """

    def __init__(self, max_attempts_per_min: int = 5, max_consecutive_failures: int = 5, lockout_seconds: int = 900):
        self._max_attempts = max_attempts_per_min
        self._max_failures = max_consecutive_failures
        self._lockout_seconds = lockout_seconds
        # {username: {"timestamps": [...], "failures": int, "locked_until": float}}
        self._records: dict = {}

    def is_limited(self, username: str) -> bool:
        """检查该用户名是否被限流或锁定。"""
        now = time.time()
        rec = self._records.get(username)
        if not rec:
            return False
        # 账户锁定检查
        if rec["locked_until"] and now < rec["locked_until"]:
            return True
        # 滑动窗口：清除超过 1 分钟的旧记录
        cutoff = now - 60
        rec["timestamps"] = [t for t in rec["timestamps"] if t > cutoff]
        return len(rec["timestamps"]) >= self._max_attempts

    def lockout_remaining(self, username: str) -> int:
        """返回锁定剩余秒数（0 表示未锁定）。"""
        rec = self._records.get(username)
        if not rec or not rec["locked_until"]:
            return 0
        remaining = rec["locked_until"] - time.time()
        return max(0, int(remaining))

    def record_failure(self, username: str) -> None:
        """记录一次失败登录，必要时触发锁定。"""
        now = time.time()
        if username not in self._records:
            self._records[username] = {"timestamps": [], "failures": 0, "locked_until": 0}
        rec = self._records[username]
        rec["timestamps"].append(now)
        rec["failures"] += 1
        if rec["failures"] >= self._max_failures:
            rec["locked_until"] = now + self._lockout_seconds
            rec["failures"] = 0  # 重置计数器，锁定结束后重新累计
            logger.warning("用户 %s 因连续 %d 次登录失败被锁定 %d 秒", username, self._max_failures, self._lockout_seconds)

    def record_success(self, username: str) -> None:
        """登录成功后清除限流记录。"""
        self._records.pop(username, None)


_login_rate_limiter = _LoginRateLimiter()


# ── 密码哈希 ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    """使用 bcrypt 对密码进行哈希处理。

    Args:
        password: 明文密码

    Returns:
        bcrypt 哈希字符串（包含盐值）

    Raises:
        ValueError: 密码为空
    """
    if not password:
        raise ValueError("密码不能为空")

    # bcrypt 要求密码编码为 bytes，使用 UTF-8
    password_bytes = password.encode("utf-8")

    # 生成盐值并进行哈希（bcrypt 自动处理盐值）
    salt = bcrypt.gensalt(rounds=12)  # rounds=12 是安全与性能的平衡点
    hashed = bcrypt.hashpw(password_bytes, salt)

    # 返回字符串格式的哈希
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """验证明文密码是否与哈希值匹配。

    Args:
        password: 明文密码
        hashed: bcrypt 哈希字符串

    Returns:
        True 如果匹配，False 如果不匹配
    """
    if not password or not hashed:
        return False

    try:
        password_bytes = password.encode("utf-8")
        hashed_bytes = hashed.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except (ValueError, TypeError) as exc:
        logger.warning("密码验证失败: %s", exc)
        return False


# ── 注册逻辑 ─────────────────────────────────────────────

def register_user(
    username: str,
    password: str,
    user_data_manager: UserDataManager,
    email: str = "",
) -> Tuple[Optional[dict], Optional[str]]:
    """注册新用户。

    完整注册流程：
    1. 验证用户名格式（由 auth_components.validate_username 处理）
    2. 验证密码格式（由 auth_components.validate_password 处理）
    3. 密码 bcrypt 哈希处理
    4. 检查用户名是否已存在
    5. 创建用户数据记录
    6. 返回用户数据或错误信息

    Args:
        username: 用户名（已验证格式）
        password: 明文密码（已验证格式）
        user_data_manager: 用户数据管理器实例
        email: 邮箱（可选）

    Returns:
        (user_data, error_message) 元组：
        - 成功时：(user_data, None)
        - 失败时：(None, error_message)
    """
    try:
        # 1. 密码哈希处理
        logger.info("开始注册用户: %s", username)
        password_hash = hash_password(password)

        # 2. 创建用户（UserDataManager 内部会检查用户名唯一性）
        user_data, error_msg = user_data_manager.safe_create_user(
            username=username,
            password_hash=password_hash,
            email=email,
            role="user",
        )

        if error_msg:
            # 创建失败，返回错误信息
            logger.warning("用户注册失败: %s — %s", username, error_msg)
            return None, error_msg

        # 3. 注册成功
        logger.info("用户注册成功: %s", username)
        return user_data, None

    except Exception as exc:
        # 未预期的异常
        logger.error("用户注册异常: %s — %s", username, exc, exc_info=True)
        return None, "注册失败，请稍后重试。如持续失败，请联系管理员。"


def login_user(
    username: str,
    password: str,
    user_data_manager: UserDataManager,
) -> Tuple[Optional[dict], Optional[str]]:
    """用户登录。

    登录流程：
    1. 验证输入格式（后端防御层）
    2. 检查用户名是否存在
    3. 验证密码（支持 bcrypt 和旧版 SHA-256 向后兼容）
    4. 加载用户数据
    5. 返回用户数据或错误信息

    Args:
        username: 用户名
        password: 明文密码
        user_data_manager: 用户数据管理器实例

    Returns:
        (user_data, error_message) 元组：
        - 成功时：(user_data, None)
        - 失败时：(None, error_message)
    """
    try:
        # 0. 后端输入验证（防御层：即使前端已验证，后端也做基本校验）
        if not username or not username.strip():
            return None, "请输入用户名"
        if not password:
            return None, "请输入密码"

        username = username.strip()

        # 0.5 登录速率限制检查
        if _login_rate_limiter.is_limited(username):
            lockout_secs = _login_rate_limiter.lockout_remaining(username)
            if lockout_secs > 0:
                logger.warning("用户 %s 登录被拒绝：账户已锁定（剩余 %d 秒）", username, lockout_secs)
                return None, "登录尝试次数过多，账户已锁定，请稍后重试"
            logger.warning("用户 %s 登录被拒绝：请求过于频繁", username)
            return None, "登录尝试过于频繁，请稍后重试"

        # 1. 获取用户数据（只读，不更新活跃时间）
        user_data, error_msg = user_data_manager.safe_get_user(username)

        if error_msg:
            # 用户不存在或其他错误
            if "不存在" in error_msg:
                return None, "用户名不存在"
            return None, error_msg

        # 2. 验证密码
        stored_hash = user_data.get("profile", {}).get("password_hash", "")
        if not stored_hash:
            logger.error("用户 %s 的密码哈希为空", username)
            return None, "系统异常，请联系管理员"

        # 支持 bcrypt 和旧版 SHA-256（向后兼容）
        password_matched = False

        # 检查是否为 bcrypt 格式（以 $2b$ 或 $2a$ 开头）
        needs_migration = False
        if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
            # bcrypt 格式
            password_matched = verify_password(password, stored_hash)
        elif len(stored_hash) == 64 and all(c in "0123456789abcdef" for c in stored_hash.lower()):
            # 旧版 SHA-256 格式（64 位十六进制）
            # 向后兼容：允许旧用户登录，登录成功后自动迁移到 bcrypt
            computed_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
            password_matched = hmac.compare_digest(computed_hash.lower(), stored_hash.lower())
            if password_matched:
                needs_migration = True
            logger.warning(
                "用户 %s 使用旧版 SHA-256 密码格式，将自动迁移到 bcrypt",
                username,
            )
        else:
            # 未知格式，拒绝登录
            logger.error("用户 %s 的密码哈希格式未知", username)
            password_matched = False

        if not password_matched:
            _login_rate_limiter.record_failure(username)
            logger.info("用户 %s 登录失败: 密码错误", username)
            return None, "密码错误"

        # 2.5 SHA-256 → bcrypt 自动迁移
        if needs_migration:
            try:
                new_hash = hash_password(password)
                user_data_manager.update_user(
                    username,
                    {"profile": {**user_data.get("profile", {}), "password_hash": new_hash}},
                )
                logger.info("用户 %s 的密码已从 SHA-256 迁移到 bcrypt", username)
            except Exception as mig_exc:
                logger.error("用户 %s 密码迁移失败: %s", username, mig_exc, exc_info=True)

        # 3. 密码正确，清除限流记录，加载用户数据（更新活跃时间和登录统计）
        _login_rate_limiter.record_success(username)
        user_data_full = user_data_manager.get_user(username)

        logger.info("用户登录成功: %s", username)
        return user_data_full, None

    except Exception as exc:
        logger.error("用户登录异常: %s — %s", username, exc, exc_info=True)
        return None, "登录失败，请稍后重试"


# ── 便捷函数 ─────────────────────────────────────────────

def create_auth_manager() -> UserDataManager:
    """创建用户数据管理器的便捷工厂函数。

    Returns:
        UserDataManager 实例
    """
    return UserDataManager()
