"""医学教材知识库 — 用户数据 CRUD 管理器

实现用户数据的创建、读取、更新、删除操作，包含：
- 空间限制检查（每用户 10KB）
- 过期数据清理（30 天自动清理，清理前 7 天提醒）
- 请求失败降级处理（本地缓存或提示用户）
- 数据操作日志记录
- 批量操作（批量清理过期数据等）

依赖：jsonbin_client.py、database_models.py
"""

import json
import logging
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from upstash_client import (
    UpstashClient as JSONBinClient,
    UpstashError as JSONBinError,
    UpstashRequestError as JSONBinRequestError,
    UpstashTimeoutError as JSONBinTimeoutError,
    UpstashAuthError as JSONBinAuthError,
)
from database_models import (
    UserData,
    UserProfile,
    ValidationError,
    MAX_USER_DATA_SIZE,
    EXPIRE_DAYS,
    EXPIRE_WARN_DAYS,
    MAX_LEARNING_RECORDS,
    create_default_user,
    create_default_logs,
    create_default_config,
    create_default_root,
    check_expiry,
    estimate_json_size,
    estimate_user_data_size,
    utcnow_iso,
    add_operation_log,
    add_error_log,
    _sanitize_sensitive_info,
)

logger = logging.getLogger(__name__)


# ── 日志注入防护 ──────────────────────────────────────────

# 匹配控制字符（换行、回车、制表符等），用于防止日志注入
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_log_value(value: str) -> str:
    """清洗日志字段值，移除换行符和控制字符，防止日志注入攻击。

    攻击者可能通过注册包含换行符的用户名（如 'alice\\n[INFO] fake log'）
    注入伪造的日志条目。此函数将所有控制字符替换为空格。
    """
    if not isinstance(value, str):
        value = str(value)
    return _CONTROL_CHAR_RE.sub(" ", value)


# ── 异常定义 ──────────────────────────────────────────────

class UserDataError(Exception):
    """用户数据管理基础异常。"""


class UserNotFoundError(UserDataError):
    """用户不存在。"""


class UserExistsError(UserDataError):
    """用户已存在。"""


class StorageLimitExceededError(UserDataError):
    """存储空间超限。"""

    def __init__(self, username: str, current_size: int, limit: int = MAX_USER_DATA_SIZE):
        self.username = username
        self.current_size = current_size
        self.limit = limit
        super().__init__(
            f"用户 {username!r} 数据大小 ({current_size} bytes) "
            f"超过上限 ({limit} bytes)，请清理数据后重试"
        )


class DataOperationError(UserDataError):
    """数据操作失败（网络/服务端错误）。"""

    def __init__(self, message: str, fallback_data: Any = None):
        self.fallback_data = fallback_data
        super().__init__(message)


# ── 常量 ─────────────────────────────────────────────────

DEFAULT_BATCH_SIZE = 50
"""批量操作默认批次大小。"""


# ── 用户数据管理器 ────────────────────────────────────────

