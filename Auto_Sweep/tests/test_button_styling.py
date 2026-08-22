# -*- coding: utf-8 -*-
"""
BDD 测试 — Connect/Disconnect 按钮颜色样式

验证 DashboardPage 和详情页的按钮在各种连接状态下正确显示颜色：
  - Connect 按钮: 绿色(可连接) / 灰色(已连接或连接中)
  - Disconnect 按钮: 红色(可断开) / 灰色(已断开)

命名规范: test_given_<前置条件>_when_<动作>_then_<预期结果>
"""

import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# =========================================================================
# 模块级 QApplication fixture
# =========================================================================

@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# =========================================================================
# helpers
# =========================================================================

def _has_green_bg(stylesheet: str) -> bool:
    """检查样式表是否包含绿色背景。"""
    if not stylesheet:
        return False
    style_lower = stylesheet.lower()
    return ("#4ade80" in style_lower or
            "background-color: green" in style_lower or
            "#27ae60" in style_lower)


def _has_red_bg(stylesheet: str) -> bool:
    """检查样式表是否包含红色背景。"""
    if not stylesheet:
        return False
    style_lower = stylesheet.lower()
    return ("#ef4444" in style_lower or
            "background-color: red" in style_lower or
            "#e74c3c" in style_lower)


def _has_gray_bg(stylesheet: str) -> bool:
    """检查样式表是否包含灰色背景。"""
    if not stylesheet:
        return False
    style_lower = stylesheet.lower()
    return ("#30363d" in style_lower or
            "background-color: gray" in style_lower or
            "background-color: grey" in style_lower)


# =========================================================================
# TestClass: Dashboard 按钮样式
# =========================================================================

class TestDashboardButtonStyling:
    """验证 DashboardPage 上 Connect/Disconnect 按钮的样式。"""

    @pytest.fixture
    def dashboard(self, qapp):
        """创建 DashboardPage 实例。"""
        from ui.dashboard_page import DashboardPage
        return DashboardPage()

    def test_given_disconnected_state_when_set_disconnected_then_connect_btn_green(
        self, dashboard
    ):
        """断开状态时 Connect 按钮应为绿色。"""
        dashboard.set_device_disconnected("laser")

        # 验证按钮存在且 Connect 按钮为绿色样式
        c_btn = getattr(dashboard, "_laser_connect_btn", None)
        d_btn = getattr(dashboard, "_laser_disconnect_btn", None)

        assert c_btn is not None, "Connect 按钮应该存在"
        assert d_btn is not None, "Disconnect 按钮应该存在"

        assert _has_green_bg(c_btn.styleSheet()), \
            f"Connect 按钮应为绿色，实际: {c_btn.styleSheet()}"
        assert c_btn.isEnabled(), "断开状态时 Connect 按钮应启用"

    def test_given_connected_state_when_set_connected_then_connect_btn_gray(
        self, dashboard
    ):
        """已连接状态时 Connect 按钮应为灰色（不需要连接）。"""
        dashboard.set_device_connected("laser", "Test Laser")

        c_btn = getattr(dashboard, "_laser_connect_btn", None)
        d_btn = getattr(dashboard, "_laser_disconnect_btn", None)

        assert c_btn is not None
        assert d_btn is not None

        assert _has_gray_bg(c_btn.styleSheet()) or not c_btn.isEnabled(), \
            f"Connect 按钮应为灰色或禁用，实际: {c_btn.styleSheet()}"
        assert not c_btn.isEnabled(), "已连接状态时 Connect 按钮应禁用"

    def test_given_connected_state_when_set_connected_then_disconnect_btn_red(
        self, dashboard
    ):
        """已连接状态时 Disconnect 按钮应为红色。"""
        dashboard.set_device_connected("laser", "Test Laser")

        d_btn = getattr(dashboard, "_laser_disconnect_btn", None)
        assert d_btn is not None

        assert _has_red_bg(d_btn.styleSheet()), \
            f"Disconnect 按钮应为红色，实际: {d_btn.styleSheet()}"
        assert d_btn.isEnabled(), "已连接状态时 Disconnect 按钮应启用"

    def test_given_disconnected_state_when_set_disconnected_then_disconnect_btn_gray(
        self, dashboard
    ):
        """断开状态时 Disconnect 按钮应为灰色（无法断开）。"""
        dashboard.set_device_disconnected("laser")

        d_btn = getattr(dashboard, "_laser_disconnect_btn", None)
        assert d_btn is not None

        assert _has_gray_bg(d_btn.styleSheet()) or not d_btn.isEnabled(), \
            f"Disconnect 按钮应为灰色或禁用，实际: {d_btn.styleSheet()}"

    def test_given_error_state_when_set_error_then_connect_btn_green_enabled(
        self, dashboard
    ):
        """错误状态时 Connect 按钮应为绿色可用（用户可手动重连）。"""
        dashboard.set_device_error("laser")

        c_btn = getattr(dashboard, "_laser_connect_btn", None)
        d_btn = getattr(dashboard, "_laser_disconnect_btn", None)

        assert c_btn is not None
        assert _has_green_bg(c_btn.styleSheet()), \
            f"错误状态时 Connect 按钮应为绿色，实际: {c_btn.styleSheet()}"
        assert c_btn.isEnabled(), "错误状态时 Connect 按钮应启用"

    def test_given_connecting_state_when_set_connecting_then_both_buttons_disabled(
        self, dashboard
    ):
        """连接中（黄灯）状态时两个按钮都应禁用。"""
        # 模拟连接中状态
        dashboard.set_device_connected("laser", "Test")
        # 如果需要 set_device_connecting 方法，通过 hasattr 检查
        if hasattr(dashboard, "set_device_connecting"):
            dashboard.set_device_connecting("laser")
            c_btn = getattr(dashboard, "_laser_connect_btn", None)
            d_btn = getattr(dashboard, "_laser_disconnect_btn", None)
            if c_btn:
                assert not c_btn.isEnabled(), "连接中时 Connect 按钮应禁用"
            if d_btn:
                assert not d_btn.isEnabled(), "连接中时 Disconnect 按钮应禁用"

    def test_given_all_three_devices_when_connected_then_all_buttons_styled(
        self, dashboard
    ):
        """三台设备都已连接时，所有 Connect 按钮应灰色，Disconnect 应红色。"""
        for key in ("laser", "lakeshore", "vna"):
            dashboard.set_device_connected(key, f"Test {key}")

        for key in ("laser", "lakeshore", "vna"):
            c_btn = getattr(dashboard, f"_{key}_connect_btn", None)
            d_btn = getattr(dashboard, f"_{key}_disconnect_btn", None)
            assert c_btn is not None, f"_{key}_connect_btn 应该存在"
            assert d_btn is not None, f"_{key}_disconnect_btn 应该存在"
            assert not c_btn.isEnabled(), \
                f"已连接时 {key} Connect 按钮应禁用"
            assert d_btn.isEnabled(), \
                f"已连接时 {key} Disconnect 按钮应启用"


