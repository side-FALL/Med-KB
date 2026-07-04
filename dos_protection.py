"""医学教材知识库 — DOS 防护机制

实现基于滑动窗口算法的请求频率限制，防止恶意攻击。

核心特性：
- 滑动窗口算法：精确到秒级的请求频率控制
- 每用户每分钟最多 30 次请求
- 白名单机制：管理员或特定 IP 不受限制
- 结构化日志：记录所有限流事件
- 与 Streamlit session_state 深度集成

依赖：无外部依赖（纯标准库 + Streamlit）
"""

import logging
import re
import secrets
import threading
import time
from collections import deque
from typing import Optional, Set

import streamlit as st

logger = logging.getLogger(__name__)


# ── 常量 ─────────────────────────────────────────────────

DEFAULT_MAX_REQUESTS = 30        # 每分钟最大请求数
DEFAULT_WINDOW_SECONDS = 60      # 滑动窗口大小（秒）
CLEANUP_INTERVAL = 100           # 每 N 次请求执行一次过期清理

# 控制字符 + 结构化日志特殊字符正则（防止日志注入）
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x1f\x7f"\\]')


def _sanitize_log_value(value: str) -> str:
    """过滤用户输入中的控制字符，防止日志注入。"""
    return _CONTROL_CHAR_RE.sub("_", value)


# ── 滑动窗口限流器（内存级） ─────────────────────────────

