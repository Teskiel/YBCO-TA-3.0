# -*- coding: utf-8 -*-
"""跨机同步框架的公共设施：git 调用、机器代号解析、控制台编码、文件分类。

设计要点（改本文件前先读）：

1. **零第三方依赖**——只用标准库。本文件会在钩子（pre-push / commit-msg）里被
   `python -c` 直接调用，钩子不允许因为缺 pip 包而崩溃。
2. **全部子进程都显式解码 UTF-8 且 `errors="replace"`**——本仓库 git 数据是
   UTF-8，但 Windows 控制台默认是 GBK/CP936。直接用 `subprocess.run(text=True)`
   会在中文 commit message 上抛 UnicodeDecodeError，或把中文打成乱码。历史上
   本仓库已经吃过一次这个亏（`setup.bat` 里的 `一键环境配置` 曾被读成乱码）。
3. **本模块不做 IO 副作用**，`configure_stdout()` 是唯一例外，且必须由调用方
   显式调用。
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── 常量 ──────────────────────────────────────────────────────────────────

MACHINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,23}$")
TRAILER_MACHINE = "Machine"
TRAILER_MACHINE_REF = "Machine-Ref"
SKIP_ENV = "YBCO_SKIP_HOOKS"
MACHINE_ENV = "YBCO_MACHINE"

#: git 配置键（每台机器用 `git config --local` 写一次）
CFG_MACHINE = "ybco.machine"

#: 命令行退出码约定
EXIT_OK = 0
EXIT_BLOCK = 1          # 阻断（钩子用它让 git 中止操作）
EXIT_CONFIG_ERROR = 2   # 环境/配置不对，需要用户先跑 setup_machine.py


class ConfigError(RuntimeError):
    """机器代号未配置或注册表损坏——需要先跑 sync/setup_machine.py。"""


# ── 控制台 ────────────────────────────────────────────────────────────────

def configure_stdout() -> None:
    """把 stdout/stderr 切成 UTF-8，让中文报告在 cp936 控制台里不乱码。

    必须在打印任何中文之前调用。幂等，重复调用无害。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            # 被重定向到不支持 reconfigure 的对象（例如某些 IDE 的假 stdout）
            pass


# ── git ───────────────────────────────────────────────────────────────────

def repo_root(start: Path | None = None) -> Path:
    """定位仓库根。优先用 `git rev-parse`，失败则按脚本位置回退。"""
    out = git(["rev-parse", "--show-toplevel"], cwd=start, check=False)
    top = getattr(out, "stdout_text", "").strip()
    if out.returncode == EXIT_OK and top:
        return Path(top).resolve()
    # 回退：本文件位于 <root>/sync/_common.py
    return Path(__file__).resolve().parent.parent


