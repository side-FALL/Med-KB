"""Upstash Redis REST API client for med-kb, replacing jsonbin_client."""

import json
import os
from typing import Any, Optional

import requests


class UpstashError(Exception):
    """Base exception for Upstash client errors."""


class UpstashAuthError(UpstashError):
    """Authentication failed."""


class UpstashTimeoutError(UpstashError):
    """Request timed out."""


class UpstashRequestError(UpstashError):
    """General request error."""


class UpstashClient:
    """Upstash Redis REST API client with JSONBin fallback."""

    TIMEOUT = 10

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self._url = (url or os.environ.get("UPSTASH_REDIS_REST_URL", "")).rstrip("/")
        self._token = token or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
        self._available = bool(self._url and self._token)
        self._last_read_from_fallback = False
        try:
            from jsonbin_client import JSONBinClient
            self._jsonbin = JSONBinClient()
        except Exception:
            self._jsonbin = None

    @property
    def is_degraded(self) -> bool:
        return self._last_read_from_fallback

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

    # ── public API ──

    def get_record(self) -> dict:
        """Read full record with Upstash → JSONBin fallback."""
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
                return record
            except Exception:
                pass

        raise UpstashRequestError("所有存储后端均不可用，请稍后重试")

    def update_record(self, data: dict) -> None:
        """Write the record with Upstash primary, JSONBin backup on failure."""
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
                return
            except UpstashError:
                pass

        if self._jsonbin:
            try:
                self._jsonbin.update_record(data)
                return
            except Exception:
                pass

        raise UpstashRequestError("所有存储后端均不可用")
