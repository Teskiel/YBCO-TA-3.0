# -*- coding: utf-8 -*-
"""
BDD tests for VNAPage frequency controls.

Covers: spinbox range validation, Hz↔GHz unit conversion,
        frequency sync logic, boundary clamping.
"""

import sys
import pytest
from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication once per test module."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def vna_page(qapp):
    """Given a freshly-constructed VNAPage."""
    from ui.vna_page import VNAPage
    page = VNAPage()
    return page


# =========================================================================
# TestClass: Frequency Spinbox Ranges
# =========================================================================

class TestFrequencySpinboxRanges:
    """Given a VNAPage, the frequency spinboxes should be clamped 1–14 GHz."""

    def test_given_vna_page_when_start_spin_checked_then_range_is_1_to_14_ghz(self, vna_page):
        assert vna_page._start_spin.minimum() == pytest.approx(1.0)
        assert vna_page._start_spin.maximum() == pytest.approx(14.0)

    def test_given_vna_page_when_stop_spin_checked_then_range_is_1_to_14_ghz(self, vna_page):
        assert vna_page._stop_spin.minimum() == pytest.approx(1.0)
        assert vna_page._stop_spin.maximum() == pytest.approx(14.0)

    def test_given_vna_page_when_center_spin_checked_then_range_is_1_to_14_ghz(self, vna_page):
        assert vna_page._center_spin.minimum() == pytest.approx(1.0)
        assert vna_page._center_spin.maximum() == pytest.approx(14.0)

    def test_given_vna_page_when_span_spin_checked_then_range_is_0_to_14_ghz(self, vna_page):
        assert vna_page._span_spin.minimum() == pytest.approx(0.0)
        assert vna_page._span_spin.maximum() == pytest.approx(14.0)


# =========================================================================
# TestClass: Default Values & Unit Conversion
# =========================================================================

class TestDefaultValuesAndUnitConversion:
    """Given a VNAPage with defaults, get_all_settings returns correct Hz values."""

    def test_given_defaults_when_getting_all_settings_then_start_is_3e9_hz(self, vna_page):
        settings = vna_page.get_all_settings()
        assert settings["start_freq_hz"] == pytest.approx(3_000_000_000.0)
        assert settings["stop_freq_hz"] == pytest.approx(6_000_000_000.0)

    def test_given_defaults_when_setting_4_to_8_ghz_via_hz_then_spins_show_correct_ghz(
        self, vna_page
    ):
        """set_all_settings receives Hz values and spinboxes show GHz."""
        vna_page.set_all_settings({
            "start_freq_hz": 4_000_000_000.0,
            "stop_freq_hz": 8_000_000_000.0,
        })
        assert vna_page._start_spin.value() == pytest.approx(4.0)
        assert vna_page._stop_spin.value() == pytest.approx(8.0)


# =========================================================================
# TestClass: Frequency Sync Logic
# =========================================================================

class TestFrequencySyncLogic:
    """Given a VNAPage, changing start/stop updates center/span and vice versa."""

    def test_given_start_3_stop_6_when_changing_stop_to_9_then_center_updates_to_6_span_to_6(
        self, vna_page
    ):
        vna_page._stop_spin.setValue(9.0)
        assert vna_page._center_spin.value() == pytest.approx(6.0)
        assert vna_page._span_spin.value() == pytest.approx(6.0)

    def test_given_center_5_span_2_when_changing_center_to_7_then_start_6_stop_8(
        self, vna_page
    ):
        vna_page._center_spin.setValue(5.0)
        vna_page._span_spin.setValue(2.0)
        vna_page._center_spin.setValue(7.0)
        assert vna_page._start_spin.value() == pytest.approx(6.0)
        assert vna_page._stop_spin.value() == pytest.approx(8.0)


# =========================================================================
# TestClass: Boundary Clamping
# =========================================================================

class TestBoundaryClamping:
    """Given a VNAPage, values outside 1–14 GHz are clamped by QDoubleSpinBox."""

    def test_given_range_1_to_14_when_setting_start_below_1_then_clamped_to_1(
        self, vna_page
    ):
        vna_page._start_spin.setValue(0.5)
        assert vna_page._start_spin.value() >= 1.0

    def test_given_range_1_to_14_when_setting_stop_above_14_then_clamped_to_14(
        self, vna_page
    ):
        vna_page._stop_spin.setValue(20.0)
        assert vna_page._stop_spin.value() <= 14.0
