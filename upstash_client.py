"""Upstash Redis REST API client for med-kb, replacing jsonbin_client.

降级存储策略：
- Redis 可用时：完整数据写入 Redis（按用户独立 key）
- Redis 不可用时：JSONBin 仅存储基础用户信息（profile/preferences），
  动态数据（learning_records/stats）暂存内存队列，Redis 恢复后补同步
"""

import json
import logging
import os
from copy import deepcopy
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class UpstashError(Exception):
    """Base exception for Upstash client errors."""


class UpstashAuthError(UpstashError):
    """Authentication failed."""


class UpstashTimeoutError(UpstashError):
    """Request timed out."""


class UpstashRequestError(UpstashError):
    """General request error."""


# JSONBin 降级时不存储的动态数据字段
_DYNAMIC_FIELDS = frozenset({"learning_records", "stats"})


class UpstashClient:
    """Upstash Redis REST API client with JSONBin fallback.

    降级策略：Redis 不可用时，JSONBin 仅写入基础用户信息（profile/preferences），
    动态数据（learning_records/stats）暂存内存，Redis 恢复后自动补同步。
    """

    TIMEOUT = 10

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self._url = (url or os.environ.get("UPSTASH_REDIS_REST_URL", "")).strip('"').rstrip("/")
        self._token = token or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip('"')
        self._available = bool(self._url and self._token)
        self._last_read_from_fallback = False
        # 显式能力标记：支持单 key 读写（get_user_by_name / set_user_by_name / get_meta / set_meta）
        # UserDataManager 通过 `getattr(client, 'supports_single_key_ops', False) is True`
        # 检测此能力，避免 hasattr 在 MagicMock 测试替身下误判（MagicMock 自动生成任意属性）
        self.supports_single_key_ops = True
        # 降级期间暂存的动态数据：{username: {"learning_records": [...], "stats": {...}}}
        self._pending_dynamic: dict[str, dict] = {}
        try:
            from jsonbin_client import JSONBinClient
            self._jsonbin = JSONBinClient()
        except Exception:
            self._jsonbin = None

    @property
    def is_degraded(self) -> bool:
        return self._last_read_from_fallback

    @property
    def has_pending_dynamic(self) -> bool:
        """是否有待补同步的动态数据。"""
        return bool(self._pending_dynamic)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    # ── low-level helpers ──

    def _get(self, key: str) -> Any:
        resp = requests.get(
            f"{self._url}/get/{key}",
            headers=self._headers(),
            timeout=self.TIMEOUT,
        )
        self._check_status(resp)
        body = resp.json()
        return body.get("result")

    def _set(self, key: str, value: Any) -> None:
        payload = value if isinstance(value, str) else json.dumps(value)
        resp = requests.post(
            f"{self._url}/set/{key}",
            headers=self._headers(),
            data=payload,
            timeout=self.TIMEOUT,
        )
        self._check_status(resp)

    def _del(self, key: str) -> None:
        resp = requests.get(
            f"{self._url}/del/{key}",
            headers=self._headers(),
            timeout=self.TIMEOUT,
        )
        self._check_status(resp)

    def _keys(self, pattern: str) -> list:
        resp = requests.get(
            f"{self._url}/keys/{pattern}",
            headers=self._headers(),
            timeout=self.TIMEOUT,
        )
        self._check_status(resp)
        body = resp.json()
        result = body.get("result")
        if isinstance(result, list):
            return result
        return []

    def _check_status(self, resp: requests.Response) -> None:
        if resp.status_code == 401 or resp.status_code == 403:
            raise UpstashAuthError(f"Auth failed ({resp.status_code}): {resp.text}")
        if resp.status_code == 408:
            raise UpstashTimeoutError(f"Timeout: {resp.text}")
        if resp.status_code >= 400:
            raise UpstashRequestError(
                f"HTTP {resp.status_code}: {resp.text}"
            )

    # ── 降级数据过滤 ──

    @staticmethod
    def _filter_for_jsonbin(data: dict) -> dict:
        """过滤用户数据，仅保留基础信息用于 JSONBin 降级存储。

        JSONBin 降级时不存储动态数据（learning_records、stats），
        仅保留 profile（用户名、密码哈希、角色、偏好设置）和 preferences。
        """
        filtered = deepcopy(data)
        users = filtered.get("users", {})
        for username in users:
            user_data = users[username]
            if isinstance(user_data, dict):
                for field in _DYNAMIC_FIELDS:
                    user_data.pop(field, None)
        return filtered

    def _save_pending_dynamic(self, data: dict) -> None:
        """从完整数据中提取动态字段并暂存到内存队列。"""
        users = data.get("users", {})
        for username, user_data in users.items():
            if not isinstance(user_data, dict):
                continue
            dynamic = {}
            if "learning_records" in user_data:
                dynamic["learning_records"] = deepcopy(user_data["learning_records"])
            if "stats" in user_data:
                dynamic["stats"] = deepcopy(user_data["stats"])
            if dynamic:
                self._pending_dynamic[username] = dynamic

    def _merge_pending_into_record(self, record: dict) -> dict:
        """将暂存的动态数据合并到从 JSONBin 读取的记录中。

        降级期间读取时，将内存中的动态数据补回，使调用方看到完整数据。
        """
        if not self._pending_dynamic:
            return record
        record = deepcopy(record)
        users = record.get("users", {})
        for username, pending in self._pending_dynamic.items():
            if username in users and isinstance(users[username], dict):
                if "learning_records" in pending:
                    users[username]["learning_records"] = deepcopy(pending["learning_records"])
                if "stats" in pending:
                    users[username]["stats"] = deepcopy(pending["stats"])
        record["users"] = users
        return record

    def _try_resync_pending(self) -> None:
        """Redis 恢复后，将降级期间的动态数据补写回 Redis。

        使用低层 API 直接操作，避免递归调用 update_record。
        失败时静默保留暂存队列，等待下次触发。
        """
        if not self._pending_dynamic:
            return

        try:
            # 读取当前 Upstash 中的用户数据
            user_keys = self._keys("user:*")
            current_users: dict = {}
            for key in user_keys:
                val = self._get(key)
                uid = key.split("user:", 1)[1] if "user:" in key else key
                current_users[uid] = json.loads(val) if isinstance(val, str) else val

            # 合并暂存的动态数据（取较大值，防止回退）
            for username, pending in self._pending_dynamic.items():
                if username not in current_users:
                    continue
                user_data = current_users[username]
                if not isinstance(user_data, dict):
                    continue

                # 学习记录：合并去重（按 timestamp）
                if "learning_records" in pending:
                    existing = user_data.get("learning_records", [])
                    pending_records = pending["learning_records"]
                    existing_timestamps = {
                        r.get("timestamp") for r in existing if isinstance(r, dict)
                    }
                    for rec in pending_records:
                        if isinstance(rec, dict) and rec.get("timestamp") not in existing_timestamps:
                            existing.append(rec)
                    user_data["learning_records"] = existing

                # 统计数据：取较大值
                if "stats" in pending:
                    existing_stats = user_data.get("stats", {})
                    pending_stats = pending["stats"]
                    for stat_key, stat_val in pending_stats.items():
                        if isinstance(stat_val, (int, float)):
                            existing_stats[stat_key] = max(
                                existing_stats.get(stat_key, 0), stat_val
                            )
                        elif isinstance(stat_val, list):
                            # favorite_topics 等列表字段取较长列表
                            if len(stat_val) > len(existing_stats.get(stat_key, [])):
                                existing_stats[stat_key] = stat_val
                        else:
                            existing_stats[stat_key] = stat_val
                    user_data["stats"] = existing_stats

                current_users[username] = user_data

            # 写回 Upstash
            old_keys = self._keys("user:*")
            for k in old_keys:
                self._del(k)
            for uid, info in current_users.items():
                self._set(f"user:{uid}", info)

            # 补同步成功，清空暂存队列
            self._pending_dynamic.clear()
            logger.info("降级期间动态数据已补同步回 Redis")

        except UpstashError as exc:
            logger.warning("动态数据补同步失败，保留暂存队列等待下次重试: %s", exc)

    # ── 单用户快速读写（登录优化路径） ──

    def get_user_by_name(self, username: str) -> Optional[dict]:
        """Read a single user's data directly from Redis by key.

        登录优化路径：避免加载全部用户，仅读取目标用户的单个 key。
        Redis 不可用时回退到 JSONBin 全量读取。

        Args:
            username: 用户名

        Returns:
            用户数据字典，不存在返回 None

        Raises:
            UpstashRequestError: 所有存储后端均不可用
        """
        self._last_read_from_fallback = False

        if self._available:
            try:
                val = self._get(f"user:{username}")
                if val is None:
                    return None
                return json.loads(val) if isinstance(val, str) else val
            except UpstashError:
                pass

        # Fallback: JSONBin 全量读取后提取单用户
        if self._jsonbin:
            try:
                record = self._jsonbin.get_record()
                self._last_read_from_fallback = True
                record = self._merge_pending_into_record(record)
                users = record.get("users", {})
                return users.get(username)
            except Exception:
                pass

        raise UpstashRequestError("所有存储后端均不可用，请稍后重试")

    def set_user_by_name(self, username: str, data: dict) -> None:
        """Write a single user's data directly to Redis by key.

        登录优化路径：避免重写全部用户，仅更新目标用户的单个 key。
        Redis 不可用时不回退到 JSONBin（动态数据不静默降级）。

        Args:
            username: 用户名
            data: 用户数据字典

        Raises:
            UpstashRequestError: Redis 不可用
        """
        if self._available:
            try:
                self._set(f"user:{username}", data)
                return
            except UpstashError:
                pass

        raise UpstashRequestError("Redis 不可用，无法写入用户数据")

    def get_meta(self) -> dict:
        """Read meta data directly from Redis.

        登录优化路径：仅读取 meta key，避免加载全部用户。

        Returns:
            meta 数据字典，不可用时返回空字典
        """
        if self._available:
            try:
                val = self._get("meta")
                if val is None:
                    return {}
                return json.loads(val) if isinstance(val, str) else val
            except UpstashError:
                pass
        return {}

    def set_meta(self, meta: dict) -> None:
        """Write meta data directly to Redis.

        登录优化路径：仅写入 meta key，避免重写全部用户。

        Raises:
            UpstashRequestError: Redis 不可用
        """
        if self._available:
            try:
                self._set("meta", meta)
                return
            except UpstashError:
                pass
        raise UpstashRequestError("Redis 不可用，无法写入元数据")

    # ── public API ──

    def get_record(self) -> dict:
        """Read full record with Upstash → JSONBin fallback.

        降级读取时，自动将内存暂存的动态数据合并到返回结果中。
        """
        self._last_read_from_fallback = False

        if self._available:
            try:
                meta_raw = self._get("meta")
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
                user_keys = self._keys("user:*")
                users: dict = {}
                for key in user_keys:
                    val = self._get(key)
                    uid = key.split("user:", 1)[1] if "user:" in key else key
                    users[uid] = json.loads(val) if isinstance(val, str) else val
                return {"users": users, "logs": meta.get("logs", {}), "config": meta.get("config", {})}
            except UpstashError:
                pass

        if self._jsonbin:
            try:
                record = self._jsonbin.get_record()
                self._last_read_from_fallback = True
                # 合并暂存的动态数据，使调用方看到完整数据
                record = self._merge_pending_into_record(record)
                return record
            except Exception:
                pass

        raise UpstashRequestError("所有存储后端均不可用，请稍后重试")

    def update_record(self, data: dict) -> None:
        """Write the record with Upstash primary, JSONBin backup on failure.

        降级到 JSONBin 时：仅写入基础用户信息，动态数据暂存内存。
        Redis 恢复写入时：自动触发补同步，将暂存的动态数据写回 Redis。
        """
        if self._available:
            try:
                users = data.get("users", {})
                meta = {k: v for k, v in data.items() if k != "users"}
                old_keys = self._keys("user:*")
                for k in old_keys:
                    self._del(k)
                for uid, info in users.items():
                    self._set(f"user:{uid}", info)
                self._set("meta", meta)
                # Redis 写入成功后，尝试补同步降级期间的动态数据
                self._try_resync_pending()
                return
            except UpstashError:
                pass

        # 降级到 JSONBin：仅存储基础用户信息，动态数据暂存内存
        if self._jsonbin:
            try:
                self._save_pending_dynamic(data)
                filtered = self._filter_for_jsonbin(data)
                self._jsonbin.update_record(filtered)
                return
            except Exception:
                pass

        raise UpstashRequestError("所有存储后端均不可用")
