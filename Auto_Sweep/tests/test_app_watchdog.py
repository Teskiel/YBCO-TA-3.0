# -*- coding: utf-8 -*-
"""app.py --watchdog / --resume CLI 参数解析测试。"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAppCliArgs:
    """CLI 参数解析测试。"""

    def test_given_no_args_when_parsing_then_defaults(self):
        """无参数时返回正常 GUI 模式。"""
        import app as app_module
        with patch.object(sys, "argv", ["app.py"]):
            args = app_module.parse_args()
            assert args.watchdog is False
            assert args.resume is None
            assert args.child_pid is None
            assert args.resume_path is None

    def test_given_watchdog_args_when_parsing_then_all_set(self):
        """--watchdog 参数正确解析。"""
        import app as app_module
        with patch.object(sys, "argv", [
            "app.py", "--watchdog", "--child-pid", "12345",
            "--resume-path", "/tmp/experiment/20260613_031259",
        ]):
            args = app_module.parse_args()
            assert args.watchdog is True
            assert args.child_pid == 12345
            assert args.resume_path == "/tmp/experiment/20260613_031259"

    def test_given_resume_arg_when_parsing_then_correct(self):
        """--resume 参数正确解析。"""
        import app as app_module
        with patch.object(sys, "argv", [
            "app.py", "--resume", "/tmp/experiment/20260613_031259",
        ]):
            args = app_module.parse_args()
            assert args.resume == "/tmp/experiment/20260613_031259"
            assert args.watchdog is False

    def test_given_watchdog_missing_required_when_parsing_then_none(self):
        """--watchdog 缺少 --child-pid 或 --resume-path 时解析成功（main() 负责校验）。"""
        import app as app_module
        with patch.object(sys, "argv", ["app.py", "--watchdog"]):
            args = app_module.parse_args()
            assert args.watchdog is True
            assert args.child_pid is None
            assert args.resume_path is None
