"""医学教材知识库 — 数据库模型定义与验证

设计分层数据存储结构，用于 JSONBin 持久化：
- 用户数据层：用户基本信息、学习记录、偏好设置
- 配置数据层：应用配置、功能开关、版本信息
- 日志数据层：操作日志、错误日志、使用统计

数据版本标记、扩展字段预留、必填校验、格式验证、大小预估（<10KB/用户）。
"""

import re
import json
import logging
import ipaddress
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────

SCHEMA_VERSION = "1.0.0"
"""数据模型版本号，用于后续升级迁移。"""

MAX_USER_DATA_SIZE = 10 * 1024  # 10 KB
"""单用户数据大小上限（字节）。"""

MAX_LEARNING_RECORDS = 200
"""学习记录最大条数，超出后淘汰最早的记录。"""

MAX_OPERATION_LOGS = 100
"""操作日志最大条数。"""

MAX_ERROR_LOGS = 50
"""错误日志最大条数。"""

EXPIRE_DAYS = 30
"""用户数据过期天数。"""

EXPIRE_WARN_DAYS = 7
"""过期前提醒天数。"""

MAX_EXTRA_DATA_SIZE = 512
"""extra_data 字段序列化后的最大字节数。"""

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
"""用户名格式：3-20 位字母、数字或下划线。"""

RESERVED_USERNAMES = frozenset({
    # 系统保留账户
    "admin", "root", "system", "superuser", "sysadmin",
    # 服务/机器人账户
    "bot", "service", "daemon", "nobody", "null",
    # 通用保留
    "administrator", "moderator", "operator", "support",
    "webmaster", "postmaster", "hostmaster",
})
"""保留用户名黑名单（大小写不敏感匹配），防止钓鱼攻击。"""

EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
"""邮箱格式校验。"""

ISO_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)
"""ISO 8601 日期时间格式校验。"""

VALID_SOURCE_TYPES = ("textbook", "search", "chat", "qa", "quiz", "compare", "case", "agent")
"""学习记录来源类型白名单。"""

VALID_ACTIONS = (
    "login", "logout", "register", "search", "query", "update_prefs",
    "change_role", "reset_password", "delete_user",
    "disable_user", "enable_user",
)
"""操作日志动作类型白名单。"""

VALID_ERROR_TYPES = ("validation", "auth", "network", "internal")
"""错误日志错误类型白名单。"""

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
]
"""敏感信息正则模式列表（密码、API Key、邮箱、IP 地址）。"""


# ── 验证异常 ──────────────────────────────────────────────

class ValidationError(Exception):
    """数据验证失败时抛出。"""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"[{field}] {message}")


# ── 工具函数 ──────────────────────────────────────────────

def utcnow_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_iso_datetime(value: str, field_name: str = "datetime") -> str:
    """校验 ISO 8601 日期时间字符串，合法则返回原值，否则抛出 ValidationError。"""
    if not isinstance(value, str):
        raise ValidationError(field_name, f"应为字符串，实际为 {type(value).__name__}")
    if not ISO_DATETIME_PATTERN.match(value):
        raise ValidationError(field_name, f"日期格式不合法: {value!r}，应为 ISO 8601 格式")
    return value


