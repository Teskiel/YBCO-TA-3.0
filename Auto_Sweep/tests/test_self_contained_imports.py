# -*- coding: utf-8 -*-
"""自包含性测试 — 验证 dataprocess 已内联至 lib/，仓库不依赖 monorepo 外部目录。

遵循 BDD 命名规范: test_given_<precondition>_when_<action>_then_<expected_result>
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # AutoSweep_SMLab201/

# 把仓库根加入 sys.path，使 tests 可导入项目模块（幂等）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestDataprocessInlined:
    """dataprocess 模块已内联至 lib/，无 monorepo Data_process 外部依赖。"""

    def test_given_repo_when_checking_then_lib_dataprocess_exists(self):
        """lib/dataprocess.py 存在。"""
        assert (PROJECT_ROOT / "lib" / "dataprocess.py").is_file(), (
            "lib/dataprocess.py 不存在"
        )

    def test_given_lib_when_importing_then_find_true_resonances_available(self):
        """lib 路径下可导入 find_true_resonances。"""
        lib_dir = str(PROJECT_ROOT / "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        import dataprocess

        assert hasattr(dataprocess, "find_true_resonances"), (
            "dataprocess 缺少 find_true_resonances"
        )
        assert callable(dataprocess.find_true_resonances)

    def test_given_repo_when_importing_tracking_utils_then_succeeds(self):
        """draw._tracking_utils 可导入且暴露 find_true_resonances。"""
        import draw._tracking_utils as tu

        assert hasattr(tu, "find_true_resonances"), (
            "draw._tracking_utils 未暴露 find_true_resonances"
        )

    def test_given_tracking_utils_when_checking_source_then_no_monorepo_reference(self):
        """draw/_tracking_utils.py 源码不含 monorepo Data_process 引用（自包含守卫）。"""
        src = (PROJECT_ROOT / "draw" / "_tracking_utils.py").read_text(
            encoding="utf-8"
        )
        assert "Data_process" not in src, (
            "draw/_tracking_utils.py 仍引用 monorepo Data_process 外部路径"
        )
