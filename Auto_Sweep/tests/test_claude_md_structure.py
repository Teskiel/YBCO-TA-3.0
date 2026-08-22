"""测试 CLAUDE.md 分层文档结构 — 验证文件存在、行数限制、章节分配、交叉引用和无信息丢失。

遵循 BDD 命名规范: test_given_<precondition>_when_<action>_then_<expected_result>
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── 路径常量 ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # 仓库根

MAIN_CLAUDE_MD = PROJECT_ROOT / "CLAUDE.md"
UI_CLAUDE_MD = PROJECT_ROOT / "ui" / "CLAUDE.md"
TESTS_CLAUDE_MD = PROJECT_ROOT / "tests" / "CLAUDE.md"
DRAW_CLAUDE_MD = PROJECT_ROOT / "draw" / "CLAUDE.md"

ALL_NEW_FILES = [MAIN_CLAUDE_MD, UI_CLAUDE_MD, TESTS_CLAUDE_MD, DRAW_CLAUDE_MD]

# ── 原 CLAUDE.md 章节 → 新文件映射 ─────────────────────────────────────

# 每个原 ## 章节应该出现在哪个新 CLAUDE.md 中
# None 表示该章节被合并/移除（在 no-info-loss 测试中特殊处理）
SECTION_DESTINATION = {
    "Project Overview": MAIN_CLAUDE_MD,
    "File Map": MAIN_CLAUDE_MD,
    "Architecture: Layered Dependency Graph": MAIN_CLAUDE_MD,
    "Two Laser Drivers": MAIN_CLAUDE_MD,
    "Two PID Modules": MAIN_CLAUDE_MD,       # 合并到 PID Strategy
    "Temperature Diagnostics": MAIN_CLAUDE_MD,
    "Three Experiment Runners": MAIN_CLAUDE_MD,
    "Entry Points": MAIN_CLAUDE_MD,
    "BDD/TDD Conventions": TESTS_CLAUDE_MD,
    "GUI Architecture": UI_CLAUDE_MD,
    "Temperature Safety Interlock (LakeShoreWorker)": UI_CLAUDE_MD,
    "VNA Page Frequency Controls": UI_CLAUDE_MD,
    "Experiment Data Directory Structure": MAIN_CLAUDE_MD,
    "Preset System": UI_CLAUDE_MD,
    "Hardware & VISA Addresses": MAIN_CLAUDE_MD,
    "Temperature Stability System": MAIN_CLAUDE_MD,
    "Experiment Stability Controller (GUI runner)": UI_CLAUDE_MD,
    "Memory Monitoring System": MAIN_CLAUDE_MD,
    "Auto-Reconnect Mechanism": UI_CLAUDE_MD,
    "Dashboard Button Styling API": UI_CLAUDE_MD,
    "PID Strategy": MAIN_CLAUDE_MD,
    "LakeShore Duck-Typing Pattern": MAIN_CLAUDE_MD,
    "Common Modifications": MAIN_CLAUDE_MD,
    "Dependencies": MAIN_CLAUDE_MD,
    "Environment Setup": MAIN_CLAUDE_MD,
}

# 主 CLAUDE.md 必须包含的核心章节关键字（用于快速验证）
CORE_SECTIONS = [
    "Project Overview",
    "File Map",
    "Architecture",
    "Laser Driver",
    "PID",
    "Temperature Diagnostics",
    "Experiment Runner",
    "Entry Point",
    "Stability",
    "Memory Monitor",
    "LakeShore Duck-Typing",
    "Common Modifications",
    "Dependencies",
    "Hardware",
    "Experiment Data Directory",
]

# UI CLAUDE.md 必须包含的章节关键字
# 每组中至少匹配一个即视为通过（支持中英双语）
UI_SECTIONS = [
    ["GUI Architecture", "GUI 架构"],
    ["Safety Interlock", "安全联锁"],
    ["Frequency Control"],
    ["Stability Controller", "ExperimentStabilityController"],
    ["Reconnect", "重连"],
    ["Button Styling"],
    ["Preset"],
]

# Tests CLAUDE.md 必须包含的章节关键字（每组至少匹配一个）
TESTS_SECTIONS = [
    ["BDD"],
    ["TDD"],
    ["test naming", "测试命名", "test_given_"],
    ["Test layer", "测试分层"],
    ["Mock"],
    ["Synthetic", "合成"],
]

# 主 CLAUDE.md 不应包含的 UI 细节关键字（已分流）
# 注：File Map 中的文件名引用不算"细节"——那是导航用的目录树
UI_ONLY_KEYWORDS = [
    "cooling-safety rule",
    "Bidirectional sync",
    "Frequency spinboxes",
    "Unit conversion",
    "_is_connection_error",
    "_start_reconnect",
    "_user_disconnect",
    "set_device_disconnected",
    "set_device_connected",
    "set_device_error",
    "set_device_connecting",
]

# 主 CLAUDE.md 不应包含的测试细节关键字（已分流）
TEST_ONLY_KEYWORDS = [
    "Test layers",
    "Mock VISA pattern",
    "GUI testing pattern",
    "Synthetic temperature data",
    "test_given_",
]


# ── 辅助函数 ─────────────────────────────────────────────────────────────

def _read_file(path: Path) -> str:
    """读取文件内容，文件不存在则返回空字符串。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_h2_headings(text: str) -> list[str]:
    """提取所有 ## 标题（去除 ## 前缀和首尾空白）。"""
    return [m.group(1).strip() for m in re.finditer(r"^##\s+(.+)", text, re.MULTILINE)]


