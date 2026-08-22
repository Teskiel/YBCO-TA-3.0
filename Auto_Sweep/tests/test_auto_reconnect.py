# -*- coding: utf-8 -*-
"""
BDD 测试 — 设备断联后自动重连机制

验证 MainWindow 在检测到 VISA 连接错误时能够：
  - 自动触发重连（黄灯状态）
  - 最多重试 3 次
  - 用户主动断开不触发自动重连
  - 多设备独立重连状态

命名规范: test_given_<前置条件>_when_<动作>_then_<预期结果>
"""

import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# TestClass: 连接错误检测
# =========================================================================

class TestConnectionErrorDetection:
    """验证 MainWindow 能正确识别 VISA 连接断开错误。"""

    def test_given_VI_ERROR_message_when_checking_then_is_connection_error(self):
        """包含 VI_ERROR 关键词的错误消息应被识别为连接错误。"""
        from ui.main_window import MainWindow
        assert MainWindow._is_connection_error(
            "VI_ERROR_TMO: Timeout expired") is True

    def test_given_timeout_message_when_checking_then_is_connection_error(self):
        """包含 timeout 关键词的错误消息应被识别为连接错误。"""
        from ui.main_window import MainWindow
        assert MainWindow._is_connection_error(
            "VISA timeout on read operation") is True

    def test_given_disconnect_message_when_checking_then_is_connection_error(self):
        """包含 disconnect/closed 关键词的错误消息应被识别为连接错误。"""
        from ui.main_window import MainWindow
        assert MainWindow._is_connection_error(
            "Device disconnected unexpectedly") is True
        assert MainWindow._is_connection_error(
            "Connection closed by remote") is True

    def test_given_lost_message_when_checking_then_is_connection_error(self):
        """包含 lost 关键词的错误消息应被识别为连接错误。"""
        from ui.main_window import MainWindow
        assert MainWindow._is_connection_error(
            "Socket connection lost") is True

    def test_given_normal_error_when_checking_then_not_connection_error(self):
        """普通错误消息（不含连接关键词）不应被识别为连接错误。"""
        from ui.main_window import MainWindow
        assert MainWindow._is_connection_error(
            "Invalid power value: must be >= 0") is False
        assert MainWindow._is_connection_error(
            "LakeShore not connected") is False
        # "not connected" 包含了 "connected"，需要仔细处理
        # "not connected" 实际上确实表示未连接状态


# =========================================================================
# TestClass: 自动重连触发
# =========================================================================

class TestAutoReconnectTrigger:
    """验证 _on_error() 在收到连接错误时触发自动重连。"""

    def test_given_connected_device_when_error_is_connection_error_then_reconnect_started(
        self, qapp
    ):
        """已连接的设备收到连接错误 → 触发自动重连。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._connected["laser"] = True
        mw._user_disconnect["laser"] = False
        mw._reconnect_addresses["laser"] = "TCPIP0::test::INSTR"

        # 模拟 _start_reconnect 被调用
        with patch.object(mw, "_start_reconnect") as mock_start:
            mw._on_error("laser", "VI_ERROR: connection lost")
            mock_start.assert_called_once_with("laser")

        mw.close()

    def test_given_connected_device_when_error_is_not_connection_then_no_reconnect(
        self, qapp
    ):
        """已连接的设备收到非连接错误 → 不触发自动重连。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._connected["laser"] = True
        mw._user_disconnect["laser"] = False

        with patch.object(mw, "_start_reconnect") as mock_start:
            mw._on_error("laser", "Invalid parameter: out of range")
            mock_start.assert_not_called()

        mw.close()

    def test_given_user_initiated_disconnect_when_error_occurs_then_no_reconnect(
        self, qapp
    ):
        """用户主动断开后收到连接错误 → 不触发自动重连。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._connected["lakeshore"] = True
        mw._user_disconnect["lakeshore"] = True  # 用户主动断开

        with patch.object(mw, "_start_reconnect") as mock_start:
            mw._on_error("lakeshore", "VI_ERROR: device not responding")
            mock_start.assert_not_called()

        mw.close()

    def test_given_device_not_connected_when_error_received_then_no_reconnect(
        self, qapp
    ):
        """未连接的设备收到错误 → 不尝试重连。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._connected["vna"] = False
        mw._user_disconnect["vna"] = False

        with patch.object(mw, "_start_reconnect") as mock_start:
            mw._on_error("vna", "VI_ERROR: not found")
            mock_start.assert_not_called()

        mw.close()