def git(args, cwd=None, check=True, timeout=60):
    """跑一条 git 命令，永远以 UTF-8 解码。

    返回 CompletedProcess；`check=True` 时非零退出抛 CalledProcessError。
    不抛 `FileNotFoundError`——git 不在 PATH 时转成 CalledProcessError 风格
    的 ConfigError，方便上层统一处理。
    """
    cmd = ["git", *args]
    env = dict(os.environ)
    # 让 git 自己输出 UTF-8，并关掉中文路径的八进制转义
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("LANG", "C.UTF-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover - 环境缺 git
        raise ConfigError("找不到 git，请先安装 Git 并加入 PATH") from exc
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        raise ConfigError(f"git 命令超时（{timeout}s）：{' '.join(cmd)}") from exc

    proc.stdout_text = proc.stdout.decode("utf-8", errors="replace")  # type: ignore[attr-defined]
    proc.stderr_text = proc.stderr.decode("utf-8", errors="replace")  # type: ignore[attr-defined]
    if check and proc.returncode != EXIT_OK:
        raise subprocess.CalledProcessError(
            proc.returncode, cmd, proc.stdout_text, proc.stderr_text)
    return proc


def git_out(args, cwd=None, check=True, default=""):
    """跑 git 并返回 stdout 文本（已 strip）。失败且 check=False 时返回 default。"""
    proc = git(args, cwd=cwd, check=False)
    if proc.returncode != EXIT_OK:
        if check:
            raise subprocess.CalledProcessError(
                proc.returncode, ["git", *args],
                proc.stdout_text, proc.stderr_text)
        return default
    return proc.stdout_text.strip()


def git_write(args, cwd=None, check=True):
    """跑会写盘的 git 命令（不加 --porcelain 之类只读开关）。返回文本输出。"""
    return git_out(args, cwd=cwd, check=check)


# ── 仓库相对路径 ──────────────────────────────────────────────────────────

def to_rel(path, root: Path) -> str:
    """转成相对仓库根的 posix 路径；不在仓库内则原样返回 posix。"""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


# ── 机器注册表 ────────────────────────────────────────────────────────────

def registry_path(root: Path) -> Path:
    return root / "machines" / "machines.json"


def load_registry(root: Path) -> dict:
    """读 machines/machines.json，返回 {'machines': [...]}；缺失返回空表。

    不做 schema 校验（那是 sync/map_history.py 与测试的活），只保证不抛异常。
    """
    p = registry_path(root)
    if not p.exists():
        return {"machines": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"注册表损坏，无法解析 JSON：{p}\n  {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("machines"), list):
        raise ConfigError(f"注册表 schema 不对（应为 {{\"machines\": [...]}}）：{p}")
    return data


def registry_ids(root: Path) -> list[str]:
    return [m.get("id", "") for m in load_registry(root).get("machines", [])]


def find_machine(registry: dict, machine_id: str):
    for m in registry.get("machines", []):
        if m.get("id") == machine_id:
            return m
    return None


def detect_by_hostname(registry: dict, hostname: str | None = None):
    """按主机名匹配注册表条目（大小写不敏感）。匹配不到返回 None。"""
    host = (hostname or os.environ.get("COMPUTERNAME")
            or os.environ.get("HOSTNAME") or "").strip().lower()
    if not host:
        return None
    for m in registry.get("machines", []):
        if str(m.get("hostname", "")).strip().lower() == host:
            return m
    return None


def machine_identity(root: Path, cwd=None):
    """解析本机代号 → (machine_id, source)。

    优先级：`git config --local ybco.machine` → 环境变量 `YBCO_MACHINE`
            → 按主机名匹配注册表 → 抛 ConfigError。

    返回的 source 用于报告（"git-config" / "env" / "hostname"），
    让 `check.py` 能说清"这个代号是哪来的"，避免静默猜错机器。
    """
    cfg = git_out(["config", "--local", CFG_MACHINE], cwd=cwd or root, check=False)
    if cfg and MACHINE_ID_RE.match(cfg):
        return cfg, "git-config"

    env_id = os.environ.get(MACHINE_ENV, "").strip()
    if env_id and MACHINE_ID_RE.match(env_id):
        return env_id, "env"

    reg = load_registry(root)
    hit = detect_by_hostname(reg)
    if hit and hit.get("id"):
        return str(hit["id"]), "hostname"

    host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"
    raise ConfigError(
        "本机还没有机器代号。\n"
        f"  主机名：{host}\n"
        "  请先在本机跑一次（只需一次）：\n"
        "      python sync/setup_machine.py --id <代号>\n"
        f"  已登记的代号：{', '.join(registry_ids(root)) or '(注册表为空)'}\n"
        "  新机器请用 --add 追加：python sync/setup_machine.py --add --id <代号>"
    )


def machine_ref(registry: dict, machine_id: str) -> str:
    """生成 trailer 用的机器指纹：<主机名>/<用户名>/<python>。"""
    import platform
    m = find_machine(registry, machine_id) or {}
    host = m.get("hostname") or os.environ.get("COMPUTERNAME", "unknown-host")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"
    py = ".".join(str(x) for x in sys.version_info[:3])
    return f"{host}/{user}/{m.get('os', platform.system())}/py{py}"


# ── trailer 读写 ──────────────────────────────────────────────────────────

def parse_trailers(message: str) -> dict[str, str]:
    """从 commit/tag message 里抽出 trailer 键值（大小写不敏感，后者覆盖前者）。"""
    out: dict[str, str] = {}
    for line in message.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.+?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def trailer_value(message: str, key: str) -> str | None:
    return parse_trailers(message).get(key.lower())


def has_machine_trailer(message: str) -> bool:
    return bool(trailer_value(message, TRAILER_MACHINE))


# ── 文件分类（"什么才是该入库的"）────────────────────────────────────────

#: 明确不该被 pre-push 唠叨的未跟踪路径（机器配置、工具私有文件）
UNTRACKED_WHITELIST = (
    re.compile(r"(^|/)CLAUDE\.local\.md$"),
    re.compile(r"(^|/)\.claude/"),
    re.compile(r"\.machine\.json$"),
    re.compile(r"^machines/machines\.local\.json$"),
    re.compile(r"(^|/)__pycache__/"),
    re.compile(r"\.(pyc|pyo)$"),
)

#: 值得提醒"该入库却还没入库"的源代码类扩展名
CODE_EXT = (".py", ".md", ".json", ".bat", ".ps1", ".sh", ".txt", ".yml", ".yaml",
            ".toml", ".cfg", ".ini")

#: 一律不入库的数据/产物类扩展名
ARTIFACT_EXT = (".s2p", ".h5", ".hdf5", ".pkl", ".npz", ".png", ".jpg", ".jpeg",
                ".pdf", ".docx", ".pptx", ".zip", ".rar", ".csv", ".xlsx")


def looks_like_code(path: str) -> bool:
    low = path.lower()
    return low.endswith(CODE_EXT) and not low.endswith(ARTIFACT_EXT)


def is_whitelisted_untracked(path: str) -> bool:
    posix = path.replace("\\", "/")
    return any(rx.search(posix) for rx in UNTRACKED_WHITELIST)


__all__ = [
    "ConfigError", "EXIT_OK", "EXIT_BLOCK", "EXIT_CONFIG_ERROR",
    "MACHINE_ID_RE", "TRAILER_MACHINE", "TRAILER_MACHINE_REF",
    "SKIP_ENV", "MACHINE_ENV", "CFG_MACHINE",
    "configure_stdout", "repo_root", "git", "git_out", "git_write", "to_rel",
    "registry_path", "load_registry", "registry_ids", "find_machine",
    "detect_by_hostname", "machine_identity", "machine_ref",
    "parse_trailers", "trailer_value", "has_machine_trailer",
    "looks_like_code", "is_whitelisted_untracked", "UNTRACKED_WHITELIST",
    "CODE_EXT", "ARTIFACT_EXT",
]