def _count_lines(text: str) -> int:
    """计算非空文本的行数。"""
    return len([line for line in text.splitlines() if line.strip()])


def _count_all_lines(text: str) -> int:
    """计算总行数（含空行）。"""
    return len(text.splitlines())


def _find_section_in_files(heading: str, files: list[Path]) -> bool:
    """检查给定的 ## 标题是否出现在任何目标文件中。"""
    for f in files:
        content = _read_file(f)
        if not content:
            continue
        # 检查 ## 标题是否匹配
        if heading in _extract_h2_headings(content):
            return True
        # 也检查 ### 子标题（处理合并情况）
        h3_match = re.findall(r"^###\s+(.+)", content, re.MULTILINE)
        if heading in [h.strip() for h in h3_match]:
            return True
    return False


# ── 仓库根 CLAUDE.md 测试 ────────────────────────────────────────────────

class TestAutoSweepClaudeMd:
    """主项目 CLAUDE.md (Auto_Sweep/CLAUDE.md)"""

    def test_given_project_when_checking_then_auto_sweep_claude_md_exists(self):
        """Auto_Sweep/CLAUDE.md 文件存在。"""
        assert MAIN_CLAUDE_MD.exists(), (
            f"主 CLAUDE.md 不存在于 {MAIN_CLAUDE_MD}"
        )

    def test_given_main_claude_md_when_counting_then_under_300_lines(self):
        """主 CLAUDE.md 应精简到 300 行以内（原 562 行压缩；独立仓库新增环境适配清单）。"""
        content = _read_file(MAIN_CLAUDE_MD)
        total = _count_all_lines(content)
        assert total <= 300, (
            f"主 CLAUDE.md 共 {total} 行，超过 300 行限制"
        )

    def test_given_main_claude_md_when_checking_then_has_all_core_sections(self):
        """主 CLAUDE.md 必须包含所有 CORE 章节。"""
        content = _read_file(MAIN_CLAUDE_MD)
        missing = []
        for keyword in CORE_SECTIONS:
            if keyword.lower() not in content.lower():
                missing.append(keyword)
        assert not missing, (
            f"主 CLAUDE.md 缺少核心章节关键字: {missing}"
        )

    def test_given_main_claude_md_when_checking_then_has_no_ui_detail_sections(self):
        """主 CLAUDE.md 不应包含已分流到 ui/CLAUDE.md 的细节。"""
        content = _read_file(MAIN_CLAUDE_MD)
        leaked = []
        for keyword in UI_ONLY_KEYWORDS:
            if keyword.lower() in content.lower():
                leaked.append(keyword)
        assert not leaked, (
            f"主 CLAUDE.md 包含应分流到 ui/CLAUDE.md 的关键字: {leaked}"
        )

    def test_given_main_claude_md_when_checking_then_has_no_test_detail_sections(self):
        """主 CLAUDE.md 不应包含已分流到 tests/CLAUDE.md 的细节。"""
        content = _read_file(MAIN_CLAUDE_MD)
        leaked = []
        for keyword in TEST_ONLY_KEYWORDS:
            if keyword.lower() in content.lower():
                leaked.append(keyword)
        assert not leaked, (
            f"主 CLAUDE.md 包含应分流到 tests/CLAUDE.md 的关键字: {leaked}"
        )

    def test_given_main_claude_md_when_checking_then_references_ui_claude_md(self):
        """主 CLAUDE.md 应指引读者查看 ui/CLAUDE.md。"""
        content = _read_file(MAIN_CLAUDE_MD)
        assert "ui/CLAUDE.md" in content, (
            "主 CLAUDE.md 缺少对 ui/CLAUDE.md 的引用"
        )

    def test_given_main_claude_md_when_checking_then_references_tests_claude_md(self):
        """主 CLAUDE.md 应指引读者查看 tests/CLAUDE.md。"""
        content = _read_file(MAIN_CLAUDE_MD)
        assert "tests/CLAUDE.md" in content, (
            "主 CLAUDE.md 缺少对 tests/CLAUDE.md 的引用"
        )

    def test_given_main_claude_md_when_checking_then_no_duplicate_pid_sections(self):
        """主 CLAUDE.md 不应有两个 PID 章节（原 Section 6 和 Section 22 已合并）。"""
        content = _read_file(MAIN_CLAUDE_MD)
        headings = _extract_h2_headings(content)
        # 只应有一个 PID 相关章节（合并后的 PID Strategy）
        pid_headings = [h for h in headings if "PID" in h]
        assert len(pid_headings) <= 1, (
            f"主 CLAUDE.md PID 相关章节应合并为一个，但发现 {len(pid_headings)}: {pid_headings}"
        )


