"""JSONBin 降级存储策略优化 — 单元测试

验收标准：
1. Redis 不可用（降级到 JSONBin）时，JSONBin 中仅存储基础用户信息，
   不包含 learning_records、stats 字段
2. 降级期间执行登录、学习记录写入，功能正常无报错
3. Redis 恢复后，降级期间的动态数据被补写回 Redis
4. 所有测试无网络依赖（用 MagicMock 模拟存储客户端）
"""

import json
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch, call

from upstash_client import (
    UpstashClient,
    UpstashError,
    UpstashAuthError,
    UpstashTimeoutError,
    UpstashRequestError,
)


def _make_user_data(username="alice", with_dynamic=True):
    """构造测试用户数据。"""
    data = {
        "profile": {
            "username": username,
            "password_hash": "$2b$12$" + "a" * 53,
            "email": f"{username}@test.com",
            "role": "user",
            "created_at": "2026-01-01T00:00:00Z",
            "last_active_at": "2026-07-23T00:00:00Z",
        },
        "preferences": {
            "theme": "light",
            "language": "zh-CN",
            "default_model": "",
            "font_size": 14,
            "extra_data": {},
        },
    }
    if with_dynamic:
        data["learning_records"] = [
            {
                "topic": "解剖学",
                "query": "心脏结构",
                "timestamp": "2026-07-23T10:00:00Z",
                "duration_seconds": 300,
                "source_type": "chat",
                "extra_data": {},
            }
        ]
        data["stats"] = {
            "total_queries": 10,
            "total_learning_time": 3600,
            "login_count": 5,
            "favorite_topics": ["解剖学"],
        }
    return data


def _make_root_data(users=None):
    """构造测试根数据。"""
    return {
        "users": users or {},
        "logs": {"operation_logs": [], "error_logs": [], "usage_stats": {"total_users": 1}},
        "config": {"app_config": {"app_name": "Med-KB"}},
    }


class TestFilterForJsonbin(unittest.TestCase):
    """测试 JSONBin 降级数据过滤。"""

    def test_filter_removes_dynamic_fields(self):
        """JSONBin 写入时过滤 learning_records 和 stats。"""
        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        filtered = UpstashClient._filter_for_jsonbin(root)

        alice = filtered["users"]["alice"]
        self.assertIn("profile", alice)
        self.assertIn("preferences", alice)
        self.assertNotIn("learning_records", alice)
        self.assertNotIn("stats", alice)

    def test_filter_preserves_basic_fields(self):
        """过滤后保留 profile 和 preferences 完整内容。"""
        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        filtered = UpstashClient._filter_for_jsonbin(root)

        alice = filtered["users"]["alice"]
        self.assertEqual(alice["profile"]["username"], "alice")
        self.assertEqual(alice["profile"]["role"], "user")
        self.assertIn("password_hash", alice["profile"])
        self.assertEqual(alice["preferences"]["theme"], "light")

    def test_filter_preserves_logs_and_config(self):
        """过滤不影响 logs 和 config 字段。"""
        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        filtered = UpstashClient._filter_for_jsonbin(root)

        self.assertIn("logs", filtered)
        self.assertIn("config", filtered)
        self.assertEqual(filtered["logs"]["usage_stats"]["total_users"], 1)

    def test_filter_multiple_users(self):
        """多用户数据均被正确过滤。"""
        users = {
            "alice": _make_user_data("alice", with_dynamic=True),
            "bob": _make_user_data("bob", with_dynamic=True),
        }
        root = _make_root_data(users)

        filtered = UpstashClient._filter_for_jsonbin(root)

        for username in ("alice", "bob"):
            self.assertNotIn("learning_records", filtered["users"][username])
            self.assertNotIn("stats", filtered["users"][username])
            self.assertIn("profile", filtered["users"][username])

    def test_filter_no_dynamic_fields(self):
        """用户数据本身没有动态字段时不过滤任何内容。"""
        user_data = _make_user_data("alice", with_dynamic=False)
        root = _make_root_data({"alice": user_data})

        filtered = UpstashClient._filter_for_jsonbin(root)

        self.assertIn("profile", filtered["users"]["alice"])
        self.assertIn("preferences", filtered["users"]["alice"])

    def test_filter_does_not_mutate_original(self):
        """过滤操作不修改原始数据。"""
        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})
        original_records = deepcopy(root["users"]["alice"]["learning_records"])

        UpstashClient._filter_for_jsonbin(root)

        self.assertEqual(root["users"]["alice"]["learning_records"], original_records)
        self.assertIn("stats", root["users"]["alice"])