# =========================================================================
# TestClass: 重连次数与状态
# =========================================================================

class TestReconnectAttempts:
    """验证重连次数限制和状态更新。"""

    def test_given_first_reconnect_attempt_when_starting_then_attempt_count_is_1(
        self, qapp
    ):
        """第一次重连时尝试计数应为 1。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._reconnect_attempts["laser"] = 0
        mw._reconnect_addresses["laser"] = "TCPIP0::test::INSTR"

        with patch.object(mw, "_attempt_reconnect") as mock_attempt:
            mw._start_reconnect("laser")
            assert mw._reconnect_attempts["laser"] == 1

        mw.close()

    def test_given_3_failed_attempts_when_starting_4th_then_stops_reconnect(
        self, qapp
    ):
        """3 次重连失败后不再尝试第 4 次。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._reconnect_attempts["laser"] = 3
        mw._reconnect_addresses["laser"] = "TCPIP0::test::INSTR"
        mw._connected["laser"] = True

        with patch.object(mw, "_attempt_reconnect") as mock_attempt:
            mw._start_reconnect("laser")
            mock_attempt.assert_not_called()
        # 设备应保持断开状态
        assert mw._connected["laser"] is False

        mw.close()

    def test_given_reconnect_succeeds_when_connected_signal_then_attempts_reset(
        self, qapp
    ):
        """重连成功后尝试计数应重置为 0。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._reconnect_attempts["laser"] = 2
        mw._reconnect_addresses["laser"] = ""

        mw._on_device_connected("laser", "Test Laser v1.0")
        assert mw._reconnect_attempts["laser"] == 0

        mw.close()


# =========================================================================
# TestClass: 用户断开标记
# =========================================================================

class TestUserDisconnectFlag:
    """验证 _user_disconnect 标记的正确设置和清除。"""

    def test_given_user_clicks_disconnect_when_disconnecting_then_flag_set(
        self, qapp
    ):
        """用户点击 Disconnect 时应设置 user_disconnect 标记。"""
        from ui.main_window import MainWindow
        from unittest.mock import patch as mock_patch_fn

        mw = MainWindow()
        # 直接模拟 _on_dashboard_disconnect 的行为
        mw._on_dashboard_disconnect("laser")
        assert mw._user_disconnect["laser"] is True

        mw.close()

    def test_given_user_clicks_connect_when_connecting_then_flag_cleared(
        self, qapp
    ):
        """用户点击 Connect 时应清除 user_disconnect 标记。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._user_disconnect["laser"] = True

        mw._on_dashboard_connect("laser", "TCPIP0::test::INSTR")
        assert mw._user_disconnect["laser"] is False

        mw.close()


# =========================================================================
# TestClass: 多设备独立重连
# =========================================================================

class TestMultiDeviceReconnect:
    """验证多个设备同时断联时重连状态互相独立。"""

    def test_given_two_devices_error_when_reconnecting_then_independent_attempts(
        self, qapp
    ):
        """两个设备同时出错 → 各自独立重连计数。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._connected["laser"] = True
        mw._connected["lakeshore"] = True
        mw._reconnect_addresses["laser"] = "addr_laser"
        mw._reconnect_addresses["lakeshore"] = "addr_ls"

        with patch.object(mw, "_attempt_reconnect") as mock_attempt:
            mw._start_reconnect("laser")
            mw._start_reconnect("lakeshore")
            assert mock_attempt.call_count == 0  # QTimer 延迟
            assert mw._reconnect_attempts["laser"] == 1
            assert mw._reconnect_attempts["lakeshore"] == 1

        mw.close()

    def test_given_one_device_succeeds_other_fails_when_reconnecting_then_states_independent(
        self, qapp
    ):
        """一台重连成功，另一台继续重试 → 状态独立。"""
        from ui.main_window import MainWindow

        mw = MainWindow()
        mw._reconnect_attempts["laser"] = 2
        mw._reconnect_attempts["vna"] = 1

        # 激光重连成功
        mw._on_device_connected("laser", "Laser OK")
        assert mw._reconnect_attempts["laser"] == 0
        # VNA 仍保持之前的计数
        assert mw._reconnect_attempts["vna"] == 1

        mw.close()


# =========================================================================
# 模块级 qapp fixture
# =========================================================================

@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