# ── ui/CLAUDE.md 测试 ────────────────────────────────────────────────────

class TestUiClaudeMd:
    """GUI 子目录 CLAUDE.md (Auto_Sweep/ui/CLAUDE.md)"""

    def test_given_ui_dir_when_checking_then_ui_claude_md_exists(self):
        """ui/CLAUDE.md 文件存在。"""
        assert UI_CLAUDE_MD.exists(), (
            f"ui/CLAUDE.md 不存在于 {UI_CLAUDE_MD}"
        )

    def test_given_ui_claude_md_when_checking_then_has_gui_architecture(self):
        """ui/CLAUDE.md 包含 GUI 架构章节。"""
        content = _read_file(UI_CLAUDE_MD)
        assert "GUI Architecture" in content or "GUI 架构" in content, (
            "ui/CLAUDE.md 缺少 GUI Architecture 章节"
        )

    def test_given_ui_claude_md_when_checking_then_has_safety_interlock(self):
        """ui/CLAUDE.md 包含温度安全联锁章节。"""
        content = _read_file(UI_CLAUDE_MD)
        assert "Safety Interlock" in content or "安全联锁" in content or "cooling-safety" in content, (
            "ui/CLAUDE.md 缺少 Temperature Safety Interlock 章节"
        )

    def test_given_ui_claude_md_when_checking_then_has_stability_controller(self):
        """ui/CLAUDE.md 包含 ExperimentStabilityController 章节。"""
        content = _read_file(UI_CLAUDE_MD)
        assert "Stability Controller" in content or "ExperimentStabilityController" in content, (
            "ui/CLAUDE.md 缺少 Experiment Stability Controller 章节"
        )

    def test_given_ui_claude_md_when_checking_then_has_reconnect(self):
        """ui/CLAUDE.md 包含自动重连机制章节。"""
        content = _read_file(UI_CLAUDE_MD)
        assert "Reconnect" in content or "重连" in content or "auto-reconnect" in content.lower(), (
            "ui/CLAUDE.md 缺少 Auto-Reconnect 章节"
        )

    def test_given_ui_claude_md_when_checking_then_has_all_required_sections(self):
        """ui/CLAUDE.md 包含所有必要章节（深度检查，支持中英双语）。"""
        content = _read_file(UI_CLAUDE_MD)
        content_lower = content.lower()
        missing = []
        for keyword_group in UI_SECTIONS:
            if not any(kw.lower() in content_lower for kw in keyword_group):
                missing.append(" | ".join(keyword_group))
        assert not missing, (
            f"ui/CLAUDE.md 缺少章节关键字（每组至少需要一个）: {missing}"
        )


# ── tests/CLAUDE.md 测试 ─────────────────────────────────────────────────