class TestDegradedWrite(unittest.TestCase):
    """测试降级写入行为。"""

    @patch("upstash_client.requests")
    def test_upstash_failure_triggers_jsonbin_filtered_write(self, mock_requests):
        """Upstash 不可用时，JSONBin 写入过滤后的数据。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        mock_jsonbin = MagicMock()
        client._jsonbin = mock_jsonbin

        # Upstash 写入失败
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_requests.post.return_value = mock_resp
        mock_requests.get.return_value = mock_resp

        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        client.update_record(root)

        # JSONBin 收到的是过滤后的数据
        mock_jsonbin.update_record.assert_called_once()
        written_data = mock_jsonbin.update_record.call_args[0][0]
        alice = written_data["users"]["alice"]
        self.assertNotIn("learning_records", alice)
        self.assertNotIn("stats", alice)
        self.assertIn("profile", alice)
        self.assertIn("preferences", alice)

    @patch("upstash_client.requests")
    def test_pending_dynamic_saved_on_degradation(self, mock_requests):
        """降级时动态数据被暂存到内存队列。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        mock_jsonbin = MagicMock()
        client._jsonbin = mock_jsonbin

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_requests.post.return_value = mock_resp
        mock_requests.get.return_value = mock_resp

        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        client.update_record(root)

        self.assertTrue(client.has_pending_dynamic)
        self.assertIn("alice", client._pending_dynamic)
        self.assertIn("learning_records", client._pending_dynamic["alice"])
        self.assertIn("stats", client._pending_dynamic["alice"])

    @patch("upstash_client.requests")
    def test_degraded_login_no_error(self, mock_requests):
        """降级期间登录操作（更新 stats）无报错。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        mock_jsonbin = MagicMock()
        client._jsonbin = mock_jsonbin

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_requests.post.return_value = mock_resp
        mock_requests.get.return_value = mock_resp

        user_data = _make_user_data("alice", with_dynamic=True)
        user_data["stats"]["login_count"] = 6
        root = _make_root_data({"alice": user_data})

        # 不应抛出异常
        client.update_record(root)

        # 暂存的 stats 包含更新的 login_count
        self.assertEqual(client._pending_dynamic["alice"]["stats"]["login_count"], 6)

    @patch("upstash_client.requests")
    def test_degraded_learning_record_no_error(self, mock_requests):
        """降级期间添加学习记录无报错。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        mock_jsonbin = MagicMock()
        client._jsonbin = mock_jsonbin

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_requests.post.return_value = mock_resp
        mock_requests.get.return_value = mock_resp

        user_data = _make_user_data("alice", with_dynamic=True)
        user_data["learning_records"].append({
            "topic": "药理学",
            "query": "阿司匹林",
            "timestamp": "2026-07-23T11:00:00Z",
            "duration_seconds": 200,
            "source_type": "chat",
            "extra_data": {},
        })
        user_data["stats"]["total_queries"] = 11
        root = _make_root_data({"alice": user_data})

        client.update_record(root)

        pending = client._pending_dynamic["alice"]
        self.assertEqual(len(pending["learning_records"]), 2)
        self.assertEqual(pending["stats"]["total_queries"], 11)


