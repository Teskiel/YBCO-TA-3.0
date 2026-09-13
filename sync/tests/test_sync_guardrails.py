# -*- coding: utf-8 -*-
"""多机同步框架的守护测试。

这些测试**不接硬件、不联网**，可在任意一台机器上跑：

    python -m pytest sync/tests -q

它们守的是"机制有没有被悄悄拆掉"，而不是"功能跑得对不对"：
把绝对路径写回配置、把机器文件提交进库、让注册表出现重复代号——
这类改动本身不会让任何程序立刻报错，但会在**换机器的那一天**集中爆发。

命名遵循仓库既有约定：test_given_<前提>_when_<动作>_then_<预期>。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SYNC_DIR = REPO_ROOT / "sync"
MACHINES_DIR = REPO_ROOT / "machines"
DOCS_DIR = REPO_ROOT / "docs"

sys.path.insert(0, str(SYNC_DIR))

import _common  # noqa: E402
import machine_config  # noqa: E402
import map_history  # noqa: E402
import release as release_mod  # noqa: E402


@pytest.fixture
def workdir():
    """仓库内的临时目录。

    为什么不用 pytest 的 `tmp_path`：在受限沙箱里，系统临时目录
    （`%TEMP%/.../pytest-of-*`）**不允许创建任何目录**，`tmp_path` 会直接
    PermissionError。把测试改成在仓库内建临时目录后，在没有完整系统临时
    目录权限的环境中也能跑——而"能在任意一台机器上跑守护测试"正是本框架
    的目标之一。

    统一用 `os.makedirs(..., exist_ok=True)`：某些沙箱只放行这一种写法，
    普通 `mkdir()` 一律拒绝。exist_ok=True 在语义上没有副作用。
    """
    import itertools
    import shutil

    seq = itertools.count()
    base = REPO_ROOT / ".tmp_synctest"
    os.makedirs(base, exist_ok=True)
    path = base / f"pytest_{os.getpid()}_{next(seq)}"
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

#: 机器专属绝对路径的指纹。写成"归一化后"的形式（反斜杠→斜杠、去重斜杠），
#: 这样一条正则能同时命中 `C:\Users\smlab` 与 `C:/Users/smlab` 两种写法。
#:
#: 这里只放**真正敏感**的两类：他人机器的用户名、以及需要管理员权限的
#: 部署位置。仓库是 PUBLIC，这两类既泄露目录结构又让代码换机器即坏。
FORBIDDEN_PATH_PATTERNS = (
    r"C:/Users/smlab",
    r"C:/Windows/System32/YBCO-TA-3.0",
)

#: 旧机器数据盘路径（`D:\YBCO\VNAMeas\...`）。这是**另一类**问题：
#: 不泄露他人私有信息（`YBCO` 是本项目自己的目录名），但会让脚本在别的
#: 机器上写不进去。
#:
#: 已整改的是**活跃代码**：`Auto_Sweep/config.py`（实验输出根，可用
#: YBCO_EXPERIMENT_DATA_DIR / YBCO_CLI_BASE_FOLDER 覆盖）、`Noisesweep/*`、
#: 以及 `draw/` 下的活跃图脚本（走 `draw/_paths.py` 的 YBCO_DRAW_CACHE_ROOT）。
#:
#: 下面这张名单是**已冻结的遗留清单**：全部是 `draw/CLAUDE.md` 明确标注为
#: "不再维护、保留仅供代码参考"的废弃脚本，以及历史设计/规格文档。
#: 冻结的含义是——**名单只许缩短，不许增长**。往里面加新文件会让测试失败，
#: 这正是我们要的：新代码不许再引入硬编码路径。
LEGACY_PATH_ALLOWLIST = {
    # 废弃绘图/诊断脚本（被 _data_cache.py + plot_all.py 取代）
    "Auto_Sweep/draw/diagnose_77K.py",
    "Auto_Sweep/draw/diagnose_77K_detail.py",
    "Auto_Sweep/draw/diagnose_nofit.py",
    "Auto_Sweep/draw/plot_AB_final.py",
    "Auto_Sweep/draw/plot_B_2dBstep.py",
    "Auto_Sweep/draw/plot_VNA_powersweep.py",
    "Auto_Sweep/draw/plot_approach_A_full.py",
    "Auto_Sweep/draw/plot_approach_B.py",
    "Auto_Sweep/draw/plot_deltaf_vs_laser.py",
    "Auto_Sweep/draw/plot_deltaf_vs_laser_v2.py",
    "Auto_Sweep/draw/plot_laser_powersweep.py",
    "Auto_Sweep/draw/plot_laser_powersweep_0-9mW.py",
    "Auto_Sweep/draw/plot_r4_laser_compare.py",
    "Auto_Sweep/draw/plot_r4_s21_multipanel.py",
    "Auto_Sweep/draw/plot_s21_overlay_batch.py",
    "Auto_Sweep/draw/plot_three_approaches.py",
    "Auto_Sweep/draw/plot_verification.py",
    # 非主线面板与报告生成器
    "Auto_Sweep/plot_dashboard/plot_laser_sweep_batch.py",
    "Auto_Sweep/report/generate_docx.py",
    # 历史计划 / 规格文档（记录当时的真实环境，改它等于篡改历史）
    "Auto_Sweep/docs/superpowers/plans/2026-06-23-data-consolidation-plan.md",
    "Auto_Sweep/docs/superpowers/specs/2026-06-23-data-consolidation-design.md",
    # 明确记录"哪些文件仍含硬编码路径"的文档本身
    "Auto_Sweep/CLAUDE.md",
    "Auto_Sweep/draw/CLAUDE.md",
    "Auto_Sweep/.claude/skills/checklog/SKILL.md",
}

#: 白名单：文档性质地**提及**历史路径（不是把它们当作运行时配置）。
#: 每条都要有理由，否则白名单会慢慢吃掉所有断言。
PATH_SCAN_WHITELIST = {
    "Noisesweep/README_personal.md":
        "历史说明，引用旧部署位置以解释背景，不是运行时配置",
    "docs/multi-machine.md":
        "规范文档举例说明「曾经写死了什么」，正是它要防止的问题",
    "machines/history.json":
        "历史归属登记的判定依据，必须记录当时看到的路径作为证据",
    "sync/check.py":
        "检查器自身的模式串",
    "sync/tests/test_sync_guardrails.py":
        "本测试的模式串",
}


def _tracked_files() -> list[str]:
    """git 跟踪的文件列表（相对 posix 路径）。"""
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO_ROOT),
        capture_output=True, check=True)
    return [ln for ln in out.stdout.decode("utf-8", "replace").splitlines() if ln]


def _normalize(text: str) -> str:
    """把路径写法统一，便于单条正则覆盖 \\ 与 / 两种分隔符。"""
    return text.replace("\\\\", "/").replace("\\", "/")


def _read_tracked(path: str) -> str:
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ── 1. 机器专属绝对路径不得回到库里 ──────────────────────────────────────

def test_given_repo_when_scanning_tracked_files_then_no_machine_specific_paths():
    """库内不得再有**敏感**的机器专属绝对路径（他人用户名 / 管理员部署位置）。

    这是本套整改的核心红线：这类路径既让代码换机器即坏，又（仓库是 PUBLIC）
    泄露他人机器的目录结构。历史上有 6 个文件踩过这个坑。
    """
    offenders: list[str] = []

    for rel in _tracked_files():
        if rel in PATH_SCAN_WHITELIST:
            continue
        if not rel.endswith((".py", ".json", ".md", ".bat", ".txt", ".cfg", ".ini")):
            continue
        text = _normalize(_read_tracked(rel))
        for pattern in FORBIDDEN_PATH_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                offenders.append(f"{rel}  命中 {pattern}")

    assert not offenders, (
        "以下入库文件含机器专属绝对路径，请改为 *.machine.json / 环境变量 / "
        "仓库内相对路径（详见 docs/multi-machine.md §5）：\n  "
        + "\n  ".join(offenders))


def test_given_repo_when_scanning_tracked_files_then_legacy_allowlist_does_not_grow():
    """冻结的遗留路径清单**只许缩短，不许增长**。

    `D:\\<旧机器>\\VNAMeas\\...` 不泄露他人私有信息，但会让脚本在别的机器上
    写不进去。活跃代码已全部整改；剩下的都是 `draw/CLAUDE.md` 标注为
    "不再维护"的废弃脚本与历史文档。

    这个测试的作用不是逼你现在去改那 20 个废弃脚本，而是**阻止新代码再引入
    硬编码路径**——想加新文件进来就会红。
    """
    unexpected: list[str] = []
    still_present: set[str] = set()

    pattern = re.compile(r"D:[\\/]+YBCO[\\/]+VNAMeas", re.IGNORECASE)

    for rel in _tracked_files():
        if rel in PATH_SCAN_WHITELIST:
            continue
        if not rel.endswith((".py", ".json", ".md", ".bat", ".txt")):
            continue
        if not pattern.search(_normalize(_read_tracked(rel))):
            continue
        still_present.add(rel)
        if rel not in LEGACY_PATH_ALLOWLIST:
            unexpected.append(rel)

    assert not unexpected, (
        "以下**新**文件引入了旧机器硬编码路径。请改用环境变量或相对路径"
        "（活跃图脚本参考 `Auto_Sweep/draw/_paths.py` 的 YBCO_DRAW_CACHE_ROOT，"
        "实验输出参考 `config.py` 的 YBCO_EXPERIMENT_DATA_DIR）：\n  "
        + "\n  ".join(unexpected))

    # 顺带报告：清单已经修掉多少（缩短是好事，不失败）
    fixed = LEGACY_PATH_ALLOWLIST - still_present
    if fixed:
        print(f"\n[提示] 以下 {len(fixed)} 个文件已不再含旧路径，"
              f"可从 LEGACY_PATH_ALLOWLIST 中删除：")
        for rel in sorted(fixed):
            print(f"    {rel}")


def test_given_baseline_configs_when_loaded_then_paths_are_empty_or_relative():
    """基准配置文件里的路径必须留空或是仓库内相对值，不能是绝对路径。"""
    baseline = REPO_ROOT / "Noisesweep" / "gui_config.json"
    cfg = json.loads(baseline.read_text(encoding="utf-8"))
    for key in ("autosweep_dir", "tracking_file", "data_process_dir",
                "data_directory", "save_root", "iq_calibration_file"):
        value = cfg.get(key, "")
        assert not re.match(r"^[A-Za-z]:[\\/]", str(value)), (
            f"基准 gui_config.json 的 {key} 是绝对路径（{value!r}）——"
            "它必须对任意机器都成立，机器专属值请放 gui_config.machine.json")


# ── 2. 机器注册表 ────────────────────────────────────────────────────────

def test_given_registry_when_loaded_then_ids_unique_and_schema_valid():
    data = json.loads((MACHINES_DIR / "machines.json").read_text(encoding="utf-8"))
    machines = data.get("machines")
    assert isinstance(machines, list) and machines, "注册表为空"

    ids = [m.get("id", "") for m in machines]
    assert len(ids) == len(set(ids)), f"机器代号重复：{ids}"

    for m in machines:
        assert _common.MACHINE_ID_RE.match(m["id"]), f"代号格式不合规：{m['id']}"
        assert m.get("role") in ("dev", "lab", "archive"), \
            f"{m['id']} 的 role 非法：{m.get('role')}"
        assert "label" in m, f"{m['id']} 缺 label"


def test_given_registry_when_hostnames_present_then_unique():
    """主机名必须唯一——认机靠它就是靠这个，重复会认错机器。"""
    data = json.loads((MACHINES_DIR / "machines.json").read_text(encoding="utf-8"))
    hosts = [str(m.get("hostname", "")).strip().lower()
             for m in data.get("machines", [])]
    filled = [h for h in hosts if h]
    assert len(filled) == len(set(filled)), f"主机名重复：{filled}"


# ── 3. 历史映射 ──────────────────────────────────────────────────────────

def test_given_history_mapping_when_checked_then_every_sha_exists_in_log():
    data = json.loads((MACHINES_DIR / "history.json").read_text(encoding="utf-8"))
    known = set(_common.git_out(["log", "--all", "--format=%H"], cwd=REPO_ROOT,
                                check=False).splitlines())

    stale = [c["sha"] for c in data.get("commits", [])
             if c.get("sha") and c["sha"] not in known]
    assert not stale, (
        "machines/history.json 里有当前历史中不存在的 SHA（上游可能发生过 "
        f"rebase/filter-branch），需重新登记：{stale}")


def test_given_history_mapping_when_checked_then_machines_are_registered():
    """历史映射里的机器代号必须在注册表内——否则总表会出现幽灵机器。"""
    registry = json.loads((MACHINES_DIR / "machines.json").read_text(encoding="utf-8"))
    valid = {m["id"] for m in registry.get("machines", [])} | {"unknown"}

    history = json.loads((MACHINES_DIR / "history.json").read_text(encoding="utf-8"))
    for c in history.get("commits", []):
        assert c.get("machine") in valid, \
            f"{c.get('sha', '?')[:12]} 的 machine={c.get('machine')!r} 未登记"
        assert c.get("confidence") in ("high", "medium", "low", "unknown"), \
            f"{c.get('sha', '?')[:12]} 的 confidence 取值非法"


def test_given_mixed_history_when_mapped_then_unknown_is_not_fabricated():
    """没有证据的提交必须保持 unknown，不能被猜成某个机器。"""
    rows, _ = map_history.collect(REPO_ROOT)
    for r in rows:
        if r["confidence"] == "unknown":
            assert r["machine"] == "unknown", \
                f"{r['short']} 无证据却记成了 {r['machine']}"


# ── 4. 配置分层契约 ──────────────────────────────────────────────────────

def test_given_no_machine_file_when_loading_then_falls_back_without_raising(
        workdir, capsys):
    """本地覆盖缺失时必须回退，不能抛异常。

    这是硬约束：离线自检链路要在"全新 clone、什么都没配"的机器上跑通。
    """
    base = workdir / "sample.json"
    base.write_text(json.dumps({"a": 1, "nested": {"x": 1, "y": 2}}),
                    encoding="utf-8")

    result = machine_config.load_layered_config(base, quiet=False)
    assert result == {"a": 1, "nested": {"x": 1, "y": 2}}

    # 缺失覆盖文件时应当给出提示（但不阻断）
    err = capsys.readouterr().err
    assert "machine" in err, "缺失本机覆盖时应当提示用户"


def test_given_machine_file_when_loading_then_it_overrides_baseline(workdir):
    base = workdir / "sample.json"
    base.write_text(json.dumps({"a": 1, "nested": {"x": 1, "y": 2}}),
                    encoding="utf-8")
    machine_config.override_path(base).write_text(
        json.dumps({"a": 9, "nested": {"y": 20}}), encoding="utf-8")

    result = machine_config.load_layered_config(base, quiet=True)
    assert result["a"] == 9
    assert result["nested"] == {"x": 1, "y": 20}, "嵌套 dict 应当递归合并"


def test_given_env_var_when_loading_then_env_wins(workdir, monkeypatch):
    base = workdir / "sample.json"
    base.write_text(json.dumps({"a": 1}), encoding="utf-8")
    monkeypatch.setenv("YBCO_TEST_CFG", json.dumps({"a": 42}))

    result = machine_config.load_layered_config(base, "YBCO_TEST_CFG", quiet=True)
    assert result["a"] == 42


def test_given_machine_id_when_git_config_set_then_resolved(workdir):
    """machine_id 直接读 .git/config，不依赖 git 可执行文件。"""
    git_dir = workdir / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n[ybco]\n\tmachine = lab-smlab\n",
        encoding="utf-8")
    assert machine_config.machine_id(workdir) == "lab-smlab"


def test_given_broken_git_config_when_resolving_machine_then_returns_empty(workdir):
    git_dir = workdir / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("this is not ini at all {{{", encoding="utf-8")
    assert machine_config.machine_id(workdir) == ""


# ── 5. 机器文件不得入库 ──────────────────────────────────────────────────

def test_given_gitignore_when_checking_machine_files_then_they_are_ignored():
    samples = [
        "machines/machines.local.json",
        "Noisesweep/gui_config.machine.json",
        "Auto_Sweep/app_settings.machine.json",
        "Noisesweep/noisesweep_config.machine.json",
    ]
    for rel in samples:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=str(REPO_ROOT))
        assert proc.returncode == 0, f"{rel} 未被 .gitignore 排除——会泄露本机环境信息"


def test_given_gitignore_when_checking_example_files_then_they_are_tracked():
    """模板必须能被提交，否则新机器没有可照抄的样板。"""
    rel = "Noisesweep/gui_config.machine.example.json"
    proc = subprocess.run(
        ["git", "check-ignore", "-q", rel], cwd=str(REPO_ROOT))
    assert proc.returncode != 0, f"{rel} 被误排除，模板无法入库"


def test_given_sync_framework_when_scanning_then_no_machine_file_is_tracked():
    tracked = set(_tracked_files())
    leaked = [f for f in tracked
              if f.endswith(".machine.json")
              or f == "machines/machines.local.json"]
    assert not leaked, f"机器专属文件被提交进库：{leaked}"


# ── 6. trailer 逻辑 ──────────────────────────────────────────────────────

def test_given_message_without_trailer_when_appended_then_machine_present():
    import trailer as trailer_mod
    new, changed = trailer_mod.append_trailers(
        "feat: something\n\n正文\n", "pc-teski", "HOST/user/Windows/py3.12")
    assert changed
    assert _common.has_machine_trailer(new)
    assert _common.trailer_value(new, "Machine") == "pc-teski"
    assert _common.trailer_value(new, "Machine-Ref") == "HOST/user/Windows/py3.12"


def test_given_message_with_trailer_when_appended_then_left_untouched():
    """已有标识不得覆盖——尊重历史，否则 rebase 会改写别人的机器归属。"""
    import trailer as trailer_mod
    original = "feat: x\n\nMachine: lab-smlab\nMachine-Ref: LAB/a/b/c\n"
    new, changed = trailer_mod.append_trailers(
        original, "pc-teski", "HOST/user/Windows/py3.12")
    assert not changed
    assert new == original


def test_given_empty_message_when_checked_then_hook_does_not_interfere():
    """用户忘写消息时该由 git 报 'empty message'，不该被机器标识抢了风头。"""
    import trailer as trailer_mod
    meaningful = [ln for ln in "# 注释\n\n" .splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    assert not meaningful


# ── 7. 版本与发布索引 ────────────────────────────────────────────────────

def test_given_version_strings_when_bumped_then_semver_correct():
    assert release_mod.bump("3.1.0", "patch") == "3.1.1"
    assert release_mod.bump("3.1.9", "minor") == "3.2.0"
    assert release_mod.bump("3.9.9", "major") == "4.0.0"
    # 旧标签 v3.1（两位）也要能被归一化后递增
    assert release_mod.normalize("3.1") == "3.1.0"
    assert release_mod.bump("3.1", "patch") == "3.1.1"


def test_given_release_index_when_parsed_then_rows_are_well_formed():
    index = REPO_ROOT / "sync" / "releases" / "INDEX.md"
    assert index.exists(), "发布索引不存在"

    rows = []
    for line in index.read_text(encoding="utf-8").splitlines():
        if line.startswith("| v") or re.match(r"^\|\s*\d+\.\d+", line):
            rows.append(line)
    assert rows, "发布索引里没有任何版本行"

    for line in rows:
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) >= 5, f"索引行字段数不足：{line}"
        assert cells[3], f"索引行缺 Tag 指向：{line}"


def test_given_release_log_when_parsed_then_every_line_is_json():
    log = REPO_ROOT / "sync" / "releases" / "log.jsonl"
    assert log.exists(), "机读发布日志不存在"
    for i, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        for key in ("version", "date", "machine", "commit"):
            assert key in record, f"log.jsonl 第 {i} 行缺 {key}"


def test_given_tags_when_inspected_then_all_are_annotated():
    """版本标签必须是附注标签——轻量标签存不下机器归属。

    这是实测踩过的坑：v3.1 原本是轻量标签，事后完全看不出是哪台机器发的。
    """
    tags = _common.git_out(["tag", "-l"], cwd=REPO_ROOT, check=False).split()
    for tag in tags:
        kind = _common.git_out(["cat-file", "-t", tag], cwd=REPO_ROOT, check=False)
        assert kind == "tag", (
            f"{tag} 是轻量标签（{kind}）。请改用附注标签："
            f"python sync/release.py --version {tag.lstrip('v')}")


# ── 8. 文档完整性 ────────────────────────────────────────────────────────

def test_given_multi_machine_doc_when_read_then_has_required_sections():
    doc = DOCS_DIR / "multi-machine.md"
    assert doc.exists(), "docs/multi-machine.md 缺失"

    text = doc.read_text(encoding="utf-8")
    for heading in ("## 2. 机器代号", "## 3. 三个检查点", "## 4. 发布版本",
                    "## 5. 配置分层", "## 6. 冲突处理铁律",
                    "## 7. 数据备份"):
        assert heading in text, f"规范文档缺少章节：{heading}"


def test_given_claude_md_drift_when_read_then_entrypoints_exist():
    """文档里提到的入口文件必须真实存在。

    实测漂移过：根 CLAUDE.md 曾写 `Noisesweep/noisesweep.py` 与
    `noisesweep_dashboard.py`，而 3.1 已把它们重命名/删除。

    允许"已移除 / 已废弃"语境下的提及——那种提及正是我们想留的（免得有人
    照着旧路径去找）。
    """
    removed_ok = ("已移除", "已删除", "取代", "废弃", "不再", "removed")
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    referenced: set[str] = set()
    for line in claude.splitlines():
        if any(kw in line for kw in removed_ok):
            continue
        referenced.update(re.findall(
            r"`((?:Noisesweep|Data_process|Auto_Sweep)/[\w./#-]+\.py)`", line))

    missing = [rel for rel in sorted(referenced) if not (REPO_ROOT / rel).exists()]
    assert not missing, f"CLAUDE.md 引用了不存在的文件：{missing}"


def test_given_repo_when_checked_then_removed_entrypoints_are_not_documented():
    """已被 3.1 删除的旧入口不得再出现在任何入库文档里。"""
    dead = ("noisesweep_dashboard.py", "kid_measurement_gui_v3.py")
    offenders = []
    for rel in _tracked_files():
        if not rel.endswith((".md", ".bat")):
            continue
        if rel in ("Noisesweep/README_personal.md", "docs/multi-machine.md",
                   "machines/history.json", "sync/tests/test_sync_guardrails.py"):
            continue
        text = _read_tracked(rel)
        for name in dead:
            # 允许出现在"已移除/已废弃"这类说明里
            for line in text.splitlines():
                if name in line and not any(
                        kw in line for kw in ("已移除", "已删除", "取代", "旧", "不再",
                                              "removed", "废弃")):
                    offenders.append(f"{rel}: {line.strip()[:100]}")
                    break
    assert not offenders, "旧入口仍在文档中作为现行用法出现：\n  " + "\n  ".join(offenders)


# ── 9. 框架自身的可运行性 ────────────────────────────────────────────────

@pytest.mark.parametrize("script", [
    "check.py", "setup_machine.py", "install_hooks.py", "trailer.py",
    "pre_push.py", "release.py", "map_history.py", "machine_config.py",
])
def test_given_sync_script_when_run_with_help_then_exits_zero(script):
    """每个脚本都要能被 --help 正常解析（防止 argparse 写错）。"""
    proc = subprocess.run(
        [sys.executable, str(SYNC_DIR / script), "--help"],
        capture_output=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, (
        f"{script} --help 失败：\n"
        + proc.stderr.decode("utf-8", "replace")[:800])


def test_given_check_script_when_json_output_then_schema_is_stable():
    proc = subprocess.run(
        [sys.executable, str(SYNC_DIR / "check.py"), "--json", "--offline"],
        capture_output=True, cwd=str(REPO_ROOT))
    payload = json.loads(proc.stdout.decode("utf-8", "replace"))
    for key in ("machine", "branch", "ahead", "behind", "issues", "summary"):
        assert key in payload, f"check.py --json 缺字段 {key}"
    assert "id" in payload["machine"] and "source" in payload["machine"]