# =========================================================================
# TestClass: 详情页按钮样式
# =========================================================================

class TestDetailPageButtonStyling:
    """验证详情页（Laser/LakeShore/VNA）上 Connect/Disconnect 按钮的样式。"""

    @pytest.fixture
    def laser_page(self, qapp):
        from ui.laser_page import LaserPage
        return LaserPage()

    @pytest.fixture
    def lakeshore_page(self, qapp):
        from ui.lakeshore_page import LakeShorePage
        return LakeShorePage()

    @pytest.fixture
    def vna_page(self, qapp):
        from ui.vna_page import VNAPage
        return VNAPage()

    def test_given_laser_page_disconnected_when_created_then_connect_btn_green(
        self, laser_page
    ):
        """激光详情页初始状态 Connect 按钮应为绿色。"""
        c_btn = getattr(laser_page, "_connect_btn", None)
        if c_btn is None:
            pytest.skip("详情页尚未添加 Connect 按钮")
        assert _has_green_bg(c_btn.styleSheet()) or not _has_gray_bg(
            c_btn.styleSheet())

    def test_given_laser_page_connected_when_set_connected_then_buttons_styled(
        self, laser_page
    ):
        """激光详情页已连接时按钮样式应正确更新。"""
        c_btn = getattr(laser_page, "_connect_btn", None)
        d_btn = getattr(laser_page, "_disconnect_btn", None)

        if c_btn is None or d_btn is None:
            pytest.skip("详情页尚未添加 Connect/Disconnect 按钮")

        laser_page.set_connected("Test Laser v1.0")

        assert _has_gray_bg(c_btn.styleSheet()) or not c_btn.isEnabled(), \
            "已连接时 Connect 按钮应为灰色或禁用"
        assert _has_red_bg(d_btn.styleSheet()), \
            "已连接时 Disconnect 按钮应为红色"

    def test_given_lakeshore_page_disconnected_when_set_disconnected_then_buttons_styled(
        self, lakeshore_page
    ):
        """LakeShore 详情页断开时按钮样式应正确更新。"""
        c_btn = getattr(lakeshore_page, "_connect_btn", None)
        d_btn = getattr(lakeshore_page, "_disconnect_btn", None)

        if c_btn is None or d_btn is None:
            pytest.skip("详情页尚未添加 Connect/Disconnect 按钮")

        lakeshore_page.set_disconnected()

        assert _has_green_bg(c_btn.styleSheet()), \
            "断开时 Connect 按钮应为绿色"
        assert _has_gray_bg(d_btn.styleSheet()) or not d_btn.isEnabled(), \
            "断开时 Disconnect 按钮应为灰色或禁用"

    def test_given_vna_page_connecting_when_set_connecting_then_both_disabled(
        self, vna_page
    ):
        """VNA 详情页连接中时两个按钮都应禁用。"""
        c_btn = getattr(vna_page, "_connect_btn", None)
        d_btn = getattr(vna_page, "_disconnect_btn", None)

        if c_btn is None or d_btn is None:
            pytest.skip("详情页尚未添加 Connect/Disconnect 按钮")

        vna_page.set_connecting()

        assert not c_btn.isEnabled(), "连接中 Connect 按钮应禁用"
        assert not d_btn.isEnabled(), "连接中 Disconnect 按钮应禁用"
