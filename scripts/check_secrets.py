#!/usr/bin/env python3
"""密钥泄露防护 — pre-commit hook

扫描 git 暂存区文件，检测常见密钥/凭据模式，阻止含有敏感信息的提交。

安装方式：
    # 复制为 pre-commit hook
    cp scripts/check_secrets.py .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit

    # 或手动运行检查
    python scripts/check_secrets.py
"""

import re
import subprocess
import sys
from pathlib import Path

# 密钥检测模式（覆盖主流 API Key / Token / Secret 格式）
SECRET_PATTERNS = [
    # 通用 API Key（sk-xxx、ak-xxx、ark-xxx 等，长度 >= 20）
    (re.compile(r"""(?:sk|ak|ark|key|token|secret|api[_-]?key)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?""", re.IGNORECASE),
     "通用 API Key/Secret"),

    # Bearer Token
    (re.compile(r"""Bearer\s+[A-Za-z0-9_\-.]{20,}""", re.IGNORECASE),
     "Bearer Token"),

    # AWS Access Key（AKIA 开头，20 字符）
    (re.compile(r"AKIA[0-9A-Z]{16}"),
     "AWS Access Key"),

    # GitHub Token（ghp_/gho_/ghu_/ghs_/ghr_ 开头）
    (re.compile(r"gh[opusr]_[A-Za-z0-9_]{36,}"),
     "GitHub Token"),

    # 火山方舟 ARK Key（ark-UUID 格式）
    (re.compile(r"ark-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE),
     "火山方舟 ARK Key"),

    # bcrypt 哈希（可能是密码哈希泄露）
    (re.compile(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}"),
     "bcrypt 密码哈希"),

    # JSONBin Master Key（$2a$ 开头的 bcrypt 格式）
    (re.compile(r"""\$2[aby]\$[./A-Za-z0-9]{50,}"""),
     "加密密钥/哈希"),

    # 魔搭社区 Token（ms-UUID 格式）
    (re.compile(r"ms-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE),
     "ModelScope Token"),
]

# 白名单文件（不需要检查）
WHITELIST_FILES = {
    ".env.example",
    ".env.template",
    "check_secrets.py",
    "secret_scanner.py",
}

# 白名单值（示例/占位符，不算真实密钥）
WHITELIST_VALUES = {
    "your_cherryin_api_key_here",
    "your_mimo_api_key_here",
    "your_ark_api_key_here",
    "your_modelscopes_token_here",
    "your_password_here",
    "your_jsonbin_bin_id_here",
    "your_jsonbin_api_key_here",
    "example",
    "placeholder",
    "changeme",
    "xxx",
}


def is_git_repo() -> bool:
    """判断当前目录是否在 git 仓库中。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_staged_files():
    """获取 git 暂存区中的文件列表。

    Returns:
        list[str]: 暂存区文件列表（可能为空）
        None: 不在 git 仓库中或 git 不可用
    """
    if not is_git_repo():
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_staged_content(filepath: str) -> str:
    """获取文件在暂存区中的内容。"""
    try:
        result = subprocess.run(
            ["git", "show", f":{filepath}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    # 回退：直接读文件
    try:
        return Path(filepath).read_text(encoding="utf-8", errors="ignore")
    except (OSError, IOError):
        return ""


def is_whitelisted_value(value: str) -> bool:
    """判断值是否为白名单占位符。"""
    lower = value.lower().strip()
    return (
        lower in WHITELIST_VALUES
        or lower.startswith("your_")
        or lower.endswith("_here")
        or lower == "placeholder"
        or len(lower) < 10
    )


def scan_content(content: str, filename: str) -> list[str]:
    """扫描文件内容中的密钥模式。"""
    findings = []
    for pattern, desc in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            matched_text = match.group(0)
            # 跳过白名单值
            if is_whitelisted_value(matched_text):
                continue
            # 找到匹配的行号
            line_num = content[:match.start()].count("\n") + 1
            # 脱敏显示（只显示前 8 字符）
            redacted = matched_text[:8] + "..." if len(matched_text) > 8 else matched_text
            findings.append(f"  {filename}:{line_num} — {desc} [{redacted}]")
    return findings


def scan_file(filepath: str) -> list[str]:
    """扫描单个文件。"""
    filename = Path(filepath).name
    if filename in WHITELIST_FILES:
        return []
    # 跳过二进制文件
    binary_exts = {".pyc", ".pyo", ".so", ".dll", ".exe", ".png", ".jpg", ".gif", ".woff", ".ttf"}
    if Path(filepath).suffix.lower() in binary_exts:
        return []

    content = get_staged_content(filepath)
    if not content:
        return []
    return scan_content(content, filepath)


def main() -> int:
    """主入口。返回 0 表示通过，1 表示发现密钥。"""
    staged_files = get_staged_files()

    if staged_files is None or len(staged_files) == 0:
        # 非 git 环境或无暂存文件（独立运行模式），直接检查 .env
        env_path = Path(".env")
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8", errors="ignore")
            findings = scan_content(content, ".env")
            if findings:
                print("⚠️  密钥泄露检测：")
                print("   .env 文件中发现真实密钥，请确保 .env 在 .gitignore 中")
                for f in findings:
                    print(f)
                print("\n   提示：此脚本作为 pre-commit hook 安装可自动阻止密钥提交")
                return 1
        print("✅ 密钥泄露检测通过")
        return 0

    # pre-commit hook 模式：扫描暂存区文件
    all_findings = []
    for filepath in staged_files:
        all_findings.extend(scan_file(filepath))

    if all_findings:
        print("🚫 密钥泄露检测失败！")
        print("   暂存区中发现疑似真实密钥，请检查以下位置：\n")
        for finding in all_findings:
            print(finding)
        print("\n   如果确认为占位符或误报，请使用 git commit --no-verify 跳过检查")
        print("   建议：将敏感信息放入 .env 文件（已在 .gitignore 中）")
        return 1

    print("✅ 密钥泄露检测通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