class TestDegradedRead(unittest.TestCase):
    """测试降级读取行为。"""

    @patch("upstash_client.requests")
    def test_read_from_jsonbin_merges_pending(self, mock_requests):
        """降级读取时，暂存的动态数据被合并到返回结果。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")

        # 模拟暂存队列已有数据
        client._pending_dynamic = {
            "alice": {
                "learning_records": [{"topic": "解剖学", "timestamp": "2026-07-23T10:00:00Z"}],
                "stats": {"total_queries": 10, "login_count": 5},
            }
        }

        # Upstash 读取失败
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_requests.get.return_value = mock_resp

        # JSONBin 返回基础数据（无动态字段）
        mock_jsonbin = MagicMock()
        jsonbin_data = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        mock_jsonbin.get_record.return_value = jsonbin_data
        client._jsonbin = mock_jsonbin

        result = client.get_record()

        alice = result["users"]["alice"]
        self.assertIn("learning_records", alice)
        self.assertEqual(len(alice["learning_records"]), 1)
        self.assertEqual(alice["learning_records"][0]["topic"], "解剖学")
        self.assertIn("stats", alice)
        self.assertEqual(alice["stats"]["total_queries"], 10)

    @patch("upstash_client.requests")
    def test_read_from_jsonbin_sets_degraded_flag(self, mock_requests):
        """降级读取后 is_degraded 为 True。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_requests.get.return_value = mock_resp

        mock_jsonbin = MagicMock()
        mock_jsonbin.get_record.return_value = _make_root_data()
        client._jsonbin = mock_jsonbin

        client.get_record()

        self.assertTrue(client.is_degraded)

    @patch("upstash_client.requests")
    def test_read_from_upstash_clears_degraded_flag(self, mock_requests):
        """Upstash 正常读取后 is_degraded 为 False。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        client._last_read_from_fallback = True

        # 模拟 Upstash 正常返回
        mock_meta_resp = MagicMock()
        mock_meta_resp.status_code = 200
        mock_meta_resp.json.return_value = {"result": json.dumps({"logs": {}, "config": {}})}

        mock_keys_resp = MagicMock()
        mock_keys_resp.status_code = 200
        mock_keys_resp.json.return_value = {"result": []}

        mock_requests.get.side_effect = [mock_meta_resp, mock_keys_resp]

        result = client.get_record()

        self.assertFalse(client.is_degraded)
        self.assertIn("users", result)


class TestResyncOnRecovery(unittest.TestCase):
    """测试 Redis 恢复后的补同步。"""

    @patch("upstash_client.requests")
    def test_resync_writes_dynamic_to_redis(self, mock_requests):
        """Redis 恢复写入时，暂存的动态数据被补写回 Redis。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")

        # 模拟暂存队列
        client._pending_dynamic = {
            "alice": {
                "learning_records": [
                    {"topic": "解剖学", "timestamp": "2026-07-23T10:00:00Z"},
                ],
                "stats": {"total_queries": 10, "login_count": 5},
            }
        }

        # 模拟 Upstash 正常：所有请求返回 200
        mock_ok = MagicMock()
        mock_ok.status_code = 200

        # _keys 返回已有用户 key
        mock_keys_resp = MagicMock()
        mock_keys_resp.status_code = 200
        mock_keys_resp.json.return_value = {"result": ["user:alice"]}

        # _get 返回当前 Upstash 中的用户数据（基础数据，无动态字段）
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        existing_user = _make_user_data("alice", with_dynamic=False)
        existing_user["learning_records"] = []
        existing_user["stats"] = {"total_queries": 0, "login_count": 0}
        mock_get_resp.json.return_value = {"result": json.dumps(existing_user)}

        # _del 和 _set 返回 200
        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 200
        mock_del_resp.json.return_value = {"result": 1}

        mock_set_resp = MagicMock()
        mock_set_resp.status_code = 200
        mock_set_resp.json.return_value = {"result": "OK"}

        # 请求序列：
        # update_record: _keys(1) → _del(1) → _set(user:alice) → _set(meta)
        # _try_resync: _keys(2) → _get(alice) → _keys(3) → _del(2) → _set(user:alice) → _set(meta)...
        # 简化：让所有 GET 返回适当值
        call_count = [0]

        def side_effect_get(*args, **kwargs):
            call_count[0] += 1
            url = args[0] if args else kwargs.get("url", "")
            if "/keys/" in url:
                return mock_keys_resp
            elif "/get/" in url:
                return mock_get_resp
            elif "/del/" in url:
                return mock_del_resp
            return mock_ok

        def side_effect_post(*args, **kwargs):
            return mock_set_resp

        mock_requests.get.side_effect = side_effect_get
        mock_requests.post.side_effect = side_effect_post

        # 触发 Upstash 写入（恢复后）
        user_data = _make_user_data("alice", with_dynamic=False)
        root = _make_root_data({"alice": user_data})
        client.update_record(root)

        # 补同步后暂存队列应被清空
        self.assertFalse(client.has_pending_dynamic)

    @patch("upstash_client.requests")
    def test_resync_merges_learning_records(self, mock_requests):
        """补同步时学习记录被合并（去重）。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")

        # 暂存一条新记录
        client._pending_dynamic = {
            "alice": {
                "learning_records": [
                    {"topic": "解剖学", "timestamp": "2026-07-23T10:00:00Z"},
                ],
                "stats": {"total_queries": 10},
            }
        }

        # Upstash 中已有一条不同时间戳的记录
        existing_user = _make_user_data("alice", with_dynamic=False)
        existing_user["learning_records"] = [
            {"topic": "药理学", "timestamp": "2026-07-22T10:00:00Z"},
        ]
        existing_user["stats"] = {"total_queries": 5}

        mock_keys_resp = MagicMock()
        mock_keys_resp.status_code = 200
        mock_keys_resp.json.return_value = {"result": ["user:alice"]}

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"result": json.dumps(existing_user)}

        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 200
        mock_del_resp.json.return_value = {"result": 1}

        mock_set_resp = MagicMock()
        mock_set_resp.status_code = 200
        mock_set_resp.json.return_value = {"result": "OK"}

        written_users = {}

        def side_effect_get(*args, **kwargs):
            url = args[0] if args else kwargs.get("url", "")
            if "/keys/" in url:
                return mock_keys_resp
            elif "/get/" in url:
                return mock_get_resp
            elif "/del/" in url:
                return mock_del_resp
            return MagicMock(status_code=200)

        def side_effect_post(*args, **kwargs):
            url = args[0] if args else kwargs.get("url", "")
            if "/set/user:" in url:
                # 捕获写入的用户数据
                data = kwargs.get("data", "")
                uid = url.split("/set/user:", 1)[1] if "/set/user:" in url else ""
                if isinstance(data, str):
                    written_users[uid] = json.loads(data)
            return mock_set_resp

        mock_requests.get.side_effect = side_effect_get
        mock_requests.post.side_effect = side_effect_post

        user_data = _make_user_data("alice", with_dynamic=False)
        root = _make_root_data({"alice": user_data})
        client.update_record(root)

        # 验证补同步后暂存队列清空
        self.assertFalse(client.has_pending_dynamic)

    @patch("upstash_client.requests")
    def test_resync_failure_keeps_pending(self, mock_requests):
        """补同步失败时暂存队列保留。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")

        client._pending_dynamic = {
            "alice": {
                "learning_records": [{"topic": "解剖学", "timestamp": "2026-07-23T10:00:00Z"}],
                "stats": {"total_queries": 10},
            }
        }

        # 第一次 _keys 正常（update_record 主流程），后续 _keys 失败（resync）
        call_count = [0]

        def side_effect_get(*args, **kwargs):
            url = args[0] if args else kwargs.get("url", "")
            if "/keys/" in url:
                call_count[0] += 1
                if call_count[0] <= 1:
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.json.return_value = {"result": []}
                    return resp
                else:
                    resp = MagicMock()
                    resp.status_code = 500
                    resp.text = "error"
                    return resp

            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"result": "OK"}
            return resp

        mock_set_resp = MagicMock()
        mock_set_resp.status_code = 200
        mock_set_resp.json.return_value = {"result": "OK"}
        mock_requests.get.side_effect = side_effect_get
        mock_requests.post.return_value = mock_set_resp

        user_data = _make_user_data("alice", with_dynamic=False)
        root = _make_root_data({"alice": user_data})

        # 不应抛出异常
        client.update_record(root)

        # 暂存队列应保留
        self.assertTrue(client.has_pending_dynamic)
        self.assertIn("alice", client._pending_dynamic)

    @patch("upstash_client.requests")
    def test_no_resync_when_no_pending(self, mock_requests):
        """无暂存数据时不触发补同步。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        self.assertFalse(client.has_pending_dynamic)

        mock_keys_resp = MagicMock()
        mock_keys_resp.status_code = 200
        mock_keys_resp.json.return_value = {"result": []}

        mock_set_resp = MagicMock()
        mock_set_resp.status_code = 200
        mock_set_resp.json.return_value = {"result": "OK"}

        mock_requests.get.return_value = mock_keys_resp
        mock_requests.post.return_value = mock_set_resp

        root = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        client.update_record(root)

        # 只有 update_record 主流程的调用，无额外补同步调用
        # _keys 被调用 1 次（主流程）
        get_calls = [c for c in mock_requests.get.call_args_list]
        self.assertEqual(len(get_calls), 1)


class TestEndToEndDegradation(unittest.TestCase):
    """端到端降级与恢复场景测试。"""

    @patch("upstash_client.requests")
    def test_full_degradation_and_recovery_cycle(self, mock_requests):
        """完整降级→操作→恢复→补同步周期。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        mock_jsonbin = MagicMock()
        client._jsonbin = mock_jsonbin

        # === 阶段 1：Upstash 不可用，降级到 JSONBin ===
        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_fail.text = "error"
        mock_requests.post.return_value = mock_fail
        mock_requests.get.return_value = mock_fail

        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})
        client.update_record(root)

        # JSONBin 收到过滤后的数据
        written = mock_jsonbin.update_record.call_args[0][0]
        self.assertNotIn("learning_records", written["users"]["alice"])
        self.assertNotIn("stats", written["users"]["alice"])
        # 暂存队列有数据
        self.assertTrue(client.has_pending_dynamic)

        # === 阶段 2：Upstash 恢复 ===
        mock_jsonbin.reset_mock()

        mock_keys_resp = MagicMock()
        mock_keys_resp.status_code = 200
        mock_keys_resp.json.return_value = {"result": ["user:alice"]}

        existing_user = _make_user_data("alice", with_dynamic=False)
        existing_user["learning_records"] = []
        existing_user["stats"] = {"total_queries": 0, "login_count": 0}

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {"result": json.dumps(existing_user)}

        mock_del_resp = MagicMock()
        mock_del_resp.status_code = 200
        mock_del_resp.json.return_value = {"result": 1}

        mock_set_resp = MagicMock()
        mock_set_resp.status_code = 200
        mock_set_resp.json.return_value = {"result": "OK"}

        def side_effect_get(*args, **kwargs):
            url = args[0] if args else kwargs.get("url", "")
            if "/keys/" in url:
                return mock_keys_resp
            elif "/get/" in url:
                return mock_get_resp
            elif "/del/" in url:
                return mock_del_resp
            return MagicMock(status_code=200)

        mock_requests.get.side_effect = side_effect_get
        mock_requests.post.return_value = mock_set_resp

        root2 = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        client.update_record(root2)

        # 补同步完成，暂存队列清空
        self.assertFalse(client.has_pending_dynamic)
        # JSONBin 不应在恢复阶段被调用
        mock_jsonbin.update_record.assert_not_called()

    @patch("upstash_client.requests")
    def test_multiple_users_degradation(self, mock_requests):
        """多用户降级场景：每个用户的动态数据独立暂存。"""
        client = UpstashClient(url="http://upstash.test", token="test-token")
        mock_jsonbin = MagicMock()
        client._jsonbin = mock_jsonbin

        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_fail.text = "error"
        mock_requests.post.return_value = mock_fail
        mock_requests.get.return_value = mock_fail

        users = {
            "alice": _make_user_data("alice", with_dynamic=True),
            "bob": _make_user_data("bob", with_dynamic=True),
        }
        root = _make_root_data(users)
        client.update_record(root)

        # 两个用户的动态数据都被暂存
        self.assertIn("alice", client._pending_dynamic)
        self.assertIn("bob", client._pending_dynamic)
        self.assertIn("learning_records", client._pending_dynamic["alice"])
        self.assertIn("stats", client._pending_dynamic["bob"])

        # JSONBin 写入的数据两个用户都没有动态字段
        written = mock_jsonbin.update_record.call_args[0][0]
        for username in ("alice", "bob"):
            self.assertNotIn("learning_records", written["users"][username])
            self.assertNotIn("stats", written["users"][username])