class SlidingWindowRateLimiter:
    """基于滑动窗口算法的请求频率限制器。

    算法原理：
    - 为每个用户维护一个时间戳队列（deque）
    - 每次请求时，先清除窗口外的过期时间戳
    - 检查窗口内剩余时间戳数量是否超过阈值
    - 未超限则记录当前时间戳并放行

    时间复杂度：O(k)，k 为窗口内请求数（通常 ≤ 30）
    空间复杂度：O(k)，每个用户仅存储窗口内的时间戳
    """

    def __init__(
        self,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        # {user_key: deque(timestamps)}
        self._requests: dict[str, deque] = {}
        self._call_count = 0  # 用于定期清理

    def check(
        self,
        user_key: str,
        max_requests: Optional[int] = None,
        window_seconds: Optional[int] = None,
    ) -> tuple[bool, dict]:
        """检查请求是否被允许。

        Args:
            user_key: 用户标识（用户名或 IP）
            max_requests: 窗口内最大请求数（None 则使用实例默认值）
            window_seconds: 窗口大小秒数（None 则使用实例默认值）

        Returns:
            (allowed, info) 元组：
            - allowed: True 表示放行，False 表示拦截
            - info: 包含 remaining（剩余配额）、retry_after（重试等待秒数）、
                     current_count（当前窗口内请求数）
        """
        effective_max = max_requests if max_requests is not None else self._max_requests
        effective_window = window_seconds if window_seconds is not None else self._window_seconds

        with self._lock:
            now = time.time()
            cutoff = now - effective_window

            # 获取或创建该用户的队列
            if user_key not in self._requests:
                self._requests[user_key] = deque()

            queue = self._requests[user_key]

            # 清除窗口外的过期时间戳
            while queue and queue[0] <= cutoff:
                queue.popleft()

            current_count = len(queue)

            if current_count >= effective_max:
                # 计算需要等待的时间：最早的时间戳过期时间
                retry_after = queue[0] - cutoff if queue else effective_window
                retry_after = max(1, int(retry_after) + 1)  # 向上取整，至少等 1 秒
                return False, {
                    "remaining": 0,
                    "retry_after": retry_after,
                    "current_count": current_count,
                    "limit": effective_max,
                    "window_seconds": effective_window,
                }

            # 放行：记录当前请求时间戳
            queue.append(now)
            remaining = effective_max - current_count - 1

            # 定期清理无用的空队列
            self._call_count += 1
            if self._call_count % CLEANUP_INTERVAL == 0:
                self._cleanup()

            return True, {
                "remaining": remaining,
                "retry_after": 0,
                "current_count": current_count + 1,
                "limit": effective_max,
                "window_seconds": effective_window,
            }

    def get_status(self, user_key: str) -> dict:
        """获取用户当前的限流状态（不消耗配额）。"""
        with self._lock:
            now = time.time()
            cutoff = now - self._window_seconds

            queue = self._requests.get(user_key)
            if not queue:
                return {
                    "remaining": self._max_requests,
                    "current_count": 0,
                    "limit": self._max_requests,
                }

            # 清除过期时间戳
            while queue and queue[0] <= cutoff:
                queue.popleft()

            current_count = len(queue)
            return {
                "remaining": max(0, self._max_requests - current_count),
                "current_count": current_count,
                "limit": self._max_requests,
            }

    def reset(self, user_key: str) -> None:
        """重置指定用户的限流记录（用于登录成功后清除）。"""
        with self._lock:
            self._requests.pop(user_key, None)

    def _cleanup(self) -> None:
        """清除所有过期用户的队列，释放内存。（调用方须持有锁）"""
        now = time.time()
        cutoff = now - self._window_seconds
        expired_keys = [
            key for key, queue in self._requests.items()
            if not queue or queue[-1] <= cutoff
        ]
        for key in expired_keys:
            del self._requests[key]
        if expired_keys:
            logger.debug("DOS 防护：清理 %d 个过期用户队列", len(expired_keys))


# ── 全局单例 ─────────────────────────────────────────────

_rate_limiter = SlidingWindowRateLimiter()


# ── 白名单管理 ───────────────────────────────────────────

class WhitelistManager:
    """白名单管理器。

    支持：
    - IP 地址白名单（精确匹配）
    - 用户名白名单（精确匹配，大小写不敏感）
    - 动态添加/移除
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ips: Set[str] = set()
        self._usernames: Set[str] = set()  # 存储小写形式

    def add_ip(self, ip: str) -> None:
        """添加 IP 到白名单。"""
        with self._lock:
            self._ips.add(ip.strip())

    def remove_ip(self, ip: str) -> None:
        """从白名单移除 IP。"""
        with self._lock:
            self._ips.discard(ip.strip())

    def add_username(self, username: str) -> None:
        """添加用户名到白名单（大小写不敏感）。"""
        with self._lock:
            self._usernames.add(username.strip().lower())

    def remove_username(self, username: str) -> None:
        """从白名单移除用户名。"""
        with self._lock:
            self._usernames.discard(username.strip().lower())

    def is_whitelisted(self, ip: str = "", username: str = "") -> bool:
        """检查是否在白名单中。

        Args:
            ip: 客户端 IP 地址
            username: 用户名

        Returns:
            True 如果在白名单中
        """
        with self._lock:
            if ip and ip.strip() in self._ips:
                return True
            if username and username.strip().lower() in self._usernames:
                return True
            return False

    @property
    def ips(self) -> Set[str]:
        """返回当前 IP 白名单（只读副本）。"""
        with self._lock:
            return set(self._ips)

    @property
    def usernames(self) -> Set[str]:
        """返回当前用户名白名单（只读副本）。"""
        with self._lock:
            return set(self._usernames)


_whitelist = WhitelistManager()


# ── 用户标识解析 ──────────────────────────────────────────

def _get_user_key() -> str:
    """获取当前用户的唯一标识。

    优先级：
    1. 已登录用户名（session_state 中的 logged_in_user）
    2. 客户端 IP 地址
    3. 会话 ID（兜底）
    """
    # 1. 已登录用户
    username = st.session_state.get("logged_in_user", "")
    if username:
        return f"user:{_sanitize_log_value(username)}"

    # 2. 尝试获取 IP（Streamlit 不直接暴露，但可从 header 推断）
    # Streamlit Cloud 环境下可通过 st.context 获取
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        ctx = get_script_run_ctx()
        if ctx and hasattr(ctx, "session_info"):
            session_info = ctx.session_info
            if session_info and hasattr(session_info, "request"):
                # 某些版本可获取到 header
                headers = getattr(session_info.request, "headers", {})
                forwarded = headers.get("X-Forwarded-For", "")
                if forwarded:
                    ip = forwarded.split(",")[0].strip()
                    return f"ip:{_sanitize_log_value(ip)}"
    except Exception:
        pass

    # 3. 兜底：使用安全的随机会话 ID
    session_id = st.session_state.get("_session_id", "")
    if not session_id:
        session_id = secrets.token_hex(16)  # 128 位熵
        st.session_state["_session_id"] = session_id
    return f"session:{session_id}"


# ── 核心 API ─────────────────────────────────────────────

def check_rate_limit(
    max_requests: int = DEFAULT_MAX_REQUESTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> tuple[bool, Optional[str]]:
    """检查当前请求是否被允许（主入口）。

    在 app.py 或各模式模块的入口处调用此函数。
    如果请求被拦截，会在页面上显示限流提示。

    Args:
        max_requests: 窗口内最大请求数（默认 30）
        window_seconds: 窗口大小（默认 60 秒）

    Returns:
        (allowed, message) 元组：
        - allowed=True, message=None：放行
        - allowed=False, message="...": 拦截，message 为提示信息
    """
    user_key = _get_user_key()
    username = st.session_state.get("logged_in_user", "")

    # 提取客户端 IP（用于白名单检查）
    client_ip = ""
    if user_key.startswith("ip:"):
        client_ip = user_key[3:]

    # 白名单检查（同时传递 IP 和用户名）
    if _whitelist.is_whitelisted(ip=client_ip, username=username):
        return True, None

    # 执行限流检查（参数通过方法传入，不修改全局状态）
    allowed, info = _rate_limiter.check(
        user_key,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )

    if not allowed:
        retry_after = info["retry_after"]
        safe_key = _sanitize_log_value(user_key)
        logger.warning(
            "DOS 防护触发 | 用户标识: %s | 窗口内请求数: %d/%d | "
            "建议等待: %d 秒",
            safe_key,
            info["current_count"],
            info["limit"],
            retry_after,
        )

        # 将限流状态写入 session_state，供 UI 层使用
        st.session_state["rate_limited"] = True
        st.session_state["rate_limit_retry_after"] = retry_after
        st.session_state["rate_limit_info"] = info

        message = (
            f"⚠️ 请求过于频繁，请稍后再试。\n\n"
            f"当前限制：每分钟最多 {info['limit']} 次请求，"
            f"请在 {retry_after} 秒后重试。"
        )
        return False, message

    # 放行：清除之前的限流状态
    if st.session_state.get("rate_limited", False):
        st.session_state["rate_limited"] = False
        st.session_state.pop("rate_limit_retry_after", None)
        st.session_state.pop("rate_limit_info", None)

    return True, None


def get_rate_limit_status() -> dict:
    """获取当前用户的限流状态（不消耗配额）。

    Returns:
        包含 remaining、current_count、limit 的字典
    """
    user_key = _get_user_key()
    return _rate_limiter.get_status(user_key)


def reset_rate_limit(user_key: str = "") -> None:
    """手动重置指定用户的限流记录。

    Args:
        user_key: 用户标识，为空时重置当前用户
    """
    if not user_key:
        user_key = _get_user_key()
    _rate_limiter.reset(user_key)
    logger.info("DOS 防护：手动重置用户 %s 的限流记录",
                _sanitize_log_value(user_key))


# ── 白名单便捷 API ───────────────────────────────────────

def add_to_whitelist(ip: str = "", username: str = "") -> None:
    """添加白名单。"""
    if ip:
        _whitelist.add_ip(ip)
        logger.info("DOS 防护：添加 IP 白名单 %s", ip)
    if username:
        _whitelist.add_username(username)
        logger.info("DOS 防护：添加用户名白名单 %s",
                     _sanitize_log_value(username))


def remove_from_whitelist(ip: str = "", username: str = "") -> None:
    """移除白名单。"""
    if ip:
        _whitelist.remove_ip(ip)
    if username:
        _whitelist.remove_username(username)


def is_whitelisted(ip: str = "", username: str = "") -> bool:
    """检查是否在白名单中。"""
    return _whitelist.is_whitelisted(ip=ip, username=username)


# ── Streamlit UI 组件 ────────────────────────────────────

def render_rate_limit_banner() -> bool:
    """渲染限流提示横幅。

    在 app.py 主流程入口处调用。如果当前被限流，显示提示并返回 True，
    调用方应 st.stop() 阻止后续渲染。

    Returns:
        True 表示当前被限流（应停止渲染），False 表示正常
    """
    if st.session_state.get("rate_limited", False):
        retry_after = st.session_state.get("rate_limit_retry_after", 60)
        info = st.session_state.get("rate_limit_info", {})
        limit = info.get("limit", DEFAULT_MAX_REQUESTS)

        st.error(
            f"🚫 **请求过于频繁，请稍后再试**\n\n"
            f"当前限制：每分钟最多 **{limit}** 次请求 | "
            f"请在 **{retry_after}** 秒后重试\n\n"
            f"💡 提示：正常浏览不受影响，频繁刷新或大量请求会触发此限制。"
        )
        return True
    return False


def render_rate_limit_indicator() -> None:
    """渲染侧边栏限流状态指示器（可选）。

    显示当前窗口内的请求使用量，帮助用户了解自己的使用状态。
    """
    status = get_rate_limit_status()
    remaining = status["remaining"]
    limit = status["limit"]
    used = status["current_count"]

    if used == 0:
        st.caption(f"🛡️ 请求配额：{limit}/{limit} 可用")
    elif remaining > limit * 0.3:
        st.caption(f"🛡️ 请求配额：{remaining}/{limit} 可用")
    else:
        st.warning(f"🛡️ 请求配额紧张：{remaining}/{limit} 可用")