def validate_required(value: Any, field_name: str) -> Any:
    """校验必填字段不为 None 或空字符串。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(field_name, "该字段为必填项，不能为空")
    return value


def estimate_json_size(data: Any) -> int:
    """估算数据序列化为 JSON 后的字节数（UTF-8 编码）。"""
    return len(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def validate_extra_data(data: dict, field_name: str = "extra_data") -> None:
    """校验 extra_data 字段大小不超过限制。"""
    size = estimate_json_size(data)
    if size > MAX_EXTRA_DATA_SIZE:
        raise ValidationError(
            field_name,
            f"扩展字段大小 ({size} bytes) 超过上限 ({MAX_EXTRA_DATA_SIZE} bytes)",
        )


def validate_ip_address(value: str, field_name: str = "ip_address") -> None:
    """校验 IP 地址格式（支持 IPv4/IPv6）。"""
    if not value:
        return
    try:
        ipaddress.ip_address(value)
    except ValueError:
        raise ValidationError(
            field_name,
            f"IP 地址格式不合法: {value!r}",
        )


def _sanitize_sensitive_info(text: str) -> str:
    """过滤文本中的敏感信息（密码、API Key、邮箱、IP 地址）。"""
    sanitized = text
    for pattern in SENSITIVE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


# ── 用户数据层 ────────────────────────────────────────────

@dataclass
class UserProfile:
    """用户基本信息。"""

    username: str = ""
    """用户名（必填，3-20 位字母/数字/下划线）。"""

    password_hash: str = ""
    """密码哈希值（必填，bcrypt 格式 $2b$ 开头 60 字符，或 SHA-256 格式 64 位十六进制）。"""

    email: str = ""
    """邮箱（可选）。"""

    role: str = "user"
    """角色：user / admin / guest。"""

    created_at: str = ""
    """注册时间（ISO 8601）。"""

    last_active_at: str = ""
    """最后活跃时间（用于过期判断，ISO 8601）。"""

    def validate(self) -> None:
        """校验必填字段和格式。"""
        validate_required(self.username, "username")
        # Unicode NFKC 规范化：防止同形字符攻击（如 Cyrillic 'а' vs Latin 'a'）
        normalized_username = unicodedata.normalize("NFKC", self.username)
        if not USERNAME_PATTERN.match(normalized_username):
            raise ValidationError(
                "username",
                f"用户名格式不合法: {self.username!r}，需 3-20 位字母、数字或下划线",
            )
        # 保留用户名检查（大小写不敏感）
        if normalized_username.lower() in RESERVED_USERNAMES:
            raise ValidationError(
                "username",
                f"用户名 {self.username!r} 为系统保留名称，请选择其他名称",
            )
        validate_required(self.password_hash, "password_hash")
        # 兼容 bcrypt（$2b$/$2a$/$2y$ 开头，60 字符）和旧版 SHA-256（64 位十六进制）
        is_bcrypt = (
            self.password_hash.startswith(("$2b$", "$2a$", "$2y$"))
            and len(self.password_hash) == 60
        )
        is_sha256 = (
            len(self.password_hash) == 64
            and all(c in "0123456789abcdef" for c in self.password_hash)
        )
        if not is_bcrypt and not is_sha256:
            raise ValidationError(
                "password_hash",
                "密码哈希格式不合法，应为 bcrypt 哈希（$2b$ 开头，60 字符）"
                "或 SHA-256 哈希（64 位十六进制字符串）",
            )
        if self.email:
            if not EMAIL_PATTERN.match(self.email):
                raise ValidationError("email", f"邮箱格式不合法: {self.email!r}")
        if self.created_at:
            validate_iso_datetime(self.created_at, "created_at")
        if self.role not in ("user", "admin", "guest", "super_admin"):
            raise ValidationError("role", f"角色值不合法: {self.role!r}")


@dataclass
class LearningRecord:
    """单条学习记录。"""

    topic: str = ""
    """学习主题。"""

    query: str = ""
    """用户提问内容。"""

    timestamp: str = ""
    """学习时间（ISO 8601）。"""

    duration_seconds: int = 0
    """学习时长（秒）。"""

    source_type: str = ""
    """来源类型：textbook / search / chat。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        validate_required(self.topic, "learning_record.topic")
        if self.timestamp:
            validate_iso_datetime(self.timestamp, "learning_record.timestamp")
        if self.duration_seconds < 0:
            raise ValidationError(
                "learning_record.duration_seconds", "学习时长不能为负数"
            )
        if self.source_type and self.source_type not in VALID_SOURCE_TYPES:
            raise ValidationError(
                "learning_record.source_type",
                f"来源类型不合法: {self.source_type!r}，"
                f"允许值: {', '.join(VALID_SOURCE_TYPES)}",
            )
        if self.extra_data:
            validate_extra_data(self.extra_data, "learning_record.extra_data")


