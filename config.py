"""医学教材知识库 — 配置模块

包含模型配置、API Key 管理、密码验证等。
"""

import os
import stat
import hashlib
import hmac
import logging
import subprocess
from pathlib import Path

import bcrypt
import streamlit as st

logger = logging.getLogger(__name__)


# ── .env 安全检查 ──────────────────────────────────────

def check_env_security() -> None:
    """启动时检查 .env 文件安全状态，发现风险时记录警告。

    检查项：
    1. .env 是否在 .gitignore 中
    2. .env 是否曾被提交到 git 历史
    3. .env 文件权限是否过于宽松（非 Windows）
    """
    env_path = Path(".env")
    if not env_path.exists():
        return

    # 1. 检查 .gitignore 是否包含 .env
    gitignore_path = Path(".gitignore")
    if gitignore_path.exists():
        gitignore_content = gitignore_path.read_text(encoding="utf-8", errors="ignore")
        if ".env" not in [line.strip() for line in gitignore_content.splitlines()]:
            logger.warning(
                "🔴 安全警告：.env 未在 .gitignore 中，"
                "真实 API 密钥可能被提交到版本控制！"
            )

    # 2. 检查 .env 是否在 git 历史中
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--full-history", "--", ".env"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.warning(
                "🔴 安全警告：.env 曾被提交到 git 历史中！"
                "所有密钥应视为已泄露，请立即轮换。"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass  # 非 git 环境或 git 不可用，跳过

    # 3. 检查文件权限（非 Windows）
    if os.name != "nt":
        try:
            file_stat = os.stat(env_path)
            mode = file_stat.st_mode
            if mode & stat.S_IROTH or mode & stat.S_IWOTH:
                logger.warning(
                    "🟡 安全警告：.env 文件权限过于宽松（其他用户可读写），"
                    "建议执行：chmod 600 .env"
                )
            elif mode & stat.S_IRGRP or mode & stat.S_IWGRP:
                logger.info(
                    "🟡 安全提示：.env 文件组用户可读写，"
                    "建议执行：chmod 600 .env"
                )
        except OSError:
            pass


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
    """生成密码的 bcrypt 哈希值（含随机盐）。

    用法：
        Python:  generate_password_hash("mypassword")

    将输出的 bcrypt 字符串（以 $2b$ 开头，60 字符）填入 .env 文件的 PASSWORD 变量。
    """
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def _is_bcrypt_hash(hash_str: str) -> bool:
    """判断是否为 bcrypt 哈希格式（以 $2b$、$2a$ 或 $2y$ 开头）。"""
    return hash_str.startswith(("$2b$", "$2a$", "$2y$"))


def _is_sha256_hash(hash_str: str) -> bool:
    """判断是否为 SHA-256 哈希格式（64 位十六进制字符串）。"""
    return len(hash_str) == 64 and all(c in "0123456789abcdef" for c in hash_str)


def check_model_password() -> bool:
    """验证付费模型密码。用户输入正确密码后缓存到 session_state。

    安全机制：
    - .env 中 PASSWORD 存储 bcrypt 哈希值（推荐，含随机盐）
    - 兼容旧版 SHA-256 哈希值（64 位十六进制字符串），便于平滑迁移
    - bcrypt 使用 bcrypt.checkpw 进行安全比对
    - SHA-256 使用 hmac.compare_digest 防止时序攻击
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
            matched = False
            if _is_bcrypt_hash(expected_hash):
                matched = bcrypt.checkpw(
                    pwd.encode("utf-8"),
                    expected_hash.encode("utf-8"),
                )
            elif _is_sha256_hash(expected_hash):
                pwd_hash = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
                matched = hmac.compare_digest(pwd_hash, expected_hash)
            else:
                logger.warning("PASSWORD 环境变量格式无法识别，拒绝访问")
                matched = False

            if matched:
                st.session_state["model_authed"] = True
                st.rerun()
            else:
                st.error("❌ 密码错误")
    return False