class TestTestsClaudeMd:
    """测试子目录 CLAUDE.md (Auto_Sweep/tests/CLAUDE.md)"""

    def test_given_tests_dir_when_checking_then_tests_claude_md_exists(self):
        """tests/CLAUDE.md 文件存在。"""
        assert TESTS_CLAUDE_MD.exists(), (
            f"tests/CLAUDE.md 不存在于 {TESTS_CLAUDE_MD}"
        )

    def test_given_tests_claude_md_when_checking_then_has_bdd_conventions(self):
        """tests/CLAUDE.md 包含 BDD/TDD 约定。"""
        content = _read_file(TESTS_CLAUDE_MD)
        assert "BDD" in content or "TDD" in content, (
            "tests/CLAUDE.md 缺少 BDD/TDD 约定"
        )

    def test_given_tests_claude_md_when_checking_then_has_test_naming(self):
        """tests/CLAUDE.md 包含测试命名规范（test_given_X_when_Y_then_Z）。"""
        content = _read_file(TESTS_CLAUDE_MD)
        assert "test_given_" in content, (
            "tests/CLAUDE.md 缺少 BDD 测试命名规范"
        )

    def test_given_tests_claude_md_when_checking_then_has_test_layers(self):
        """tests/CLAUDE.md 包含测试分层说明。"""
        content = _read_file(TESTS_CLAUDE_MD)
        assert "Config" in content and "Algorithm" in content, (
            "tests/CLAUDE.md 缺少测试分层说明"
        )

    def test_given_tests_claude_md_when_checking_then_has_mock_patterns(self):
        """tests/CLAUDE.md 包含 Mock VISA 模式说明。"""
        content = _read_file(TESTS_CLAUDE_MD)
        assert "Mock" in content or "mock" in content, (
            "tests/CLAUDE.md 缺少 Mock 模式说明"
        )

    def test_given_tests_claude_md_when_checking_then_has_run_commands(self):
        """tests/CLAUDE.md 包含 pytest 运行命令。"""
        content = _read_file(TESTS_CLAUDE_MD)
        assert "pytest" in content, (
            "tests/CLAUDE.md 缺少 pytest 运行命令"
        )

    def test_given_tests_claude_md_when_checking_then_has_all_required_sections(self):
        """tests/CLAUDE.md 包含所有必要章节（深度检查，支持中英双语）。"""
        content = _read_file(TESTS_CLAUDE_MD)
        content_lower = content.lower()
        missing = []
        for keyword_group in TESTS_SECTIONS:
            if not any(kw.lower() in content_lower for kw in keyword_group):
                missing.append(" | ".join(keyword_group))
        assert not missing, (
            f"tests/CLAUDE.md 缺少章节关键字（每组至少需要一个）: {missing}"
        )


# ── draw/CLAUDE.md 测试 ─────────────────────────────────────────────────

class TestDrawClaudeMd:
    """绘图子目录 CLAUDE.md (Auto_Sweep/draw/CLAUDE.md)"""

    def test_given_draw_dir_when_checking_then_draw_claude_md_exists(self):
        """draw/CLAUDE.md 文件存在。"""
        assert DRAW_CLAUDE_MD.exists(), (
            f"draw/CLAUDE.md 不存在于 {DRAW_CLAUDE_MD}"
        )

    def test_given_draw_claude_md_when_checking_then_has_plot_scripts(self):
        """draw/CLAUDE.md 应引用绘图脚本。"""
        content = _read_file(DRAW_CLAUDE_MD)
        assert "plot_laser_powersweep" in content or "plot_VNA_powersweep" in content, (
            "draw/CLAUDE.md 应提及绘图脚本名称"
        )

    def test_given_draw_claude_md_when_checking_then_has_usage_instructions(self):
        """draw/CLAUDE.md 应包含使用说明或命令行示例。"""
        content = _read_file(DRAW_CLAUDE_MD)
        assert "python" in content.lower() or "用法" in content or "Usage" in content, (
            "draw/CLAUDE.md 缺少使用说明"
        )


# ── 交叉引用测试 ─────────────────────────────────────────────────────────

class TestCrossReferences:
    """验证 CLAUDE.md 文件之间的交叉引用有效。"""

    def test_given_all_files_when_checking_cross_refs_then_all_targets_exist(self):
        """所有交叉引用的目标文件都存在。"""
        # 收集所有文件中提到的 CLAUDE.md 路径
        pattern = re.compile(r"([\w./]+\.md)")
        for src_file in ALL_NEW_FILES:
            content = _read_file(src_file)
            if not content:
                continue
            # 去掉首行标题（避免自引用，如 "# ui/CLAUDE.md — ...")
            lines = content.splitlines()
            if lines and lines[0].startswith("# "):
                content = "\n".join(lines[1:])
            refs = pattern.findall(content)
            for ref in refs:
                # 排除外部引用（http/https）
                if ref.startswith("http"):
                    continue
                # 构建相对于仓库根的路径
                target = src_file.parent / ref
                if not target.exists():
                    # 不是每个 .md 引用都必须存在（可能是文档内锚点），
                    # 只检查 CLAUDE.md 引用
                    if "CLAUDE.md" in ref:
                        pytest.fail(
                            f"{src_file.name} 引用了不存在的 {ref} "
                            f"(解析为 {target})"
                        )

    def test_given_all_files_when_checking_then_no_major_content_duplication(self):
        """主要章节不应在多个 CLAUDE.md 中重复。"""
        # 检查 ui/CLAUDE.md 的 h2 标题不出现在主 CLAUDE.md 中
        main_content = _read_file(MAIN_CLAUDE_MD)
        ui_headings = _extract_h2_headings(_read_file(UI_CLAUDE_MD))
        for heading in ui_headings:
            if heading in _extract_h2_headings(main_content):
                pytest.fail(
                    f"ui/CLAUDE.md 章节 '{heading}' 也出现在主 CLAUDE.md 中 — 考虑移除一处"
                )

    def test_given_all_files_when_checking_then_tests_headings_not_in_main(self):
        """tests/CLAUDE.md 的 h2 标题不应出现在主 CLAUDE.md 中。"""
        main_content = _read_file(MAIN_CLAUDE_MD)
        tests_headings = _extract_h2_headings(_read_file(TESTS_CLAUDE_MD))
        for heading in tests_headings:
            if heading in _extract_h2_headings(main_content):
                pytest.fail(
                    f"tests/CLAUDE.md 章节 '{heading}' 也出现在主 CLAUDE.md 中 — 考虑移除一处"
                )


