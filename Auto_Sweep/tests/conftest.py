# -*- coding: utf-8 -*-
"""
Shared test fixtures for the YBCO Auto Sweep test suite.

Provides:
  - mock_pyvisa        — fully mocked VISA ResourceManager + Resource
  - synthetic temperature data generators (stable, oscillating, drifting, noisy)
  - preset temp directory helpers
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ======================================================================
# Mock pyvisa
# ======================================================================

class MockResource:
    """Simulates a pyvisa Resource (device)."""

    def __init__(self, identity="MOCK,INSTR"):
        self._idn = identity
        self._registers: dict[str, str] = {}
        self._written: list[str] = []
        self.timeout = 5000
        self.baud_rate = 9600
        self.data_bits = 8
        self.parity = None
        self.stop_bits = None
        self.read_termination = "\n"
        self.write_termination = "\n"

    def write(self, cmd: str):
        self._written.append(cmd)

    def query(self, cmd: str) -> str:
        self._written.append(cmd)
        cmd_upper = cmd.strip().upper()
        if cmd_upper == "*IDN?":
            return self._idn
        # Temperature read
        if cmd_upper.startswith("KRDG?"):
            return self._registers.get("KRDG", "50.0000")
        # Setpoint read
        if cmd_upper.startswith("SETP?"):
            parts = cmd_upper.split()
            loop = parts[1] if len(parts) > 1 else "1"
            return self._registers.get(f"SETP{loop}", "50.0000")
        # Heater read
        if cmd_upper.startswith("HTR?"):
            return self._registers.get("HTR", "0.00")
        # Range read
        if cmd_upper.startswith("RANGE?"):
            return self._registers.get("RANGE", "0")
        # PID read
        if cmd_upper.startswith("PID?"):
            return self._registers.get("PID", "100.0,5.0,0.0")
        # Laser power
        if cmd_upper.startswith(":SOURce:POWer?"):
            return self._registers.get("POWER", "5.000")
        # Laser wavelength
        if cmd_upper.startswith(":SOURce:WAV?"):
            return self._registers.get("WAV", "1550.000")
        # Laser output state
        if cmd_upper.startswith(":OUTPut:STATe?"):
            return self._registers.get("OUTP", "1")
        # VNA error
        if cmd_upper.startswith(":SYSTem:ERRor?"):
            return '0,"No error"'
        return "0"

    def close(self):
        pass

    @property
    def last_command(self) -> str:
        return self._written[-1] if self._written else ""

    @property
    def all_commands(self) -> list:
        return list(self._written)


class MockResourceManager:
    """Simulates pyvisa.ResourceManager."""

    def __init__(self, lib="visa32.dll"):
        self.lib = lib
        self._resources: dict[str, MockResource] = {}

    def open_resource(self, address: str) -> MockResource:
        if address in self._resources:
            return self._resources[address]
        r = MockResource()
        self._resources[address] = r
        return r

    def list_resources(self) -> list:
        return list(self._resources.keys())

    def close(self):
        pass


@pytest.fixture
def mock_pyvisa():
    """Patch pyvisa.ResourceManager to return MockResourceManager."""
    with patch("pyvisa.ResourceManager", autospec=True) as mock_rm_class:
        mock_rm = MockResourceManager()
        mock_rm_class.return_value = mock_rm
        yield mock_rm


@pytest.fixture
def mock_resource():
    """Return a standalone MockResource for direct testing."""
    return MockResource()


# ======================================================================
# Synthetic temperature data
# ======================================================================

import numpy as np


@pytest.fixture
def stable_temperatures():
    """30K target, ±0.02K noise — should pass all stability checks."""
    np.random.seed(42)
    return [30.0 + np.random.normal(0, 0.02) for _ in range(60)]


@pytest.fixture
def oscillating_temperatures():
    """30K target with 0.5K amplitude sinusoidal oscillation."""
    return [30.0 + 0.5 * np.sin(i * 0.3) for i in range(60)]


@pytest.fixture
def drifting_temperatures():
    """30K → 32K linear drift over 60 samples."""
    return [30.0 + 0.033 * i for i in range(60)]


@pytest.fixture
def noisy_temperatures():
    """30K target with large ±0.08K random noise."""
    np.random.seed(7)
    return [30.0 + np.random.normal(0, 0.08) for _ in range(60)]


@pytest.fixture
def perfect_stable_temperatures():
    """Exactly 30K, zero variance — ideal case."""
    return [30.0] * 60


# ======================================================================
# Temp directories
# ======================================================================

@pytest.fixture
def temp_presets_dir():
    """Create a temporary presets directory, clean up after test."""
    with tempfile.TemporaryDirectory() as tmp:
        old_cwd = os.getcwd()
        # We don't chdir; PresetManager takes a path arg
        yield tmp