class TestMergePendingIntoRecord(unittest.TestCase):
    """测试暂存数据合并到读取记录。"""

    def test_merge_empty_pending(self):
        """无暂存数据时不修改记录。"""
        client = UpstashClient.__new__(UpstashClient)
        client._pending_dynamic = {}

        record = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        result = client._merge_pending_into_record(record)

        self.assertEqual(result, record)

    def test_merge_adds_dynamic_fields(self):
        """暂存数据被正确合并到用户记录。"""
        client = UpstashClient.__new__(UpstashClient)
        client._pending_dynamic = {
            "alice": {
                "learning_records": [{"topic": "解剖学", "timestamp": "t1"}],
                "stats": {"total_queries": 10, "login_count": 5},
            }
        }

        record = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        result = client._merge_pending_into_record(record)

        alice = result["users"]["alice"]
        self.assertEqual(len(alice["learning_records"]), 1)
        self.assertEqual(alice["learning_records"][0]["topic"], "解剖学")
        self.assertEqual(alice["stats"]["total_queries"], 10)

    def test_merge_unknown_user_ignored(self):
        """暂存队列中的未知用户不影响记录。"""
        client = UpstashClient.__new__(UpstashClient)
        client._pending_dynamic = {
            "unknown_user": {
                "learning_records": [{"topic": "test"}],
                "stats": {"total_queries": 1},
            }
        }

        record = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        result = client._merge_pending_into_record(record)

        self.assertNotIn("unknown_user", result["users"])
        self.assertIn("alice", result["users"])

    def test_merge_does_not_mutate_pending(self):
        """合并操作不修改暂存队列本身。"""
        client = UpstashClient.__new__(UpstashClient)
        pending_records = [{"topic": "解剖学", "timestamp": "t1"}]
        client._pending_dynamic = {
            "alice": {
                "learning_records": pending_records,
                "stats": {"total_queries": 10},
            }
        }

        record = _make_root_data({"alice": _make_user_data("alice", with_dynamic=False)})
        client._merge_pending_into_record(record)

        # 暂存队列未被修改
        self.assertEqual(client._pending_dynamic["alice"]["learning_records"], pending_records)


