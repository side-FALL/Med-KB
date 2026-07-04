"""医学教材知识库 — JSONBin API 客户端封装

封装 JSONBin REST API 的所有 HTTP 请求逻辑，包括：
- 从 .env 读取 API 密钥和 Bin ID
- GET / POST / PUT / DELETE 四种 HTTP 方法
- 认证请求头（X-Master-Key）
- 请求超时处理（默认 10 秒）
- 请求失败重试机制（最多 3 次，指数退避）
- 请求失败降级处理（本地文件缓存）
"""

import os
import re
import sys
import json
import time
import logging
import hashlib
import tempfile
from pathlib import Path
from typing import Any, Optional

import requests
from requests.exceptions import RequestException, Timeout, ConnectionError as ReqConnectionError
from dotenv import load_dotenv

# ── 日志配置 ──────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────

JSONBIN_BASE_URL = "https://api.jsonbin.io/v3"

DEFAULT_TIMEOUT = 10          # 秒
DEFAULT_MAX_RETRIES = 3       # 最大重试次数
RETRY_BACKOFF_BASE = 1.0      # 指数退避基数（秒）

# 可重试的 HTTP 状态码
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# 本地缓存目录（降级用）— 使用平台特定的用户专属目录，避免多用户共享泄露数据
if sys.platform == "win32":
    _local_app_data = os.environ.get("LOCALAPPDATA")
    if _local_app_data:
        CACHE_DIR = Path(_local_app_data) / "med_kb" / "jsonbin"
    else:
        CACHE_DIR = Path.home() / ".cache" / "med_kb" / "jsonbin"
else:
    CACHE_DIR = Path.home() / ".cache" / "med_kb" / "jsonbin"

# 响应体大小上限（10 MB），防止恶意服务器发送巨大 JSON 导致内存耗尽
MAX_RESPONSE_SIZE = 10 * 1024 * 1024

# bin_id 合法格式：仅允许字母、数字、连字符
_BIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-]+$")


# ── 加载环境变量 ──────────────────────────────────────────

load_dotenv()


# ── 异常定义 ──────────────────────────────────────────────

class JSONBinError(Exception):
    """JSONBin 客户端基础异常。"""


class JSONBinAuthError(JSONBinError):
    """认证失败（API Key 缺失或无效）。"""


class JSONBinTimeoutError(JSONBinError):
    """请求超时。"""


class JSONBinRequestError(JSONBinError):
    """请求失败（重试耗尽后仍失败）。"""

    def __init__(self, message: str, status_code: Optional[int] = None, fallback_data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.fallback_data = fallback_data  # 降级时返回的缓存数据


# ── 本地缓存工具 ──────────────────────────────────────────

def _cache_key(bin_id: str, path: str = "") -> str:
    """生成缓存文件名（基于 bin_id + path 的哈希）。"""
    raw = f"{bin_id}:{path}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(bin_id: str, path: str = "") -> Path:
    """返回缓存文件路径。"""
    return CACHE_DIR / f"{_cache_key(bin_id, path)}.json"


def _set_file_owner_only(filepath: Path) -> None:
    """设置文件权限为仅当前用户可读写。

    在 POSIX 系统上使用 os.chmod(0o600)。
    在 Windows 上使用 win32security API 设置 ACL（如果可用）。
    """
    if sys.platform != "win32":
        try:
            os.chmod(filepath, 0o600)
        except OSError:
            pass
        return

    # Windows: 尝试使用 win32security 设置 ACL
    try:
        import win32security
        import ntsecuritycon as con

        # 获取当前用户的 SID
        username = os.environ.get("USERNAME", "")
        if not username:
            return

        domain = os.environ.get("USERDOMAIN", "")
        sid = win32security.LookupAccountName(None, f"{domain}\\{username}" if domain else username)
        if sid is None:
            return

        user_sid, _, _ = sid
        dacl = win32security.ACL()
        # 授予当前用户完全控制权限
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            con.FILE_ALL_ACCESS,
            user_sid,
        )

        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(1, dacl, 0)
        win32security.SetFileSecurity(
            str(filepath), win32security.DACL_SECURITY_INFORMATION, sd
        )
    except (ImportError, OSError, Exception):
        # win32security 不可用或设置失败，回退到 os.chmod（有限但无害）
        try:
            os.chmod(filepath, 0o600)
        except OSError:
            pass