class UserDataManager:
    """用户数据 CRUD 管理器。

    封装所有用户数据操作，包含空间限制检查、过期管理、日志记录和降级处理。

    用法::

        manager = UserDataManager()              # 自动从 .env 读取配置
        manager.create_user("alice", "hash_xxx")  # 创建用户
        data = manager.get_user("alice")          # 读取用户数据
        manager.update_user("alice", data)        # 更新用户数据
        manager.delete_user("alice")              # 删除用户

    也支持手动传入 client::

        client = JSONBinClient(api_key="xxx", bin_id="yyy")
        manager = UserDataManager(client=client)
    """

    # 允许的角色值
    _VALID_ROLES = frozenset({"user", "admin", "guest"})
    # 需要管理员权限才能创建的角色
    _PRIVILEGED_ROLES = frozenset({"admin"})

    def __init__(
        self,
        client: Optional[JSONBinClient] = None,
        auto_init: bool = True,
        current_user: Optional[str] = None,
    ):
        """初始化用户数据管理器。

        Args:
            client: JSONBin 客户端实例（默认自动创建）
            auto_init: 是否自动初始化根数据结构（若不存在）
            current_user: 当前操作用户名（用于权限检查，None 表示系统级操作）
        """
        self._client = client or JSONBinClient()
        self._auto_init = auto_init
        self._initialized = False
        self._current_user = current_user
        # 线程锁，防止并发操作导致数据覆盖
        self._lock = threading.Lock()
        # 短时根数据缓存，避免同一流程内重复网络读取（如登录流程的 safe_get_user + record_login_success）
        self._root_cache: Optional[dict] = None
        self._root_cache_time: float = 0

    # ── 内部工具方法 ──────────────────────────────────────

    def _ensure_initialized(self) -> dict:
        """确保根数据结构已初始化，返回当前根数据。

        使用短时缓存（3秒）避免同一流程内重复网络读取。
        缓存在 _save_root 后自动失效，确保后续操作读取最新数据。

        Returns:
            根数据字典

        Raises:
            DataOperationError: 读取/初始化失败
        """
        # 短时缓存命中：避免同一流程内的重复网络请求
        if self._root_cache is not None and (time.time() - self._root_cache_time) < 3:
            return self._root_cache

        try:
            record = self._client.get_record()
            if isinstance(record, dict) and "users" in record:
                self._initialized = True
                self._root_cache = record
                self._root_cache_time = time.time()
                return record
        except JSONBinError as exc:
            logger.warning("读取根数据失败，尝试初始化: %s", exc)

        # 初始化根数据
        if self._auto_init:
            try:
                root = create_default_root()
                self._client.update_record(root)
                self._initialized = True
                self._root_cache = root
                self._root_cache_time = time.time()
                logger.info("根数据结构已初始化")
                return root
            except JSONBinError as exc:
                raise DataOperationError(
                    f"初始化根数据失败: {exc}",
                    fallback_data=create_default_root(),
                ) from exc

        raise DataOperationError("根数据未初始化且 auto_init=False")

    def _save_root(self, root: dict) -> None:
        """保存根数据到 JSONBin。

        保存后清除根数据缓存，确保下次 _ensure_initialized 读取最新数据。

        Args:
            root: 根数据字典

        Raises:
            DataOperationError: 保存失败
        """
        try:
            self._client.update_record(root)
            # 保存后清除缓存，确保后续操作读取最新数据
            self._root_cache = None
            self._root_cache_time = 0
        except JSONBinError as exc:
            raise DataOperationError(f"保存数据失败: {exc}") from exc

    def _log_operation(self, root: dict, action: str, username: str = "",
                       detail: str = "", ip_address: str = "") -> None:
        """记录操作日志到根数据中。

        日志内容会过滤敏感信息。

        Args:
            root: 根数据字典（会被原地修改）
            action: 操作类型
            username: 操作用户
            detail: 操作详情
            ip_address: 来源 IP
        """
        logs = root.get("logs", create_default_logs())
        sanitized_detail = _sanitize_sensitive_info(_sanitize_log_value(detail))
        sanitized_username = _sanitize_log_value(username)
        root["logs"] = add_operation_log(
            logs, action=action, username=sanitized_username,
            detail=sanitized_detail, ip_address=ip_address,
        )

    def _log_error(self, root: dict, error_type: str, message: str,
                   username: str = "", stack_trace: str = "") -> None:
        """记录错误日志到根数据中。

        Args:
            root: 根数据字典（会被原地修改）
            error_type: 错误类型
            message: 错误信息
            username: 关联用户
            stack_trace: 堆栈信息
        """
        logs = root.get("logs", create_default_logs())
        root["logs"] = add_error_log(
            logs, error_type=error_type, message=message,
            username=username, stack_trace=stack_trace,
        )

    def _check_user_data_size(self, username: str, user_data: dict) -> int:
        """检查用户数据大小是否超限。

        Args:
            username: 用户名
            user_data: 用户数据字典

        Returns:
            数据大小（字节）

        Raises:
            StorageLimitExceededError: 数据超限
        """
        size = estimate_json_size(user_data)
        if size > MAX_USER_DATA_SIZE:
            raise StorageLimitExceededError(username, size)
        return size

    def _touch_active(self, user_data: dict) -> None:
        """更新用户最后活跃时间。

        Args:
            user_data: 用户数据字典（会被原地修改）
        """
        profile = user_data.get("profile", {})
        profile["last_active_at"] = utcnow_iso()
        user_data["profile"] = profile

    # ── CRUD 操作 ─────────────────────────────────────────

    def create_user(
        self,
        username: str,
        password_hash: str,
        email: str = "",
        role: str = "user",
    ) -> dict:
        """创建新用户（注册时调用）。

        Args:
            username: 用户名
            password_hash: 密码哈希值（bcrypt 或 SHA-256）
            email: 邮箱（可选）
            role: 角色（默认 user）

        Returns:
            新创建的用户数据字典

        Raises:
            UserExistsError: 用户已存在
            StorageLimitExceededError: 数据超限
            DataOperationError: 操作失败
            ValidationError: 字段校验失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username in users:
                raise UserExistsError(f"用户 {username!r} 已存在")

            # 角色权限验证：检查目标角色是否合法
            if role not in self._VALID_ROLES:
                raise ValidationError(
                    "role",
                    f"非法角色: {role!r}，允许值: {', '.join(sorted(self._VALID_ROLES))}",
                )

            # 权限检查：创建特权角色（如 admin）需要管理员权限
            if role in self._PRIVILEGED_ROLES:
                if self._current_user is None:
                    # 系统级操作（无当前用户上下文），允许
                    pass
                else:
                    # 检查当前用户是否为管理员
                    caller_data = users.get(self._current_user)
                    if caller_data is None:
                        raise DataOperationError(
                            "权限不足: 当前用户不存在，无法创建特权角色用户"
                        )
                    caller_role = caller_data.get("profile", {}).get("role", "user")
                    if caller_role != "admin":
                        raise DataOperationError(
                            f"权限不足: 仅管理员可创建 {role!r} 角色用户，"
                            f"当前用户 {self._current_user!r} 角色为 {caller_role!r}"
                        )

            # 创建默认用户数据
            user_data = create_default_user(
                username=username,
                password_hash=password_hash,
                email=email,
                role=role,
            )

            # 大小检查
            self._check_user_data_size(username, user_data)

            # 写入根数据
            users[username] = user_data
            root["users"] = users

            # 记录操作日志
            self._log_operation(
                root, action="register", username=username,
                detail=f"用户注册: {username}",
            )

            # 更新统计
            logs = root.get("logs", create_default_logs())
            stats = logs.get("usage_stats", {})
            stats["total_users"] = stats.get("total_users", 0) + 1
            logs["usage_stats"] = stats
            root["logs"] = logs

            # 保存
            self._save_root(root)
            logger.info("用户创建成功: %s", username)
            return user_data

    def get_user(self, username: str) -> dict:
        """读取用户数据（登录时调用）。

        Args:
            username: 用户名

        Returns:
            用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            user_data = deepcopy(users[username])

            # 更新最后活跃时间
            self._touch_active(user_data)
            users[username] = user_data
            root["users"] = users

            # 记录操作日志
            self._log_operation(
                root, action="login", username=username,
                detail=f"用户登录: {username}",
            )

            # 更新登录统计
            stats = user_data.get("stats", {})
            stats["login_count"] = stats.get("login_count", 0) + 1
            user_data["stats"] = stats
            users[username] = user_data
            root["users"] = users

            self._save_root(root)
            logger.info("用户数据读取成功: %s", username)
            return user_data

    def record_login_success(self, username: str, user_data: dict) -> dict:
        """登录成功后更新统计并保存（接受已获取的 user_data，避免重复网络读取）。

        在一次 locked 操作中完成：读取根数据 → 更新登录计数 → 更新活跃时间 → 记录日志 → 保存。
        与 safe_get_user 配合使用时，总网络调用为 1次读 + 1次写（相比 get_user 的 1次读 + 1次写
        再加 safe_get_user 的 1次读，节省一次 Upstash 读取请求）。

        Args:
            username: 用户名
            user_data: safe_get_user 返回的用户数据（会在此基础上更新统计）

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            # 更新登录统计
            stats = user_data.get("stats", {})
            stats["login_count"] = stats.get("login_count", 0) + 1
            user_data["stats"] = stats

            # 更新活跃时间
            self._touch_active(user_data)

            users[username] = user_data
            root["users"] = users

            # 记录操作日志
            self._log_operation(
                root, action="login", username=username,
                detail=f"用户登录: {username}",
            )

            self._save_root(root)
            logger.info("登录成功记录完成: %s", username)
            return user_data

    def get_user_readonly(self, username: str) -> dict:
        """只读获取用户数据（不更新活跃时间和登录统计）。

        适用于仅需查询用户数据的场景，避免不必要的写入操作。

        Args:
            username: 用户名

        Returns:
            用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            DataOperationError: 操作失败
        """
        root = self._ensure_initialized()
        users = root.get("users", {})

        if username not in users:
            raise UserNotFoundError(f"用户 {username!r} 不存在")

        return deepcopy(users[username])

    def update_user(self, username: str, user_data: dict) -> dict:
        """更新用户数据。

        会进行大小校验和数据验证。

        Args:
            username: 用户名
            user_data: 新的用户数据字典

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            StorageLimitExceededError: 数据超限
            ValidationError: 数据校验失败
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            # 数据校验
            ud = UserData.from_dict(user_data)
            ud.validate()

            # 角色不可变性守卫：禁止通过 update_user 修改 role 字段（防止权限提升）
            # role 仅可在 create_user（含特权校验）中设置，或由专门的管理员操作变更，
            # 避免任意调用方借全量更新将自身/他人提权为 admin。
            _existing_role = users[username].get("profile", {}).get("role", "user")
            _incoming_profile = user_data.get("profile", {})
            if isinstance(_incoming_profile, dict) and "role" in _incoming_profile:
                _incoming_role = _incoming_profile["role"]
                if _incoming_role != _existing_role:
                    raise DataOperationError(
                        "权限不足: 不允许通过用户数据更新修改角色"
                        f"（当前: {_existing_role!r}, 尝试: {_incoming_role!r}）"
                    )

            # 大小检查
            self._check_user_data_size(username, user_data)

            # 更新活跃时间
            self._touch_active(user_data)

            users[username] = user_data
            root["users"] = users

            # 记录操作日志
            self._log_operation(
                root, action="update_prefs", username=username,
                detail=f"用户数据更新: {username}",
            )

            self._save_root(root)
            logger.info("用户数据更新成功: %s", username)
            return user_data

    def delete_user(self, username: str) -> None:
        """删除用户数据（用户注销或管理员操作）。

        Args:
            username: 用户名

        Raises:
            UserNotFoundError: 用户不存在
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            del users[username]
            root["users"] = users

            # 记录操作日志
            self._log_operation(
                root, action="logout", username=username,
                detail=f"用户删除: {username}",
            )

            # 更新统计
            logs = root.get("logs", create_default_logs())
            stats = logs.get("usage_stats", {})
            stats["total_users"] = max(0, stats.get("total_users", 1) - 1)
            logs["usage_stats"] = stats
            root["logs"] = logs

            self._save_root(root)
            logger.info("用户删除成功: %s", username)

    # ── 管理员操作 ──────────────────────────────────────────

    def change_role(
        self, username: str, new_role: str, operator: Optional[str] = None,
    ) -> dict:
        """管理员变更用户角色。

        安全规则（角色不可变性守卫）：
        - 不允许将用户提升为 admin（防提权攻击）
        - 仅允许 admin -> user/guest 降级，或 user <-> guest 互转
        - update_user 的通用路径拒绝 role 变更，此方法为受控专用接口

        Args:
            username: 目标用户名
            new_role: 新角色（user / guest / admin）
            operator: 操作者用户名（用于日志记录，默认取 current_user）

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            ValidationError: 非法角色值
            DataOperationError: 尝试提权为 admin 或操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            if new_role not in self._VALID_ROLES:
                raise ValidationError(
                    "role",
                    f"非法角色: {new_role!r}，允许值: {', '.join(sorted(self._VALID_ROLES))}",
                )

            # 防提权守卫：禁止通过管理面板将用户提升为 admin
            if new_role == "admin":
                raise DataOperationError(
                    "安全限制：不允许通过管理面板将用户提升为管理员角色"
                )

            user_data = deepcopy(users[username])
            current_role = user_data.get("profile", {}).get("role", "user")
            user_data["profile"]["role"] = new_role
            users[username] = user_data
            root["users"] = users

            operator_name = operator or self._current_user or "admin"
            self._log_operation(
                root, action="update_prefs", username=operator_name,
                detail=f"角色变更: {username} ({current_role} -> {new_role})",
            )

            self._save_root(root)
            logger.info("用户角色变更: %s %s -> %s (by %s)",
                        username, current_role, new_role, operator_name)
            return user_data

    def reset_password(
        self, username: str, new_password_hash: str,
        operator: Optional[str] = None,
    ) -> dict:
        """管理员重置用户密码。

        Args:
            username: 目标用户名
            new_password_hash: 新密码的哈希值（bcrypt 格式）
            operator: 操作者用户名（用于日志记录）

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            user_data = deepcopy(users[username])
            user_data["profile"]["password_hash"] = new_password_hash
            users[username] = user_data
            root["users"] = users

            operator_name = operator or self._current_user or "admin"
            self._log_operation(
                root, action="update_prefs", username=operator_name,
                detail=f"密码重置: {username}",
            )

            self._save_root(root)
            logger.info("用户密码重置: %s (by %s)", username, operator_name)
            return user_data

    def set_user_disabled(
        self, username: str, disabled: bool,
        operator: Optional[str] = None,
    ) -> dict:
        """管理员禁用/启用用户账户。

        禁用的用户在登录时会被拒绝。禁用状态存储在 profile.disabled 字段。

        Args:
            username: 目标用户名
            disabled: True 禁用 / False 启用
            operator: 操作者用户名（用于日志记录）

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            user_data = deepcopy(users[username])
            user_data["profile"]["disabled"] = disabled
            users[username] = user_data
            root["users"] = users

            operator_name = operator or self._current_user or "admin"
            action_text = "禁用" if disabled else "启用"
            self._log_operation(
                root, action="update_prefs", username=operator_name,
                detail=f"账户{action_text}: {username}",
            )

            self._save_root(root)
            logger.info("用户账户%s: %s (by %s)",
                        action_text, username, operator_name)
            return user_data

    # ── 学习记录操作 ──────────────────────────────────────

    def add_learning_record(
        self,
        username: str,
        topic: str,
        query: str = "",
        source_type: str = "chat",
        duration_seconds: int = 0,
        extra_data: Optional[dict] = None,
    ) -> dict:
        """添加学习记录。

        自动检查大小限制，超限时拒绝并回滚。

        Args:
            username: 用户名
            topic: 学习主题
            query: 用户提问
            source_type: 来源类型（textbook/search/chat）
            duration_seconds: 学习时长（秒）
            extra_data: 扩展数据

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            StorageLimitExceededError: 数据超限
            ValidationError: 记录校验失败
            DataOperationError: 操作失败
        """
        from database_models import add_learning_record as _add_record

        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            user_data = deepcopy(users[username])
            old_data = deepcopy(user_data)  # 备份用于回滚

            try:
                user_data = _add_record(
                    user_data,
                    topic=topic,
                    query=query,
                    source_type=source_type,
                    duration_seconds=duration_seconds,
                    extra_data=extra_data,
                )
            except ValidationError:
                raise
            except Exception as exc:
                raise DataOperationError(f"添加学习记录失败: {exc}") from exc

            # 最终大小检查
            self._check_user_data_size(username, user_data)

            users[username] = user_data
            root["users"] = users

            # 记录操作日志
            self._log_operation(
                root, action="query", username=username,
                detail=f"学习记录: topic={topic}",
            )

            self._save_root(root)
            logger.info("学习记录添加成功: user=%s, topic=%s", username, topic)
            return user_data

    # ── 偏好设置操作 ──────────────────────────────────────

    def update_preferences(self, username: str, preferences: dict) -> dict:
        """更新用户偏好设置。

        Args:
            username: 用户名
            preferences: 新的偏好设置字典

        Returns:
            更新后的用户数据字典

        Raises:
            UserNotFoundError: 用户不存在
            StorageLimitExceededError: 数据超限
            ValidationError: 偏好校验失败
            DataOperationError: 操作失败
        """
        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})

            if username not in users:
                raise UserNotFoundError(f"用户 {username!r} 不存在")

            user_data = deepcopy(users[username])
            user_data["preferences"] = preferences

            # 校验偏好
            from database_models import UserPreferences
            prefs = UserPreferences(**{
                k: v for k, v in preferences.items()
                if k in UserPreferences.__dataclass_fields__
            })
            prefs.validate()

            # 大小检查
            self._check_user_data_size(username, user_data)

            # 更新活跃时间
            self._touch_active(user_data)

            users[username] = user_data
            root["users"] = users

            self._log_operation(
                root, action="update_prefs", username=username,
                detail="偏好设置更新",
            )

            self._save_root(root)
            logger.info("用户偏好更新成功: %s", username)
            return user_data

    # ── 过期管理 ──────────────────────────────────────────

    def check_user_expiry(self, username: str) -> dict:
        """检查单个用户的过期状态。

        Args:
            username: 用户名

        Returns:
            过期信息字典，包含 is_expired、is_warning、days_remaining 等

        Raises:
            UserNotFoundError: 用户不存在
        """
        user_data = self.get_user_readonly(username)
        return check_expiry(user_data)

    def get_expiry_warnings(self) -> list[dict]:
        """获取所有即将过期和已过期的用户列表。

        Returns:
            过期警告列表，每项包含 username、expiry_info
        """
        root = self._ensure_initialized()
        users = root.get("users", {})
        warnings = []

        for username, user_data in users.items():
            info = check_expiry(user_data)
            if info["is_expired"] or info["is_warning"]:
                warnings.append({
                    "username": username,
                    "expiry_info": info,
                })

        return warnings

    def get_expired_users(self) -> list[str]:
        """获取所有已过期（超过 30 天未活跃）的用户名列表。

        Returns:
            过期用户名列表
        """
        root = self._ensure_initialized()
        users = root.get("users", {})
        expired = []

        for username, user_data in users.items():
            info = check_expiry(user_data)
            if info["is_expired"]:
                expired.append(username)

        return expired

    def get_expiring_users(self) -> list[str]:
        """获取所有即将过期（距过期不足 7 天）的用户名列表。

        Returns:
            即将过期的用户名列表
        """
        root = self._ensure_initialized()
        users = root.get("users", {})
        expiring = []

        for username, user_data in users.items():
            info = check_expiry(user_data)
            if info["is_warning"]:
                expiring.append(username)

        return expiring

    def cleanup_expired_users(self, batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
        """批量清理过期用户数据。

        清理超过 30 天未活跃的用户数据。支持分批处理。

        Args:
            batch_size: 每批清理的最大用户数

        Returns:
            清理结果字典::

                {
                    "cleaned": ["user1", "user2"],
                    "failed": ["user3"],
                    "total_cleaned": 2,
                    "total_failed": 1,
                }
        """
        result = {
            "cleaned": [],
            "failed": [],
            "total_cleaned": 0,
            "total_failed": 0,
        }

        expired = self.get_expired_users()
        if not expired:
            logger.info("无过期用户需要清理")
            return result

        # 分批处理
        to_clean = expired[:batch_size]
        logger.info("开始清理过期用户，共 %d 个（批次 %d）", len(expired), len(to_clean))

        for username in to_clean:
            try:
                self.delete_user(username)
                result["cleaned"].append(username)
                result["total_cleaned"] += 1
                logger.info("过期用户清理成功: %s", username)
            except (UserDataError, JSONBinError) as exc:
                result["failed"].append(username)
                result["total_failed"] += 1
                logger.warning("过期用户清理失败: %s — %s", username, exc)

        if len(expired) > batch_size:
            logger.info(
                "还有 %d 个过期用户未清理，请再次调用 cleanup_expired_users",
                len(expired) - batch_size,
            )

        return result

    def notify_expiring_users(self) -> list[dict]:
        """获取即将过期用户的提醒信息。

        在用户登录时调用，返回需要展示给用户的提醒列表。

        Returns:
            提醒信息列表，每项包含 username、days_remaining、message
        """
        expiring = self.get_expiring_users()
        notifications = []

        for username in expiring:
            try:
                info = self.check_user_expiry(username)
                days = info["days_remaining"]
                notifications.append({
                    "username": username,
                    "days_remaining": days,
                    "message": (
                        f"您的数据将在 {days} 天后因长期未活跃而被自动清理，"
                        f"请登录以保持数据活跃。"
                    ),
                })
            except UserNotFoundError:
                continue

        return notifications

    # ── 批量操作 ──────────────────────────────────────────

    def list_users(self) -> list[dict]:
        """列出所有用户的基本信息。

        Returns:
            用户基本信息列表，每项包含 username、role、created_at、last_active_at、data_size
        """
        root = self._ensure_initialized()
        users = root.get("users", {})
        result = []

        for username, user_data in users.items():
            profile = user_data.get("profile", {})
            stats = user_data.get("stats", {})
            size_info = estimate_user_data_size(user_data)
            result.append({
                "username": username,
                "role": profile.get("role", "user"),
                "email": profile.get("email", ""),
                "created_at": profile.get("created_at", ""),
                "last_active_at": profile.get("last_active_at", ""),
                "login_count": stats.get("login_count", 0),
                "disabled": profile.get("disabled", False),
                "data_size_bytes": size_info["total_bytes"],
                "data_size_kb": size_info["total_kb"],
            })

        return result

    def get_system_stats(self) -> dict:
        """获取系统级统计数据（供管理面板使用）。

        Returns:
            统计数据字典，包含:
            - total_users: 总用户数
            - total_queries: 总查询次数（所有用户查询次数之和）
            - admin_count: 管理员数量
            - user_count: 普通用户数量
        """
        root = self._ensure_initialized()
        users = root.get("users", {})

        total_queries = 0
        admin_count = 0
        user_count = 0

        for username, user_data in users.items():
            stats = user_data.get("stats", {})
            total_queries += stats.get("total_queries", 0)
            role = user_data.get("profile", {}).get("role", "user")
            if role == "admin":
                admin_count += 1
            elif role == "user":
                user_count += 1

        return {
            "total_users": len(users),
            "total_queries": total_queries,
            "admin_count": admin_count,
            "user_count": user_count,
        }

    def get_operation_logs(self) -> list[dict]:
        """获取所有操作日志（供管理面板趋势分析使用）。

        操作日志存储在根数据的 logs.operation_logs 中，上限 MAX_OPERATION_LOGS 条。
        每条包含 timestamp、username、action、detail、ip_address。

        Returns:
            操作日志列表（深拷贝，调用方可安全修改）
        """
        root = self._ensure_initialized()
        logs = root.get("logs", {})
        return deepcopy(logs.get("operation_logs", []))

    def get_all_learning_records(self) -> list[dict]:
        """聚合所有用户的学习记录（供管理面板模式使用分析使用）。

        遍历所有用户，收集其 learning_records 并附加 username 字段。
        每条包含 username、topic、query、timestamp、source_type、duration_seconds。

        Returns:
            学习记录列表（深拷贝）
        """
        root = self._ensure_initialized()
        users = root.get("users", {})
        records = []
        for username, user_data in users.items():
            for rec in user_data.get("learning_records", []):
                entry = deepcopy(rec)
                entry["username"] = username
                records.append(entry)
        return records

    def batch_update_active(self, usernames: list[str]) -> dict:
        """批量更新用户活跃时间。

        适用于用户同时操作多个账号的场景。

        Args:
            usernames: 用户名列表

        Returns:
            更新结果::

                {
                    "updated": ["user1", "user2"],
                    "failed": ["user3"],
                }
        """
        result = {"updated": [], "failed": []}

        with self._lock:
            root = self._ensure_initialized()
            users = root.get("users", {})
            now = utcnow_iso()

            for username in usernames:
                if username not in users:
                    result["failed"].append(username)
                    continue

                user_data = users[username]
                profile = user_data.get("profile", {})
                profile["last_active_at"] = now
                user_data["profile"] = profile
                users[username] = user_data
                result["updated"].append(username)

            root["users"] = users

            try:
                self._save_root(root)
            except DataOperationError as exc:
                logger.warning("批量更新活跃时间保存失败: %s", exc)
                result["failed"] = list(usernames)
                result["updated"] = []

        return result

    def batch_delete_users(self, usernames: list[str]) -> dict:
        """批量删除用户。

        Args:
            usernames: 要删除的用户名列表

        Returns:
            删除结果::

                {
                    "deleted": ["user1"],
                    "not_found": ["user2"],
                    "failed": ["user3"],
                }
        """
        result = {"deleted": [], "not_found": [], "failed": []}

        for username in usernames:
            try:
                self.delete_user(username)
                result["deleted"].append(username)
            except UserNotFoundError:
                result["not_found"].append(username)
            except (DataOperationError, JSONBinError) as exc:
                result["failed"].append(username)
                logger.warning("批量删除用户失败: %s — %s", username, exc)

        return result

    def get_all_expired_users_detail(self) -> list[dict]:
        """获取所有过期用户的详细信息（包含数据大小、过期天数等）。

        Returns:
            过期用户详情列表
        """
        root = self._ensure_initialized()
        users = root.get("users", {})
        result = []

        for username, user_data in users.items():
            info = check_expiry(user_data)
            if info["is_expired"]:
                size_info = estimate_user_data_size(user_data)
                profile = user_data.get("profile", {})
                result.append({
                    "username": username,
                    "last_active_at": info["last_active_at"],
                    "days_remaining": info["days_remaining"],
                    "data_size_bytes": size_info["total_bytes"],
                    "data_size_kb": size_info["total_kb"],
                    "role": profile.get("role", "user"),
                    "created_at": profile.get("created_at", ""),
                })

        return result

    # ── 空间管理 ──────────────────────────────────────────

    def get_user_data_size(self, username: str) -> dict:
        """获取用户数据大小详情。

        Args:
            username: 用户名

        Returns:
            大小详情字典，包含各部分大小和总大小

        Raises:
            UserNotFoundError: 用户不存在
        """
        user_data = self.get_user_readonly(username)
        return estimate_user_data_size(user_data)

    def check_storage_limit(self, username: str) -> dict:
        """检查用户存储空间使用情况。

        Args:
            username: 用户名

        Returns:
            空间使用信息::

                {
                    "used_bytes": int,
                    "limit_bytes": int,
                    "used_percent": float,
                    "remaining_bytes": int,
                    "is_near_limit": bool,
                    "is_over_limit": bool,
                }

        Raises:
            UserNotFoundError: 用户不存在
        """
        size_info = self.get_user_data_size(username)
        used = size_info["total_bytes"]
        limit = MAX_USER_DATA_SIZE
        remaining = max(0, limit - used)
        percent = round(used / limit * 100, 1) if limit > 0 else 0

        return {
            "used_bytes": used,
            "limit_bytes": limit,
            "used_percent": percent,
            "remaining_bytes": remaining,
            "is_near_limit": percent >= 80,
            "is_over_limit": used > limit,
        }

    # ── 降级处理 ──────────────────────────────────────────

    def safe_get_user(self, username: str) -> tuple[Optional[dict], Optional[str]]:
        """安全获取用户数据，失败时返回降级信息。

        不抛出异常，而是返回 (data, error_message) 元组。
        前端可根据 error_message 展示降级提示。

        Args:
            username: 用户名

        Returns:
            (用户数据或 None, 错误信息或 None)
        """
        try:
            data = self.get_user_readonly(username)
            return data, None
        except UserNotFoundError:
            return None, f"用户 {username!r} 不存在"
        except DataOperationError as exc:
            if exc.fallback_data:
                users = exc.fallback_data.get("users", {})
                cached = users.get(username)
                if cached:
                    logger.info("使用降级缓存返回用户数据: %s", username)
                    return cached, "当前使用离线缓存数据，可能与最新数据不同步"
            return None, "数据加载失败，请稍后重试。如持续失败，请检查网络连接。"
        except Exception as exc:
            logger.error("未预期的错误: %s", exc, exc_info=True)
            return None, "系统异常，请稍后重试"

    def safe_create_user(
        self,
        username: str,
        password_hash: str,
        email: str = "",
        role: str = "user",
    ) -> tuple[Optional[dict], Optional[str]]:
        """安全创建用户，失败时返回降级信息。

        Args:
            username: 用户名
            password_hash: 密码哈希
            email: 邮箱
            role: 角色

        Returns:
            (用户数据或 None, 错误信息或 None)
        """
        try:
            data = self.create_user(username, password_hash, email, role)
            return data, None
        except UserExistsError:
            return None, "用户名已存在，请选择其他用户名"
        except StorageLimitExceededError:
            return None, "存储空间不足，无法创建用户"
        except ValidationError as exc:
            logger.warning("创建用户数据校验失败: field=%s, message=%s", exc.field, exc.message)
            return None, "数据校验失败，请检查输入格式是否正确"
        except DataOperationError:
            return None, "创建用户失败，请稍后重试。如持续失败，请检查网络连接。"
        except Exception as exc:
            logger.error("未预期的错误: %s", exc, exc_info=True)
            return None, "系统异常，请稍后重试"

    # ── 上下文管理 ────────────────────────────────────────

    def close(self) -> None:
        """关闭底层客户端。"""
        if self._client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self) -> str:
        return f"UserDataManager(client={self._client!r})"


# ── 便捷函数 ──────────────────────────────────────────────

def create_manager(client: Optional[JSONBinClient] = None) -> UserDataManager:
    """创建 UserDataManager 实例的工厂函数。

    Args:
        client: 可选的 JSONBin 客户端实例

    Returns:
        UserDataManager 实例
    """
    return UserDataManager(client=client)