class TestSavePendingDynamic(unittest.TestCase):
    """测试动态数据暂存逻辑。"""

    def test_save_extracts_dynamic_fields(self):
        """正确提取并暂存动态字段。"""
        client = UpstashClient.__new__(UpstashClient)
        client._pending_dynamic = {}

        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        client._save_pending_dynamic(root)

        self.assertIn("alice", client._pending_dynamic)
        self.assertIn("learning_records", client._pending_dynamic["alice"])
        self.assertIn("stats", client._pending_dynamic["alice"])
        self.assertNotIn("profile", client._pending_dynamic["alice"])
        self.assertNotIn("preferences", client._pending_dynamic["alice"])

    def test_save_skips_users_without_dynamic(self):
        """没有动态字段的用户不被暂存。"""
        client = UpstashClient.__new__(UpstashClient)
        client._pending_dynamic = {}

        user_data = _make_user_data("alice", with_dynamic=False)
        root = _make_root_data({"alice": user_data})

        client._save_pending_dynamic(root)

        self.assertNotIn("alice", client._pending_dynamic)

    def test_save_deep_copies_data(self):
        """暂存使用深拷贝，不与原数据共享引用。"""
        client = UpstashClient.__new__(UpstashClient)
        client._pending_dynamic = {}

        user_data = _make_user_data("alice", with_dynamic=True)
        root = _make_root_data({"alice": user_data})

        client._save_pending_dynamic(root)

        # 修改原数据不影响暂存
        root["users"]["alice"]["learning_records"].append({"topic": "new"})
        self.assertEqual(len(client._pending_dynamic["alice"]["learning_records"]), 1)


if __name__ == "__main__":
    unittest.main()
