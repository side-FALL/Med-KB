"""Upstash Redis REST API client for med-kb, replacing jsonbin_client."""

import json
import os
import platform
import time
from pathlib import Path
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


def _cache_dir() -> Path:
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    else:
        base = Path.home() / ".cache"
    d = Path(base) / "med_kb" / "upstash"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path() -> Path:
    return _cache_dir() / "data.json"


class UpstashClient:
    """Upstash Redis REST API client with local-cache fallback."""

    TIMEOUT = 10

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self._url = (url or os.environ.get("UPSTASH_REDIS_REST_URL", "")).rstrip("/")
        self._token = token or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
        if not self._url or not self._token:
            raise UpstashAuthError(
                "UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be set"
            )

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

    # ── local cache helpers ──

    def _read_cache(self) -> Optional[dict]:
        p = _cache_path()
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, data: dict) -> None:
        try:
            _cache_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    # ── public API ──

    def get_record(self) -> dict:
        """Read meta key + all user:* keys and assemble the full record."""
        try:
            # meta holds everything except per-user data
            meta_raw = self._get("meta")
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})

            # per-user keys
            user_keys = self._keys("user:*")
            users: dict = {}
            for key in user_keys:
                val = self._get(key)
                uid = key.split("user:", 1)[1] if "user:" in key else key
                users[uid] = json.loads(val) if isinstance(val, str) else val

            record = {
                "users": users,
                "logs": meta.get("logs", {}),
                "config": meta.get("config", {}),
            }
            self._write_cache(record)
            return record

        except UpstashError:
            cached = self._read_cache()
            if cached is not None:
                return cached
            raise

    def update_record(self, data: dict) -> None:
        """Write the record: users become user:* keys, rest goes into meta."""
        try:
            users = data.get("users", {})
            meta = {k: v for k, v in data.items() if k != "users"}

            # wipe old user keys first
            old_keys = self._keys("user:*")
            for k in old_keys:
                self._del(k)

            # write per-user data
            for uid, info in users.items():
                self._set(f"user:{uid}", info)

            # write meta
            self._set("meta", meta)

            self._write_cache(data)

        except UpstashError:
            self._write_cache(data)
            raise