# ── 无信息丢失测试 ───────────────────────────────────────────────────────

class TestNoInfoLoss:
    """验证从原 CLAUDE.md 到新分层文件没有丢失关键信息。"""

    # 被合并的章节：原有两个 ## 标题，新文件中只保留一个
    MERGED_SECTIONS = {
        "Two PID Modules": "已合并到 PID Strategy 章节",
        "PID Strategy": "PID Strategy（合并了 Two PID Modules + PID Strategy）",
    }

    def test_given_original_claude_md_when_comparing_then_all_sections_preserved(self):
        """原 CLAUDE.md 的每个 ## 章节在新文件中至少出现一次。"""
        # 读取当前（重构前）的 CLAUDE.md 作为金本
        # 注：由于此测试在 Red 阶段运行，MAIN_CLAUDE_MD 仍是旧版本
        original = _read_file(MAIN_CLAUDE_MD)
        if not original:
            pytest.skip("主 CLAUDE.md 尚未创建（Phase 1 Red 阶段）")

        original_headings = _extract_h2_headings(original)
        if not original_headings:
            pytest.skip("无法提取原 CLAUDE.md 章节标题")

        # 收集所有目标文件的内容
        all_contents = {}
        for f in ALL_NEW_FILES:
            content = _read_file(f)
            if content:
                all_contents[f.name] = content

        missing = []
        for heading in original_headings:
            if heading in self.MERGED_SECTIONS:
                continue  # 已合并的章节不检查

            expected_file = SECTION_DESTINATION.get(heading)
            if expected_file is None:
                continue  # 明确标记为可移除的章节

            # 检查目标文件是否包含此标题
            target_content = _read_file(expected_file)
            if not target_content:
                missing.append(f"{heading} → {expected_file.name} (文件为空或不存在)")
                continue

            if heading not in _extract_h2_headings(target_content):
                # 标题可能被改写（例如用中文），检查关键字
                keyword = heading.split("(")[0].strip().lower()
                if keyword not in target_content.lower():
                    missing.append(f"{heading} → {expected_file.name}")

        if missing:
            pytest.fail(
                f"以下原 CLAUDE.md 章节在新文件中缺失:\n" +
                "\n".join(f"  - {m}" for m in missing) +
                "\n\n已合并的章节: " + ", ".join(self.MERGED_SECTIONS.keys())
            )

    def test_given_original_claude_md_when_comparing_then_config_reference_preserved(self):
        """config.py 速查表（Common Modifications）必须在主 CLAUDE.md 中保留。"""
        content = _read_file(MAIN_CLAUDE_MD)
        if not content:
            pytest.skip("主 CLAUDE.md 尚未创建（Phase 1 Red 阶段）")
        # Common Modifications 是最常被引用的章节，必须保留
        assert "Common Modifications" in content or "config.py" in content.lower(), (
            "主 CLAUDE.md 必须包含 Common Modifications / config.py 速查表"
        )

    def test_given_original_claude_md_when_comparing_then_critical_note_preserved(self):
        """关键警告（HiSLIP/PXI 地址）必须在主 CLAUDE.md 中保留。"""
        content = _read_file(MAIN_CLAUDE_MD)
        if not content:
            pytest.skip("主 CLAUDE.md 尚未创建（Phase 1 Red 阶段）")
        assert "HiSLIP" in content, (
            "主 CLAUDE.md 必须包含 PXI VNA HiSLIP 地址关键警告"
        )