@dataclass
class UserPreferences:
    """用户偏好设置。"""

    theme: str = "light"
    """主题：light / dark。"""

    language: str = "zh-CN"
    """界面语言。"""

    default_model: str = ""
    """默认使用的模型。"""

    font_size: int = 14
    """字体大小。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        if self.theme not in ("light", "dark"):
            raise ValidationError("preferences.theme", f"主题值不合法: {self.theme!r}")
        if not (10 <= self.font_size <= 32):
            raise ValidationError(
                "preferences.font_size", f"字体大小需在 10-32 之间，当前: {self.font_size}"
            )
        if self.extra_data:
            validate_extra_data(self.extra_data, "preferences.extra_data")


@dataclass
class UserData:
    """单个用户的完整数据结构。

    大小预估：空用户 ~600B，200 条学习记录 ~4KB，
    偏好设置 ~300B，日志 ~2KB，合计 < 10KB。
    """

    profile: dict = field(default_factory=dict)
    """用户基本信息，结构同 UserProfile。"""

    learning_records: list = field(default_factory=list)
    """学习记录列表，最大 MAX_LEARNING_RECORDS 条。"""

    preferences: dict = field(default_factory=dict)
    """偏好设置，结构同 UserPreferences。"""

    stats: dict = field(default_factory=lambda: {
        "total_queries": 0,
        "total_learning_time": 0,
        "login_count": 0,
        "favorite_topics": [],
    })
    """使用统计。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段，预留未来功能。"""

    def validate(self) -> None:
        """校验整个用户数据。"""
        # 校验 profile
        profile = UserProfile(**{
            k: v for k, v in self.profile.items()
            if k in UserProfile.__dataclass_fields__
        })
        profile.validate()

        # 校验学习记录
        if len(self.learning_records) > MAX_LEARNING_RECORDS:
            raise ValidationError(
                "learning_records",
                f"学习记录数 ({len(self.learning_records)}) 超过上限 ({MAX_LEARNING_RECORDS})",
            )
        for i, rec in enumerate(self.learning_records):
            if isinstance(rec, dict):
                record = LearningRecord(**{
                    k: v for k, v in rec.items()
                    if k in LearningRecord.__dataclass_fields__
                })
                record.validate()

        # 校验偏好
        if self.preferences:
            prefs = UserPreferences(**{
                k: v for k, v in self.preferences.items()
                if k in UserPreferences.__dataclass_fields__
            })
            prefs.validate()

        # 校验 extra_data
        if self.extra_data:
            validate_extra_data(self.extra_data, "user_data.extra_data")

        # 校验大小
        size = estimate_json_size(self.to_dict())
        if size > MAX_USER_DATA_SIZE:
            raise ValidationError(
                "user_data",
                f"用户数据大小 ({size} bytes) 超过上限 ({MAX_USER_DATA_SIZE} bytes)",
            )

    def to_dict(self) -> dict:
        """转为字典。"""
        return {
            "profile": self.profile,
            "learning_records": self.learning_records,
            "preferences": self.preferences,
            "stats": self.stats,
            "extra_data": self.extra_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserData":
        """从字典创建实例。"""
        return cls(
            profile=data.get("profile", {}),
            learning_records=data.get("learning_records", []),
            preferences=data.get("preferences", {}),
            stats=data.get("stats", {
                "total_queries": 0,
                "total_learning_time": 0,
                "login_count": 0,
                "favorite_topics": [],
            }),
            extra_data=data.get("extra_data", {}),
        )


# ── 配置数据层 ────────────────────────────────────────────

@dataclass
class AppConfig:
    """应用配置。"""

    app_name: str = "Med-KB"
    """应用名称。"""

    schema_version: str = SCHEMA_VERSION
    """数据模型版本号。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        validate_required(self.app_name, "config.app_name")
        validate_required(self.schema_version, "config.schema_version")
        if self.extra_data:
            validate_extra_data(self.extra_data, "config.extra_data")


