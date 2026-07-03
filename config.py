"""医学教材知识库 — 配置模块

包含模型配置、API Key 管理、密码验证等。
"""

import os
import hashlib
import hmac

import streamlit as st


# ── 模型提供商配置 ──────────────────────────────────────

MODEL_PROVIDERS = {
    "cherryin": {
        "base_url": "https://open.cherryin.net/v1/chat/completions",
        "embed_url": "https://open.cherryin.net/v1",
        "api_key_env": "CS_API_KEY",
    },
    "mimo": {
        "base_url": "https://api.xiaomimimo.com/v1/chat/completions",
        "embed_url": "https://api.xiaomimimo.com/v1",
        "api_key_env": "MIMO_API_KEY",
    },
    "ark": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "embed_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "ARK_API_KEY",
    },
}


# ── 模型配置 ────────────────────────────────────────────

MODELS = {
    "cherryin/deepseek-v4-flash": {
        "name": "DeepSeek V4 Flash",
        "provider": "cherryin",
        "model_id": "deepseek/deepseek-v4-flash(free)",
    },
    "mimo/mimo-v2.5": {
        "name": "MiMo V2.5",
        "provider": "mimo",
        "model_id": "mimo-v2.5",
    },
    "ark/deepseek-v4-flash": {
        "name": "DeepSeek V4 Flash (火山)",
        "provider": "ark",
        "model_id": "deepseek-v4-flash-260425",
    },
}


# ── 降级顺序 ────────────────────────────────────────────

FALLBACK_ORDER = [
    "cherryin/deepseek-v4-flash",
    "mimo/mimo-v2.5",
    "ark/deepseek-v4-flash",
]


# ── API Key 管理 ────────────────────────────────────────

def get_api_key(provider: str) -> str:
    """获取指定提供商的 API Key。"""
    env_var = MODEL_PROVIDERS[provider]["api_key_env"]
    return os.environ.get(env_var, "")


def get_model_api_config(model_key: str) -> tuple[str, str, str]:
    """获取模型的 API 配置。

    Returns:
        (api_key, api_url, model_id)
    """
    model_config = MODELS[model_key]
    provider_config = MODEL_PROVIDERS[model_config["provider"]]
    api_key = get_api_key(model_config["provider"])
    api_url = provider_config["base_url"]
    model_id = model_config["model_id"]
    return api_key, api_url, model_id


def is_model_free(model_key: str) -> bool:
    """判断模型是否为免费模型。"""
    model = MODELS[model_key]
    return "(free)" in model.get("model_id", "")


# ── 密码验证 ────────────────────────────────────────────

def generate_password_hash(password: str) -> str:
    """生成密码的 SHA-256 哈希值。

    用法：
        命令行: echo -n "mypassword" | sha256sum
        Python:  generate_password_hash("mypassword")

    将输出的 64 位十六进制字符串填入 .env 文件的 PASSWORD 变量。
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def check_model_password() -> bool:
    """验证付费模型密码。用户输入正确密码后缓存到 session_state。

    安全机制：
    - .env 中 PASSWORD 存储 SHA-256 哈希值（64 位十六进制字符串）
    - 用户输入经 SHA-256 哈希后与存储值比对，全程无明文密码参与比较
    - 使用 hmac.compare_digest 防止时序攻击
    - 未配置 PASSWORD 环境变量时跳过验证（向后兼容）
    """
    expected_hash = os.environ.get("PASSWORD", "")
    if not expected_hash:
        return True  # 未配置密码则跳过验证

    if st.session_state.get("model_authed", False):
        return True

    st.info("🔒 输入密码后可使用付费模型")
    with st.form("password_form", clear_on_submit=True):
        pwd = st.text_input("请输入访问密码", type="password")
        submitted = st.form_submit_button("确认")
        if submitted:
            pwd_hash = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
            if hmac.compare_digest(pwd_hash, expected_hash):
                st.session_state["model_authed"] = True
                st.rerun()
            else:
                st.error("❌ 密码错误")
    return False