def _write_cache(bin_id: str, path: str, data: Any) -> None:
    """将数据写入本地缓存（目录 700，文件 600）。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Windows 上 mkdir mode 无效，额外设置目录权限
        if sys.platform == "win32":
            _set_file_owner_only(CACHE_DIR)
        fp = _cache_path(bin_id, path)
        fp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _set_file_owner_only(fp)
        logger.debug("缓存已写入: %s", fp)
    except OSError as exc:
        logger.warning("写入本地缓存失败: %s", exc)


def _read_cache(bin_id: str, path: str = "") -> Optional[Any]:
    """从本地缓存读取数据，不存在则返回 None。"""
    try:
        fp = _cache_path(bin_id, path)
        if fp.exists():
            data = json.loads(fp.read_text(encoding="utf-8"))
            logger.info("从本地缓存读取数据: %s", fp)
            return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取本地缓存失败: %s", exc)
    return None


def _validate_bin_id(bin_id: str) -> str:
    """校验 bin_id 格式，仅允许字母、数字和连字符。"""
    if not bin_id or not _BIN_ID_PATTERN.match(bin_id):
        raise JSONBinRequestError(
            f"无效的 bin_id 格式: {bin_id!r}，仅允许字母、数字和连字符"
        )
    return bin_id


# ── JSONBin 客户端 ────────────────────────────────────────

class JSONBinClient:
    """JSONBin REST API 客户端。

    用法::

        client = JSONBinClient()          # 自动从 .env 读取配置
        data = client.get_record()         # 读取 Bin 数据
        client.update_record({"key": "value"})  # 更新 Bin 数据

    也支持手动传入配置::

        client = JSONBinClient(api_key="xxx", bin_id="yyy")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        bin_id: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ):
        self.api_key = api_key or os.environ.get("JSONBIN_API_KEY", "")
        self.bin_id = bin_id or os.environ.get("JSONBIN_BIN_ID", "")
        self.timeout = timeout
        self.max_retries = max_retries

        if not self.api_key:
            raise JSONBinAuthError(
                "JSONBIN_API_KEY 未配置。请在 .env 文件中设置 JSONBIN_API_KEY。"
            )
        if not self.bin_id:
            logger.warning("JSONBIN_BIN_ID 未配置，部分操作可能失败。请在 .env 中设置。")

        self._session = requests.Session()
        self._session.headers.update(self._default_headers())

    # ── 请求头 ────────────────────────────────────────────

    # 敏感请求头名称（小写），日志中需要脱敏
    _SENSITIVE_HEADERS = frozenset({
        "x-master-key", "authorization", "x-api-key", "cookie", "set-cookie",
    })

    def _default_headers(self) -> dict[str, str]:
        """构建默认请求头，包含认证信息。"""
        return {
            "X-Master-Key": self.api_key,
            "Content-Type": "application/json",
            "User-Agent": "Med-KB-JSONBinClient/1.0",
        }

    @staticmethod
    def _sanitize_headers_for_log(headers: dict[str, str]) -> dict[str, str]:
        """对请求头进行脱敏处理，用于日志输出。

        敏感头（如 X-Master-Key、Authorization）的值会被替换为 [REDACTED]。
        """
        sanitized = {}
        for key, value in headers.items():
            if key.lower() in JSONBinClient._SENSITIVE_HEADERS:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = value
        return sanitized

    # ── 核心请求方法 ──────────────────────────────────────

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_data: Any = None,
        params: Optional[dict] = None,
        extra_headers: Optional[dict[str, str]] = None,
        use_cache: bool = True,
        cache_bin_id: Optional[str] = None,
        cache_path: str = "",
    ) -> Any:
        """发送 HTTP 请求，包含重试和降级逻辑。

        Args:
            method: HTTP 方法（GET/POST/PUT/DELETE）
            url: 完整请求 URL
            json_data: 请求体 JSON 数据
            params: URL 查询参数
            extra_headers: 额外请求头
            use_cache: 是否启用本地缓存降级
            cache_bin_id: 缓存用的 bin_id（默认使用 self.bin_id）
            cache_path: 缓存用的路径标识

        Returns:
            解析后的 JSON 响应数据

        Raises:
            JSONBinTimeoutError: 请求超时（重试耗尽）
            JSONBinRequestError: 请求失败（重试耗尽）
        """
        _bin_id = cache_bin_id or self.bin_id
        headers = {**self._session.headers, **(extra_headers or {})}

        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                safe_headers = self._sanitize_headers_for_log(headers)
                logger.debug(
                    "请求 %s %s (attempt %d/%d, headers=%s)",
                    method, url, attempt, self.max_retries, safe_headers,
                )
                resp = self._session.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                    stream=True,
                )

                # 4xx / 5xx 处理
                if resp.status_code >= 400:
                    # 先消耗响应体以释放连接
                    resp.content
                    # 不可重试的客户端错误，直接抛出
                    if resp.status_code not in RETRYABLE_STATUS_CODES:
                        error_msg = self._extract_error(resp)
                        if resp.status_code == 401 or resp.status_code == 403:
                            raise JSONBinAuthError(
                                f"认证失败 ({resp.status_code}): {error_msg}"
                            )
                        raise JSONBinRequestError(
                            f"请求失败 ({resp.status_code}): {error_msg}",
                            status_code=resp.status_code,
                        )

                    # 可重试的状态码
                    logger.warning(
                        "可重试状态码 %d (attempt %d/%d): %s",
                        resp.status_code, attempt, self.max_retries, url,
                    )
                    last_exc = JSONBinRequestError(
                        f"HTTP {resp.status_code}", status_code=resp.status_code,
                    )
                else:
                    # 成功 — 流式检查响应体大小，防止内存耗尽
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                        resp.close()
                        raise JSONBinRequestError(
                            f"响应体过大 (Content-Length: {content_length} > {MAX_RESPONSE_SIZE} bytes)，已拒绝",
                            status_code=resp.status_code,
                        )

                    # 流式下载，逐块检查大小
                    chunks = []
                    downloaded = 0
                    for chunk in resp.iter_content(chunk_size=8192):
                        downloaded += len(chunk)
                        if downloaded > MAX_RESPONSE_SIZE:
                            resp.close()
                            raise JSONBinRequestError(
                                f"响应体过大 (已下载 {downloaded} bytes > {MAX_RESPONSE_SIZE} bytes)，已拒绝",
                                status_code=resp.status_code,
                            )
                        chunks.append(chunk)

                    resp.close()
                    resp._content = b"".join(chunks)

                    # 写入缓存（仅 GET 请求）
                    if method.upper() == "GET" and use_cache:
                        try:
                            _write_cache(_bin_id, cache_path, resp.json())
                        except ValueError:
                            pass
                    return resp.json()

            except (Timeout, ReqConnectionError) as exc:
                logger.warning(
                    "网络异常 (attempt %d/%d): %s — %s",
                    attempt, self.max_retries, type(exc).__name__, exc,
                )
                last_exc = exc

            except JSONBinAuthError:
                raise  # 认证错误不重试

            except JSONBinRequestError as exc:
                if exc.status_code and exc.status_code not in RETRYABLE_STATUS_CODES:
                    raise  # 不可重试的错误直接抛出
                last_exc = exc

            except RequestException as exc:
                logger.warning(
                    "请求异常 (attempt %d/%d): %s — %s",
                    attempt, self.max_retries, type(exc).__name__, exc,
                )
                last_exc = exc

            # 指数退避
            if attempt < self.max_retries:
                wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.debug("等待 %.1f 秒后重试…", wait)
                time.sleep(wait)

        # ── 重试耗尽，尝试降级 ────────────────────────────

        if use_cache and method.upper() == "GET":
            cached = _read_cache(_bin_id, cache_path)
            if cached is not None:
                logger.info("重试耗尽，使用本地缓存降级返回数据")
                return cached

        # 无缓存可用，抛出异常
        if isinstance(last_exc, (Timeout, ReqConnectionError)):
            raise JSONBinTimeoutError(
                f"请求超时（已重试 {self.max_retries} 次）: {url}"
            ) from last_exc

        raise JSONBinRequestError(
            f"请求失败（已重试 {self.max_retries} 次）: {url} — {last_exc}",
        ) from last_exc

    # ── 错误信息提取 ──────────────────────────────────────

    @staticmethod
    def _extract_error(resp: requests.Response) -> str:
        """从响应中提取可读的错误信息。"""
        try:
            body = resp.json()
            # JSONBin 错误格式: {"message": "..."} 或 {"error": "..."}
            return body.get("message") or body.get("error") or resp.text[:200]
        except (ValueError, KeyError):
            return resp.text[:200] if resp.text else f"HTTP {resp.status_code}"

    # ── 公开 API ──────────────────────────────────────────

    def get_record(
        self,
        bin_id: Optional[str] = None,
        version: Optional[str] = None,
    ) -> Any:
        """读取 Bin 中的 JSON 数据（GET）。

        Args:
            bin_id: 目标 Bin ID（默认使用初始化时的 bin_id）
            version: 指定版本（如 "latest"），默认 latest

        Returns:
            Bin 中的 record 数据（已解包 record 字段）
        """
        _bin_id = bin_id or self.bin_id
        if not _bin_id:
            raise JSONBinRequestError("bin_id 未指定，无法执行 GET 操作")
        _validate_bin_id(_bin_id)

        url = f"{JSONBIN_BASE_URL}/b/{_bin_id}"
        params = {}
        if version:
            params["v"] = version

        result = self._request(
            "GET", url, params=params or None,
            cache_bin_id=_bin_id,
        )
        # JSONBin v3 返回格式: {"record": {...}, "metadata": {...}}
        if isinstance(result, dict) and "record" in result:
            return result["record"]
        return result

    def create_record(
        self,
        data: Any,
        bin_name: Optional[str] = None,
        private: bool = True,
    ) -> Any:
        """创建新的 Bin（POST）。

        Args:
            data: 要存储的 JSON 数据
            bin_name: Bin 名称（可选）
            private: 是否私有（默认 True）

        Returns:
            创建结果，包含 metadata（含 bin_id）
        """
        url = f"{JSONBIN_BASE_URL}/b"
        extra_headers = {}
        if bin_name:
            extra_headers["X-Bin-Name"] = bin_name
        if private:
            extra_headers["X-Bin-Private"] = "true"

        return self._request(
            "POST", url, json_data=data, extra_headers=extra_headers,
            use_cache=False,
        )

    def update_record(
        self,
        data: Any,
        bin_id: Optional[str] = None,
    ) -> Any:
        """更新已有 Bin 的数据（PUT）。

        Args:
            data: 新的 JSON 数据（会覆盖原有内容）
            bin_id: 目标 Bin ID（默认使用初始化时的 bin_id）

        Returns:
            更新结果
        """
        _bin_id = bin_id or self.bin_id
        if not _bin_id:
            raise JSONBinRequestError("bin_id 未指定，无法执行 PUT 操作")
        _validate_bin_id(_bin_id)

        url = f"{JSONBIN_BASE_URL}/b/{_bin_id}"
        result = self._request(
            "PUT", url, json_data=data,
            use_cache=False,
        )
        # 更新成功后清除该 bin 的 GET 缓存
        cache_file = _cache_path(_bin_id)
        if cache_file.exists():
            try:
                cache_file.unlink()
                logger.debug("已清除缓存: %s", cache_file)
            except OSError:
                pass
        return result

    def delete_record(self, bin_id: Optional[str] = None) -> Any:
        """删除一个 Bin（DELETE）。

        Args:
            bin_id: 目标 Bin ID（默认使用初始化时的 bin_id）

        Returns:
            删除结果
        """
        _bin_id = bin_id or self.bin_id
        if not _bin_id:
            raise JSONBinRequestError("bin_id 未指定，无法执行 DELETE 操作")
        _validate_bin_id(_bin_id)

        url = f"{JSONBIN_BASE_URL}/b/{_bin_id}"
        result = self._request(
            "DELETE", url, use_cache=False,
        )
        # 删除成功后清除缓存
        cache_file = _cache_path(_bin_id)
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError:
                pass
        return result

    # ── 便捷方法 ──────────────────────────────────────────

    def get_user_data(self, username: str) -> Optional[dict]:
        """读取指定用户的数据（从 Bin record 中提取）。

        约定 Bin 中存储格式: {"users": {"alice": {...}, "bob": {...}}}

        Args:
            username: 用户名

        Returns:
            用户数据字典，不存在返回 None
        """
        record = self.get_record()
        if isinstance(record, dict):
            users = record.get("users", {})
            return users.get(username)
        return None

    def update_user_data(self, username: str, user_data: dict, max_retries: int = 3) -> Any:
        """更新指定用户的数据（带乐观锁，防止并发覆盖）。

        通过 record 中的 ``_version`` 字段实现乐观锁：
        读取时记录版本号，写入时比对版本号，若已被其他请求修改则自动重试。

        Args:
            username: 用户名
            user_data: 用户数据字典
            max_retries: 乐观锁冲突时的最大重试次数（默认 3）

        Returns:
            更新结果
        """
        for attempt in range(1, max_retries + 1):
            try:
                record = self.get_record()
            except JSONBinError:
                record = {}

            if not isinstance(record, dict):
                record = {}

            # 乐观锁：读取当前版本号
            current_version = record.get("_version", 0)

            users = record.get("users", {})
            users[username] = user_data
            record["users"] = users
            record["_version"] = current_version + 1

            try:
                result = self.update_record(record)
                return result
            except JSONBinRequestError:
                if attempt < max_retries:
                    logger.warning(
                        "update_user_data 乐观锁冲突 (attempt %d/%d)，重试中…",
                        attempt, max_retries,
                    )
                    time.sleep(0.2 * attempt)
                    continue
                raise

    def delete_user_data(self, username: str) -> Any:
        """删除指定用户的数据。

        Args:
            username: 用户名

        Returns:
            更新结果
        """
        try:
            record = self.get_record()
        except JSONBinError:
            record = {}

        if not isinstance(record, dict):
            record = {}

        users = record.get("users", {})
        users.pop(username, None)
        record["users"] = users

        return self.update_record(record)

    def check_user_exists(self, username: str) -> bool:
        """检查用户是否已存在。

        Args:
            username: 用户名

        Returns:
            True 表示用户已存在
        """
        return self.get_user_data(username) is not None

    # ── 上下文管理 ────────────────────────────────────────

    def close(self) -> None:
        """关闭底层 HTTP 会话。"""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self) -> str:
        return (
            f"JSONBinClient(bin_id={self.bin_id!r}, "
            f"timeout={self.timeout}, max_retries={self.max_retries})"
        )