@dataclass
class FeatureFlags:
    """功能开关。"""

    enable_registration: bool = True
    """是否开放注册。"""

    enable_guest_mode: bool = True
    """是否允许游客模式。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        """功能开关均为 bool，仅校验 extra_data 大小。"""
        if self.extra_data:
            validate_extra_data(self.extra_data, "feature_flags.extra_data")


@dataclass
class ConfigData:
    """配置数据层完整结构。"""

    app_config: dict = field(default_factory=dict)
    """应用配置。"""

    feature_flags: dict = field(default_factory=dict)
    """功能开关。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        if self.app_config:
            cfg = AppConfig(**{
                k: v for k, v in self.app_config.items()
                if k in AppConfig.__dataclass_fields__
            })
            cfg.validate()
        if self.extra_data:
            validate_extra_data(self.extra_data, "config_data.extra_data")

    def to_dict(self) -> dict:
        return {
            "app_config": self.app_config,
            "feature_flags": self.feature_flags,
            "extra_data": self.extra_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConfigData":
        return cls(
            app_config=data.get("app_config", {}),
            feature_flags=data.get("feature_flags", {}),
            extra_data=data.get("extra_data", {}),
        )


# ── 日志数据层 ────────────────────────────────────────────

@dataclass
class OperationLog:
    """操作日志条目。"""

    timestamp: str = ""
    """操作时间（ISO 8601）。"""

    username: str = ""
    """操作用户。"""

    action: str = ""
    """操作类型：login / logout / register / search / query / update_prefs。"""

    detail: str = ""
    """操作详情。"""

    ip_address: str = ""
    """来源 IP（可选）。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        validate_required(self.action, "operation_log.action")
        if self.action and self.action not in VALID_ACTIONS:
            raise ValidationError(
                "operation_log.action",
                f"操作类型不合法: {self.action!r}，"
                f"允许值: {', '.join(VALID_ACTIONS)}",
            )
        if self.timestamp:
            validate_iso_datetime(self.timestamp, "operation_log.timestamp")
        if self.ip_address:
            validate_ip_address(self.ip_address, "operation_log.ip_address")
        if self.extra_data:
            validate_extra_data(self.extra_data, "operation_log.extra_data")


@dataclass
class ErrorLog:
    """错误日志条目。"""

    timestamp: str = ""
    """错误发生时间（ISO 8601）。"""

    error_type: str = ""
    """错误类型：validation / auth / network / internal。"""

    message: str = ""
    """错误信息。"""

    username: str = ""
    """关联用户（可选）。"""

    stack_trace: str = ""
    """堆栈信息（可选，截断存储）。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        validate_required(self.error_type, "error_log.error_type")
        if self.error_type and self.error_type not in VALID_ERROR_TYPES:
            raise ValidationError(
                "error_log.error_type",
                f"错误类型不合法: {self.error_type!r}，"
                f"允许值: {', '.join(VALID_ERROR_TYPES)}",
            )
        validate_required(self.message, "error_log.message")
        if self.timestamp:
            validate_iso_datetime(self.timestamp, "error_log.timestamp")
        if self.extra_data:
            validate_extra_data(self.extra_data, "error_log.extra_data")


@dataclass
class LogData:
    """日志数据层完整结构。

    日志存储在用户数据之外（系统级），不受 10KB/用户限制。
    """

    operation_logs: list = field(default_factory=list)
    """操作日志列表，最大 MAX_OPERATION_LOGS 条。"""

    error_logs: list = field(default_factory=list)
    """错误日志列表，最大 MAX_ERROR_LOGS 条。"""

    usage_stats: dict = field(default_factory=lambda: {
        "total_users": 0,
    })
    """全局使用统计。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段。"""

    def validate(self) -> None:
        if len(self.operation_logs) > MAX_OPERATION_LOGS:
            raise ValidationError(
                "operation_logs",
                f"操作日志数 ({len(self.operation_logs)}) 超过上限 ({MAX_OPERATION_LOGS})",
            )
        if len(self.error_logs) > MAX_ERROR_LOGS:
            raise ValidationError(
                "error_logs",
                f"错误日志数 ({len(self.error_logs)}) 超过上限 ({MAX_ERROR_LOGS})",
            )
        for log in self.operation_logs:
            if isinstance(log, dict):
                op = OperationLog(**{
                    k: v for k, v in log.items()
                    if k in OperationLog.__dataclass_fields__
                })
                op.validate()
        for log in self.error_logs:
            if isinstance(log, dict):
                err = ErrorLog(**{
                    k: v for k, v in log.items()
                    if k in ErrorLog.__dataclass_fields__
                })
                err.validate()
        if self.extra_data:
            validate_extra_data(self.extra_data, "log_data.extra_data")

    def to_dict(self) -> dict:
        return {
            "operation_logs": self.operation_logs,
            "error_logs": self.error_logs,
            "usage_stats": self.usage_stats,
            "extra_data": self.extra_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LogData":
        return cls(
            operation_logs=data.get("operation_logs", []),
            error_logs=data.get("error_logs", []),
            usage_stats=data.get("usage_stats", {
                "total_users": 0,
            }),
            extra_data=data.get("extra_data", {}),
        )


# ── 根数据结构 ────────────────────────────────────────────

@dataclass
class RootRecord:
    """JSONBin 中存储的根数据结构。

    与 jsonbin_client.py 中约定的格式一致::

        {
            "_version": "1.0.0",
            "_schema_version": 1,
            "users": { "alice": {...}, "bob": {...} },
            "config": { ... },
            "logs": { ... },
            "extra_data": {}
        }
    """

    schema_version: str = SCHEMA_VERSION
    """数据模型版本号（语义化版本）。"""

    schema_number: int = 1
    """数据模型数字版本号（整数，便于迁移脚本判断升降级）。"""

    users: dict = field(default_factory=dict)
    """用户数据字典，key 为用户名，value 为 UserData.to_dict()。"""

    config: dict = field(default_factory=dict)
    """配置数据，结构同 ConfigData.to_dict()。"""

    logs: dict = field(default_factory=dict)
    """日志数据，结构同 LogData.to_dict()。"""

    extra_data: dict = field(default_factory=dict)
    """扩展字段，预留未来功能。"""

    def validate(self) -> None:
        """校验整个根数据。"""
        validate_required(self.schema_version, "schema_version")

        # 校验每个用户数据
        for username, user_dict in self.users.items():
            if isinstance(user_dict, dict):
                user_data = UserData.from_dict(user_dict)
                try:
                    user_data.validate()
                except ValidationError as exc:
                    raise ValidationError(
                        f"users.{username}",
                        f"用户 {username!r} 数据校验失败: {exc.message}",
                    ) from exc

        # 校验配置
        if self.config:
            config_data = ConfigData.from_dict(self.config)
            config_data.validate()

        # 校验日志
        if self.logs:
            log_data = LogData.from_dict(self.logs)
            log_data.validate()

        # 校验 extra_data
        if self.extra_data:
            validate_extra_data(self.extra_data, "root_record.extra_data")

    def to_dict(self) -> dict:
        return {
            "_version": self.schema_version,
            "_schema_version": self.schema_number,
            "users": self.users,
            "config": self.config,
            "logs": self.logs,
            "extra_data": self.extra_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RootRecord":
        return cls(
            schema_version=data.get("_version", SCHEMA_VERSION),
            schema_number=data.get("_schema_version", 1),
            users=data.get("users", {}),
            config=data.get("config", {}),
            logs=data.get("logs", {}),
            extra_data=data.get("extra_data", {}),
        )


# ── 工厂函数 ──────────────────────────────────────────────

def create_default_user(
    username: str,
    password_hash: str,
    email: str = "",
    role: str = "user",
) -> dict:
    """创建默认用户数据（注册时使用）。

    Args:
        username: 用户名
        password_hash: bcrypt 密码哈希（推荐）或 SHA-256 哈希（兼容旧版）
        email: 邮箱（可选）
        role: 角色（默认 user）

    Returns:
        用户数据字典，可直接存入 RootRecord.users

    Raises:
        ValidationError: 字段校验失败
    """
    now = utcnow_iso()
    profile = {
        "username": username,
        "password_hash": password_hash,
        "email": email,
        "role": role,
        "created_at": now,
        "last_active_at": now,
    }
    preferences = {
        "theme": "light",
        "language": "zh-CN",
        "default_model": "",
        "font_size": 14,
        "extra_data": {},
    }
    stats = {
        "total_queries": 0,
        "total_learning_time": 0,
        "login_count": 1,
        "favorite_topics": [],
    }
    user_data = UserData(
        profile=profile,
        learning_records=[],
        preferences=preferences,
        stats=stats,
        extra_data={},
    )
    user_data.validate()
    return user_data.to_dict()


def create_default_config() -> dict:
    """创建默认应用配置。

    Returns:
        配置数据字典。
    """
    config = ConfigData(
        app_config={
            "app_name": "Med-KB",
            "schema_version": SCHEMA_VERSION,
            "extra_data": {},
        },
        feature_flags={
            "enable_registration": True,
            "enable_guest_mode": True,
            "extra_data": {},
        },
        extra_data={},
    )
    config.validate()
    return config.to_dict()


def create_default_logs() -> dict:
    """创建默认日志结构。

    Returns:
        日志数据字典。
    """
    logs = LogData(
        operation_logs=[],
        error_logs=[],
        usage_stats={
            "total_users": 0,
        },
        extra_data={},
    )
    return logs.to_dict()


def create_default_root() -> dict:
    """创建完整的默认根数据结构（初始化 JSONBin 时使用）。

    Returns:
        根数据字典，可直接存入 JSONBin。
    """
    root = RootRecord(
        schema_version=SCHEMA_VERSION,
        schema_number=1,
        users={},
        config=create_default_config(),
        logs=create_default_logs(),
        extra_data={},
    )
    return root.to_dict()


# ── 辅助操作 ──────────────────────────────────────────────

def add_learning_record(
    user_data: dict,
    topic: str,
    query: str = "",
    source_type: str = "chat",
    duration_seconds: int = 0,
    extra_data: Optional[dict] = None,
) -> dict:
    """向用户数据中添加一条学习记录。

    自动裁剪超出上限的旧记录，并更新统计信息。

    Args:
        user_data: 用户数据字典
        topic: 学习主题
        query: 用户提问
        source_type: 来源类型
        duration_seconds: 学习时长
        extra_data: 扩展数据

    Returns:
        更新后的用户数据字典

    Raises:
        ValidationError: 记录校验失败
    """
    record = {
        "topic": topic,
        "query": query,
        "timestamp": utcnow_iso(),
        "duration_seconds": duration_seconds,
        "source_type": source_type,
        "extra_data": extra_data or {},
    }
    rec = LearningRecord(**{
        k: v for k, v in record.items()
        if k in LearningRecord.__dataclass_fields__
    })
    rec.validate()

    records = user_data.get("learning_records", [])
    records.append(record)

    # 裁剪：保留最新的 MAX_LEARNING_RECORDS 条
    if len(records) > MAX_LEARNING_RECORDS:
        records = records[-MAX_LEARNING_RECORDS:]

    user_data["learning_records"] = records

    # 更新统计
    stats = user_data.get("stats", {})
    stats["total_queries"] = stats.get("total_queries", 0) + 1
    stats["total_learning_time"] = stats.get("total_learning_time", 0) + duration_seconds
    fav = stats.get("favorite_topics", [])
    if topic and topic not in fav:
        fav.append(topic)
        if len(fav) > 20:
            fav = fav[-20:]
    stats["favorite_topics"] = fav
    user_data["stats"] = stats

    # 更新活跃时间
    profile = user_data.get("profile", {})
    profile["last_active_at"] = utcnow_iso()
    user_data["profile"] = profile

    # 大小预检查：确保添加记录后不超过 10KB 限制
    size = estimate_json_size(user_data)
    if size > MAX_USER_DATA_SIZE:
        # 回滚：移除刚添加的记录，恢复统计
        records = user_data.get("learning_records", [])
        if records and records[-1] is record:
            records.pop()
        stats["total_queries"] = stats.get("total_queries", 0) - 1
        stats["total_learning_time"] = stats.get("total_learning_time", 0) - duration_seconds
        if topic and topic in fav:
            fav.remove(topic)
        raise ValidationError(
            "user_data",
            f"添加学习记录后数据大小 ({size} bytes) 超过上限 ({MAX_USER_DATA_SIZE} bytes)",
        )

    return user_data


def add_operation_log(
    log_data: dict,
    action: str,
    username: str = "",
    detail: str = "",
    ip_address: str = "",
    extra_data: Optional[dict] = None,
) -> dict:
    """向日志数据中添加一条操作日志。

    Args:
        log_data: 日志数据字典
        action: 操作类型
        username: 操作用户
        detail: 操作详情
        ip_address: 来源 IP
        extra_data: 扩展数据（如管理员操作的 target 目标用户）

    Returns:
        更新后的日志数据字典
    """
    entry = {
        "timestamp": utcnow_iso(),
        "username": username,
        "action": action,
        "detail": detail,
        "ip_address": ip_address,
        "extra_data": extra_data or {},
    }
    op = OperationLog(**{
        k: v for k, v in entry.items()
        if k in OperationLog.__dataclass_fields__
    })
    op.validate()

    logs = log_data.get("operation_logs", [])
    logs.append(entry)
    if len(logs) > MAX_OPERATION_LOGS:
        logs = logs[-MAX_OPERATION_LOGS:]
    log_data["operation_logs"] = logs

    return log_data


def add_error_log(
    log_data: dict,
    error_type: str,
    message: str,
    username: str = "",
    stack_trace: str = "",
) -> dict:
    """向日志数据中添加一条错误日志。

    Args:
        log_data: 日志数据字典
        error_type: 错误类型
        message: 错误信息
        username: 关联用户
        stack_trace: 堆栈信息

    Returns:
        更新后的日志数据字典
    """
    # 过滤敏感信息后再截断存储
    sanitized_message = _sanitize_sensitive_info(message)[:500]
    sanitized_trace = _sanitize_sensitive_info(stack_trace)[:1000]

    entry = {
        "timestamp": utcnow_iso(),
        "error_type": error_type,
        "message": sanitized_message,
        "username": username,
        "stack_trace": sanitized_trace,
        "extra_data": {},
    }
    err = ErrorLog(**{
        k: v for k, v in entry.items()
        if k in ErrorLog.__dataclass_fields__
    })
    err.validate()

    logs = log_data.get("error_logs", [])
    logs.append(entry)
    if len(logs) > MAX_ERROR_LOGS:
        logs = logs[-MAX_ERROR_LOGS:]
    log_data["error_logs"] = logs

    return log_data


def check_expiry(user_data: dict) -> dict:
    """检查用户数据过期状态。

    Returns:
        包含过期信息的字典::

            {
                "is_expired": bool,       # 是否已过期
                "is_warning": bool,       # 是否即将过期（需提醒）
                "days_remaining": int,    # 剩余天数（负数表示已过期）
                "last_active_at": str,    # 最后活跃时间
            }
    """
    profile = user_data.get("profile", {})
    last_active = profile.get("last_active_at", "")

    if not last_active:
        return {
            "is_expired": True,
            "is_warning": False,
            "days_remaining": -1,
            "last_active_at": "",
        }

    try:
        last_dt = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = (now - last_dt).days
    except (ValueError, TypeError):
        return {
            "is_expired": True,
            "is_warning": False,
            "days_remaining": -1,
            "last_active_at": last_active,
        }

    return {
        "is_expired": delta >= EXPIRE_DAYS,
        "is_warning": EXPIRE_WARN_DAYS <= delta < EXPIRE_DAYS,
        "days_remaining": EXPIRE_DAYS - delta,
        "last_active_at": last_active,
    }


def estimate_user_data_size(user_data: dict) -> dict:
    """估算用户数据各部分大小。

    Returns:
        大小估算字典::

            {
                "profile_bytes": int,
                "learning_records_bytes": int,
                "preferences_bytes": int,
                "stats_bytes": int,
                "extra_data_bytes": int,
                "total_bytes": int,
                "total_kb": float,
                "within_limit": bool,
            }
    """
    parts = {
        "profile_bytes": estimate_json_size(user_data.get("profile", {})),
        "learning_records_bytes": estimate_json_size(user_data.get("learning_records", [])),
        "preferences_bytes": estimate_json_size(user_data.get("preferences", {})),
        "stats_bytes": estimate_json_size(user_data.get("stats", {})),
        "extra_data_bytes": estimate_json_size(user_data.get("extra_data", {})),
    }
    total = sum(parts.values())
    parts["total_bytes"] = total
    parts["total_kb"] = round(total / 1024, 2)
    parts["within_limit"] = total <= MAX_USER_DATA_SIZE
    return parts
