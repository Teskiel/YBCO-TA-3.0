"""Keysight P5002A Streamline VNA controller (PyVISA + SCPI).

Run this file directly to connect to the confirmed HiSLIP endpoint and perform
a short identification test. VISA discovery is not required.

Requirements:
    pip install pyvisa numpy
    Keysight IO Libraries Suite
    Keysight PXIe/USB VNA application with HiSLIP enabled
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
import time
import re

import numpy as np
import pyvisa
from pyvisa.errors import VisaIOError


# P5002A VNA application HiSLIP address confirmed on the user's computer.
# It does not need to appear in ResourceManager.list_resources(); open_resource
# can connect to this complete address directly.
DEFAULT_P5002A_ADDRESS = (
    "TCPIP0::DESKTOP-QM3MBMD::"
    "hislip_PXI10_CHASSIS1_SLOT1_INDEX0::INSTR"
)


@dataclass
class SParameterData:
    frequency_hz: np.ndarray
    complex_data: np.ndarray
    parameter: str

    @property
    def magnitude_db(self) -> np.ndarray:
        return 20.0 * np.log10(np.maximum(np.abs(self.complex_data), 1e-300))

    @property
    def phase_deg(self) -> np.ndarray:
        return np.angle(self.complex_data, deg=True)


@dataclass
class PXIHardwareInfo:
    """Information that can be read from a low-level PXI VISA resource."""

    resource_name: str
    canonical_name: str = ""
    python_type: str = ""
    manufacturer_name: str = ""
    manufacturer_id: Optional[int] = None
    model_name: str = ""
    model_code: Optional[int] = None
    error: str = ""

    @property
    def is_p5002a(self) -> bool:
        text = f"{self.manufacturer_name} {self.model_name}".upper()
        return "P5002A" in text


class P5002A:
    """SCPI controller for a Keysight P5002A VNA."""

    VALID_S_PARAMETERS = {"S11", "S21", "S12", "S22"}

    def __init__(
        self,
        resource_name: str = DEFAULT_P5002A_ADDRESS,
        timeout_ms: int = 300_000,
        channel: int = 1,
        verbose: bool = True,
    ) -> None:
        self.resource_name = resource_name
        self.timeout_ms = int(timeout_ms)
        self.channel = int(channel)
        self.verbose = bool(verbose)
        self.command_history: list[str] = []
        self._linear_sweep_config: Optional[tuple[float, float, int]] = None
        self._cw_measurement_number: Optional[int] = None
        self._cw_output_port: int = 1
        self._resource_manager = None
        self._instrument = None

    @property
    def is_connected(self) -> bool:
        return self._instrument is not None

    @staticmethod
    def _safe_attribute(resource, name: str, default=None):
        try:
            return getattr(resource, name)
        except Exception:
            return default

    @classmethod
    def inspect_pxi_hardware(
        cls, resource_name: str, resource_manager=None
    ) -> PXIHardwareInfo:
        """Read identification attributes without sending SCPI commands."""
        owns_manager = resource_manager is None
        rm = resource_manager or pyvisa.ResourceManager()
        resource = None
        result = PXIHardwareInfo(resource_name=resource_name)
        try:
            info = rm.resource_info(resource_name)
            result.canonical_name = info.resource_name
            resource = rm.open_resource(resource_name, open_timeout=5000)
            result.python_type = type(resource).__name__
            result.manufacturer_name = str(
                cls._safe_attribute(resource, "manufacturer_name", "") or ""
            )
            result.manufacturer_id = cls._safe_attribute(
                resource, "manufacturer_id"
            )
            result.model_name = str(
                cls._safe_attribute(resource, "model_name", "") or ""
            )
            result.model_code = cls._safe_attribute(resource, "model_code")
        except Exception as error:
            result.error = str(error)
        finally:
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
            if owns_manager:
                rm.close()
        return result

    @classmethod
    def scan_resources(
        cls, verbose: bool = True
    ) -> tuple[tuple[str, ...], list[PXIHardwareInfo]]:
        """Scan VISA resources and inspect each low-level PXI instrument."""
        rm = pyvisa.ResourceManager()
        try:
            resources = tuple(rm.list_resources("?*"))
            pxi_details = [
                cls.inspect_pxi_hardware(resource, rm)
                for resource in resources
                if resource.upper().startswith("PXI")
                and resource.upper().endswith("::INSTR")
                and "BACKPLANE" not in resource.upper()
                and "MEMACC" not in resource.upper()
            ]
        finally:
            rm.close()

        if verbose:
            print("\nDiscovered VISA resources:")
            if resources:
                for number, resource in enumerate(resources, 1):
                    kind = "SCPI candidate" if "HISLIP" in resource.upper() else "hardware/other"
                    print(f"  [{number}] {resource}  ({kind})")
            else:
                print("  No VISA resources found.")

            if pxi_details:
                print("\nLow-level PXI hardware information:")
                for item in pxi_details:
                    print(f"  Address: {item.resource_name}")
                    print(f"    Canonical address: {item.canonical_name or 'unavailable'}")
                    print(f"    Python type: {item.python_type or 'unavailable'}")
                    print(f"    Manufacturer: {item.manufacturer_name or 'unavailable'}")
                    print(f"    Manufacturer ID: {item.manufacturer_id!r}")
                    print(f"    Model: {item.model_name or 'unavailable'}")
                    print(f"    Model code: {item.model_code!r}")
                    if item.is_p5002a:
                        print("    >>> P5002A hardware detected")
                    if item.error:
                        print(f"    Read error: {item.error}")
        return resources, pxi_details

    @staticmethod
    def find_hislip_resources(resources: Sequence[str]) -> list[str]:
        return [r for r in resources if "HISLIP" in r.upper()]

    @classmethod
    def choose_resource(
        cls,
        resources: Sequence[str],
        pxi_details: Optional[Sequence[PXIHardwareInfo]] = None,
    ) -> str:
        """Choose a HiSLIP resource, asking the user if several are present."""
        candidates = cls.find_hislip_resources(resources)
        if not candidates:
            detected = [item for item in (pxi_details or []) if item.is_p5002a]
            if detected:
                detail = "\nP5002A hardware detected at:\n  " + "\n  ".join(
                    item.resource_name for item in detected
                )
                status = (
                    "\nThe hardware is installed correctly, but its VNA SCPI "
                    "service is not currently visible."
                )
            else:
                pxi = [r for r in resources if r.upper().startswith("PXI")]
                detail = "\nDetected PXI resources:\n  " + "\n  ".join(pxi) if pxi else ""
                status = ""
            raise RuntimeError(
                "The P5002A SCPI/HiSLIP address was not found."
                f"{detail}{status}\n\n"
                "Start the Keysight PXIe/USB VNA application, load the P5002A, "
                "then enable HiSLIP under Instrument > Setup > System Setup > "
                "Remote Interface. Run this program again afterward. A PXI... "
                "hardware address cannot be used by this PyVISA SCPI class."
            )
        if len(candidates) == 1:
            return candidates[0]

        print("\nMultiple HiSLIP resources were found:")
        for number, resource in enumerate(candidates, 1):
            print(f"  [{number}] {resource}")
        while True:
            answer = input(f"Select the P5002A [1-{len(candidates)}]: ").strip()
            try:
                return candidates[int(answer) - 1]
            except (ValueError, IndexError):
                print("Invalid selection; try again.")

    def connect(self) -> str:
        """Connect directly to the configured HiSLIP address and verify model."""
        if self.is_connected:
            return self.identify()

        self._resource_manager = pyvisa.ResourceManager()
        try:
            if not self.resource_name:
                raise ValueError("A P5002A HiSLIP address is required.")

            if self.resource_name.upper().startswith("PXI"):
                raise ValueError(
                    f"{self.resource_name!r} is a low-level PXI hardware address. "
                    "Use the HiSLIP address shown by the VNA application, such as "
                    "TCPIP0::localhost::hislip0::INSTR."
                )

            print(f"\nConnecting to: {self.resource_name}")
            self._instrument = self._resource_manager.open_resource(
                self.resource_name, open_timeout=self.timeout_ms
            )
            self._instrument.timeout = self.timeout_ms
            self._instrument.write_termination = "\n"
            self._instrument.read_termination = "\n"
            self.write("*CLS")
            identity = self.query("*IDN?")
            if "P5002A" not in identity.upper():
                raise RuntimeError(
                    "The address responded, but the instrument is not a P5002A: "
                    f"{identity}"
                )
            return identity
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        instrument, manager = self._instrument, self._resource_manager
        self._instrument = None
        self._resource_manager = None
        if instrument is not None:
            try:
                instrument.close()
            except Exception:
                pass
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass

    def _require_connection(self) -> None:
        if self._instrument is None:
            raise RuntimeError("P5002A is not connected.")

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        self.command_history.append(line)
        if self.verbose:
            print(line)

    def _raw_write(self, command: str) -> None:
        self._require_connection()
        self._instrument.write(command)

    def _raw_query(self, command: str) -> str:
        self._require_connection()
        return self._instrument.query(command).strip()

    def _raw_query_ascii_values(self, command: str) -> np.ndarray:
        self._require_connection()
        return np.asarray(self._instrument.query_ascii_values(command), dtype=float)

    def _read_error_queue(self, maximum: int = 50) -> list[str]:
        """Read the SCPI error queue without recursively checking these queries."""
        errors: list[str] = []
        try:
            count = int(float(self._raw_query("SYST:ERR:COUN?")))
        except (ValueError, VisaIOError):
            # Compatibility fallback if the firmware does not provide ERR:COUN.
            count = maximum
        for _ in range(min(count, maximum)):
            reply = self._raw_query("SYST:ERR?")
            if reply.startswith("0") or reply.startswith("+0"):
                break
            errors.append(reply)
        return errors

    def _raise_for_scpi_errors(self, command: str) -> None:
        errors = self._read_error_queue()
        if errors:
            self._log(f"ERROR after {command}: {'; '.join(errors)}")
            raise RuntimeError(
                f"P5002A rejected or failed SCPI command {command!r}:\n"
                + "\n".join(errors)
            )

    def write(self, command: str, check_errors: bool = True) -> None:
        """Send a command and verify that it produced no SCPI errors."""
        self._raw_write(command)
        if check_errors and command.upper() != "*CLS":
            self._raise_for_scpi_errors(command)
        self._log(f"WRITE OK: {command}")

    def query(self, command: str, check_errors: bool = True) -> str:
        """Send a query, read its response, and verify the SCPI error queue."""
        response = self._raw_query(command)
        if check_errors and not command.upper().startswith("SYST:ERR"):
            self._raise_for_scpi_errors(command)
        preview = response if len(response) <= 160 else response[:157] + "..."
        self._log(f"QUERY OK: {command} -> {preview}")
        return response

    def query_ascii_values(self, command: str) -> np.ndarray:
        self._log(f"QUERY START: {command}")
        values = self._raw_query_ascii_values(command)
        self._raise_for_scpi_errors(command)
        self._log(f"QUERY OK: {command} -> {values.size} numeric values")
        return values

    def query_binary_values(self, command: str) -> np.ndarray:
        """Read an IEEE binary block containing little-endian float64 values."""
        self._require_connection()
        self._log(f"BINARY QUERY START: {command}")
        values = np.asarray(
            self._instrument.query_binary_values(
                command,
                datatype="d",
                is_big_endian=False,
                container=np.array,
            ),
            dtype=float,
        )
        self._raise_for_scpi_errors(command)
        self._log(f"BINARY QUERY OK: {command} -> {values.size} numeric values")
        return values

    def identify(self) -> str:
        return self.query("*IDN?")

    def clear_status(self) -> None:
        self.write("*CLS")

    def reset(self) -> None:
        self.write("*RST")
        self.wait_until_complete()

    def wait_until_complete(
        self,
        timeout_ms: Optional[int] = None,
        poll_interval_s: float = 0.1,
    ) -> None:
        """Poll IEEE-488 status registers until the preceding operation finishes.

        This intentionally uses ``*OPC`` plus status-byte polling instead of the
        blocking ``*OPC?`` query. The timeout is only a fail-safe.
        """
        self._require_connection()
        limit_ms = int(timeout_ms or self.timeout_ms)
        deadline = time.monotonic() + limit_ms / 1000.0

        # Enable only the OPC bit in the Standard Event Status register and route
        # its summary bit (ESB, bit 5) into the status byte.
        self._raw_write("*ESE 1")
        self._raise_for_scpi_errors("*ESE 1")
        self._log("WRITE OK: *ESE 1")
        self._raw_write("*SRE 32")
        self._raise_for_scpi_errors("*SRE 32")
        self._log("WRITE OK: *SRE 32")
        self._raw_write("*OPC")
        self._log("WRITE OK: *OPC (completion event armed)")

        while time.monotonic() < deadline:
            status_byte = int(self._instrument.read_stb())
            if status_byte & 0x20:  # ESB: enabled Standard Event bit is set
                event_status = int(float(self._raw_query("*ESR?")))
                self._log(
                    f"SWEEP STATUS: STB=0x{status_byte:02X}, ESR=0x{event_status:02X}"
                )
                if event_status & 0x01:  # Operation Complete bit
                    self._raise_for_scpi_errors("completed operation")
                    self._log("OPERATION COMPLETE: OPC bit confirmed")
                    return
            time.sleep(max(0.02, float(poll_interval_s)))

        raise TimeoutError(
            f"The operation-complete bit was not set within {limit_ms / 1000:.1f} s. "
            "This is a fail-safe timeout; inspect the VNA state and command log."
        )

    def check_errors(self, maximum: int = 20) -> list[str]:
        return self._read_error_queue(maximum)

    def configure_linear_sweep(
        self,
        start_frequency_hz: float,
        stop_frequency_hz: float,
        points: int = 1001,
        if_bandwidth_hz: Optional[float] = None,
        power_dbm: Optional[float] = None,
        sweep_time_s: Optional[float] = None,
    ) -> None:
        if start_frequency_hz >= stop_frequency_hz:
            raise ValueError("Start frequency must be below stop frequency.")
        if points < 2:
            raise ValueError("Sweep must contain at least two points.")
        ch = self.channel
        self.write(f"SENS{ch}:SWE:MODE HOLD")
        self.write(f"SENS{ch}:SWE:TYPE LIN")
        self.write(f"SENS{ch}:FREQ:STAR {float(start_frequency_hz)}")
        self.write(f"SENS{ch}:FREQ:STOP {float(stop_frequency_hz)}")
        self.write(f"SENS{ch}:SWE:POIN {int(points)}")
        if if_bandwidth_hz is not None:
            self.set_if_bandwidth(if_bandwidth_hz)
        if power_dbm is not None:
            self.set_power(power_dbm)
        if sweep_time_s is not None:
            self.set_sweep_time(sweep_time_s)
        # Store only after every SCPI command above has passed the error check.
        self._linear_sweep_config = (
            float(start_frequency_hz),
            float(stop_frequency_hz),
            int(points),
        )

    def configure_cw(
        self,
        frequency_hz: float,
        power_dbm: Optional[float] = None,
        port: int = 1,
        output_mode: Optional[str] = None,
    ) -> None:
        port = int(port)
        if port not in (1, 2):
            raise ValueError("CW output port must be 1 or 2.")
        ch = self.channel
        mode = output_mode or ("PORT1" if port == 1 else "PORT2")
        self.set_cw_output_mode(mode)
        self.write(f"SENS{ch}:SWE:MODE CONT")
        self.write(f"SENS{ch}:SWE:TYPE CW")
        self.write(f"SENS{ch}:FREQ:CW {float(frequency_hz)}")
        self.set_trigger_continuous(True)
        self._linear_sweep_config = None
        if power_dbm is not None:
            if mode in ("PORT1", "BOTH"):
                self.set_power(power_dbm, port=1)
            if mode in ("PORT2", "BOTH"):
                self.set_power(power_dbm, port=2)

    def set_cw_output_mode(self, output_mode: str) -> dict:
        """Force Port 1, Port 2, or both physical source ports ON."""
        mode = str(output_mode).replace(" ", "").upper()
        aliases = {
            "PORT1": "PORT1", "1": "PORT1",
            "PORT2": "PORT2", "2": "PORT2",
            "BOTH": "BOTH", "PORT1+PORT2": "BOTH",
        }
        if mode not in aliases:
            raise ValueError("output_mode must be PORT1, PORT2, or BOTH.")
        mode = aliases[mode]
        options = self.query("*OPT?").upper()
        dual_source = "402" in {item.strip() for item in options.split(",")}
        if mode == "BOTH" and not dual_source:
            raise RuntimeError(
                "This P5002A does not report second-source Option 402, so Port 1 "
                "and Port 2 cannot output CW simultaneously. Select Port 1 only "
                "or Port 2 only. Instrument options: {}".format(options)
            )
        requested = {
            1: "ON" if mode in ("PORT1", "BOTH") else "OFF",
            2: "ON" if mode in ("PORT2", "BOTH") else "OFF",
        }
        for port, state in requested.items():
            self.write(f"SOUR{self.channel}:POW{port}:MODE {state}")
        actual = {
            port: self.query(
                f"SOUR{self.channel}:POW{port}:MODE?"
            ).strip().upper()
            for port in (1, 2)
        }
        for port in (1, 2):
            if actual[port] != requested[port]:
                raise RuntimeError(
                    f"Port {port} source-state verification failed: "
                    f"requested {requested[port]}, instrument reports {actual[port]}."
                )
        self._cw_output_port = 0 if mode == "BOTH" else int(mode[-1])
        self._log(
            f"CW OUTPUT VERIFIED: {mode}; Port1={actual[1]}, Port2={actual[2]}"
        )
        return {"output_mode": mode, "port1_mode": actual[1], "port2_mode": actual[2]}

    def select_cw_output_port(self, port: int) -> int:
        """Select the actual RF stimulus port through an active S-parameter.

        On a two-port VNA, S11/S21 use Port 1 as the stimulus and S12/S22 use
        Port 2. Merely writing POW2 changes its level but does not select it as
        the active source. A dedicated numbered S11 or S22 measurement is
        therefore defined and selected here.
        """
        port = int(port)
        if port not in (1, 2):
            raise ValueError("CW output port must be 1 or 2.")
        parameter = "S11" if port == 1 else "S22"
        if self._cw_measurement_number is None:
            self._cw_measurement_number = self.allocate_measurement_numbers(1)[0]
            self.define_numbered_measurement(
                self._cw_measurement_number, parameter
            )
        else:
            self.set_numbered_measurement_parameter(
                self._cw_measurement_number, parameter
            )
        number = self._cw_measurement_number
        # Select by measurement/trace number. P5002A does not implement
        # CALC:MEAS<n>:SEL; the documented command is CALC:PAR:MNUM <n>.
        self.write(f"CALC{self.channel}:PAR:MNUM {number}")
        selected_number = int(float(self.query(
            f"CALC{self.channel}:PAR:MNUM?"
        )))
        if selected_number != number:
            raise RuntimeError(
                f"CW Port {port} selection failed: requested MEAS{number}, "
                f"instrument selected MEAS{selected_number}."
            )
        actual = self.query(
            f"CALC{self.channel}:MEAS{number}:PAR?"
        ).strip().strip("'\"").upper()
        if actual != parameter:
            raise RuntimeError(
                f"CW Port {port} selection failed: expected {parameter}, "
                f"instrument reports {actual}."
            )
        self._cw_output_port = port
        self._log(
            f"CW STIMULUS VERIFIED: Port {port} via selected MEAS{number} {actual}"
        )
        return number

    def set_power(self, power_dbm: float, port: int = 1) -> None:
        self.write(f"SOUR{self.channel}:POW{int(port)} {float(power_dbm)}")

    def set_output(self, enabled: bool) -> None:
        self.write(f"OUTP {'ON' if enabled else 'OFF'}")

    def set_trigger_continuous(self, enabled: bool) -> None:
        """Enable or stop continuous channel triggering."""
        self.write(
            f"INIT{self.channel}:CONT {'ON' if enabled else 'OFF'}"
        )

    def trigger_single(self) -> None:
        """Issue one immediate trigger while continuous triggering is off."""
        self.set_trigger_continuous(False)
        self.write(f"INIT{self.channel}:IMM")

    def set_if_bandwidth(self, bandwidth_hz: float) -> None:
        if bandwidth_hz <= 0:
            raise ValueError("IF bandwidth must be positive.")
        self.write(f"SENS{self.channel}:BAND {float(bandwidth_hz)}")

    def set_sweep_time(self, sweep_time_s: float) -> None:
        """Set manual time for one complete sweep."""
        sweep_time_s = float(sweep_time_s)
        if sweep_time_s < 100.0:
            raise ValueError("Sweep time must be at least 100 seconds.")
        ch = self.channel
        self.write(f"SENS{ch}:SWE:TIME:AUTO OFF")
        self.write(f"SENS{ch}:SWE:TIME {sweep_time_s}")

    def set_averaging(self, count: int = 1, enabled: bool = True) -> None:
        ch = self.channel
        if count < 1:
            raise ValueError("Average count must be at least one.")
        self.write(f"SENS{ch}:AVER:COUN {int(count)}")
        self.write(f"SENS{ch}:AVER {'ON' if enabled else 'OFF'}")
        if enabled:
            self.write(f"SENS{ch}:AVER:CLE")

    def read_status(self, port: int = 1) -> dict:
        """Read the current VNA source/sweep state without changing it."""
        port = int(port)
        if port not in (1, 2):
            raise ValueError("Output port must be 1 or 2.")
        ch = self.channel
        sweep_type = self.query(f"SENS{ch}:SWE:TYPE?").strip().upper()
        port1_mode = self.query(f"SOUR{ch}:POW1:MODE?").strip().upper()
        port2_mode = self.query(f"SOUR{ch}:POW2:MODE?").strip().upper()
        options = self.query("*OPT?").upper()
        dual_source = "402" in {item.strip() for item in options.split(",")}
        port1_power = float(self.query(f"SOUR{ch}:POW1?"))
        port2_power = float(self.query(f"SOUR{ch}:POW2?"))
        # Do not query CALC:PAR:MNUM? while loading the initial state.  A PNA
        # channel is allowed to have no selected CALC measurement (for example
        # immediately after preset or after all traces were deleted).  On this
        # firmware that query then raises SCPI 103, "CALC measurement selection
        # set to none", even though the HiSLIP connection is healthy.
        #
        # AUTO means that the source follows whichever measurement is selected
        # later.  If no port is explicitly forced ON, retain the caller's
        # requested display/readback port without changing instrument state.
        selected_measurement = None
        selected_parameter = ""
        port1_forced = port1_mode == "ON"
        port2_forced = port2_mode == "ON"
        if dual_source and port1_forced and port2_forced:
            output_mode = "BOTH"
        elif port2_forced and not port1_forced:
            output_mode = "PORT2"
        elif port1_forced and not port2_forced:
            output_mode = "PORT1"
        else:
            output_mode = "PORT2" if port == 2 else "PORT1"
        return {
            "sweep_type": sweep_type,
            "sweep_mode": self.query(f"SENS{ch}:SWE:MODE?").strip().upper(),
            "trigger_continuous": bool(
                int(float(self.query(f"INIT{ch}:CONT?")))
            ),
            "output_port": self._cw_output_port,
            "power_readback_port": port,
            "output_mode": output_mode,
            "port1_mode": port1_mode,
            "port2_mode": port2_mode,
            "instrument_options": options,
            "dual_source_supported": dual_source,
            "selected_measurement": selected_measurement,
            "selected_parameter": selected_parameter,
            "start_frequency_hz": float(self.query(f"SENS{ch}:FREQ:STAR?")),
            "stop_frequency_hz": float(self.query(f"SENS{ch}:FREQ:STOP?")),
            "cw_frequency_hz": float(self.query(f"SENS{ch}:FREQ:CW?")),
            "points": int(float(self.query(f"SENS{ch}:SWE:POIN?"))),
            "sweep_time_s": float(self.query(f"SENS{ch}:SWE:TIME?")),
            "sweep_time_auto": bool(
                int(float(self.query(f"SENS{ch}:SWE:TIME:AUTO?")))
            ),
            "power_dbm": port2_power if output_mode == "PORT2" else port1_power,
            "port1_power_dbm": port1_power,
            "port2_power_dbm": port2_power,
            "if_bandwidth_hz": float(self.query(f"SENS{ch}:BAND?")),
            "averaging_enabled": bool(
                int(float(self.query(f"SENS{ch}:AVER?")))
            ),
            "average_count": int(float(self.query(f"SENS{ch}:AVER:COUN?"))),
            "output_enabled": bool(int(float(self.query("OUTP?")))),
        }

    def create_measurement(self, parameter: str = "S21", measurement: str = "Meas1") -> None:
        parameter = parameter.upper()
        if parameter not in self.VALID_S_PARAMETERS:
            raise ValueError(f"Parameter must be one of {sorted(self.VALID_S_PARAMETERS)}")
        ch = self.channel
        self.write(f"CALC{ch}:PAR:DEF:EXT '{measurement}','{parameter}'")
        self.write(f"CALC{ch}:PAR:SEL '{measurement}'")

    def define_numbered_measurement(self, measurement_number: int, parameter: str) -> None:
        """Define an S-parameter at an explicit VNA measurement number."""
        parameter = parameter.upper()
        if parameter not in self.VALID_S_PARAMETERS:
            raise ValueError(f"Parameter must be one of {sorted(self.VALID_S_PARAMETERS)}")
        if measurement_number < 1:
            raise ValueError("Measurement number must be positive.")
        self.write(
            f"CALC{self.channel}:MEAS{int(measurement_number)}:DEF '{parameter}'"
        )
        actual = self.query(
            f"CALC{self.channel}:MEAS{int(measurement_number)}:PAR?"
        ).strip().strip("'\"").upper()
        if actual.replace("_", "") != parameter.replace("_", ""):
            raise RuntimeError(
                f"Measurement {measurement_number} verification failed: "
                f"requested {parameter}, instrument reports {actual}."
            )
        self._log(
            f"MEASUREMENT VERIFIED: MEAS{measurement_number} -> {actual}"
        )

    def set_numbered_measurement_parameter(
        self, measurement_number: int, parameter: str
    ) -> None:
        """Overwrite the parameter of an existing numbered measurement."""
        parameter = parameter.upper()
        if parameter not in self.VALID_S_PARAMETERS:
            raise ValueError(f"Parameter must be one of {sorted(self.VALID_S_PARAMETERS)}")
        self.write(
            f"CALC{self.channel}:MEAS{int(measurement_number)}:PAR '{parameter}'"
        )
        actual = self.query(
            f"CALC{self.channel}:MEAS{int(measurement_number)}:PAR?"
        ).strip().strip("'\"").upper()
        if actual.replace("_", "") != parameter.replace("_", ""):
            raise RuntimeError(
                f"Measurement {measurement_number} overwrite failed: "
                f"requested {parameter}, instrument reports {actual}."
            )
        self._log(
            f"MEASUREMENT OVERWRITTEN: MEAS{measurement_number} -> {actual}"
        )

    def get_measurement_numbers(self, channel: Optional[int] = None) -> set[int]:
        """Return measurement numbers already used on the requested channel."""
        ch = self.channel if channel is None else int(channel)
        reply = self.query(f"SYST:MEAS:CAT? {ch}").strip().strip("'\"")
        numbers = {int(item) for item in re.findall(r"\d+", reply)}
        self._log(
            f"MEASUREMENT CATALOG CH{ch}: "
            + (", ".join(map(str, sorted(numbers))) if numbers else "empty")
        )
        return numbers

    def allocate_measurement_numbers(self, count: int = 4) -> list[int]:
        """Choose free globally unique measurement numbers without overwriting traces."""
        if count < 1:
            raise ValueError("Measurement count must be positive.")
        # Measurement numbers are globally unique. Querying without a channel
        # protects measurements that belong to other channels as well.
        reply = self.query("SYST:MEAS:CAT?").strip().strip("'\"")
        used = {int(item) for item in re.findall(r"\d+", reply)}
        allocated: list[int] = []
        candidate = 1
        while len(allocated) < count:
            if candidate not in used:
                allocated.append(candidate)
            candidate += 1
        self._log(
            "ALLOCATED FREE MEASUREMENTS: " + ", ".join(map(str, allocated))
        )
        return allocated

    def prepare_two_port_measurements(self) -> dict[str, int]:
        """Reuse/overwrite channel-1 measurements and create only missing ones."""
        parameters = ("S11", "S21", "S12", "S22")
        channel_numbers = sorted(self.get_measurement_numbers(self.channel))
        selected = channel_numbers[:4]

        if len(selected) < 4:
            selected.extend(self.allocate_measurement_numbers(4 - len(selected)))

        mapping = dict(zip(parameters, selected))
        existing = set(channel_numbers)
        for parameter, number in mapping.items():
            if number in existing:
                self.set_numbered_measurement_parameter(number, parameter)
            else:
                self.define_numbered_measurement(number, parameter)

        self._log(
            "TWO-PORT MEASUREMENT MAP: "
            + ", ".join(f"{parameter}=MEAS{number}" for parameter, number in mapping.items())
        )
        return mapping

    def select_measurement(self, measurement: str = "Meas1") -> None:
        self.write(f"CALC{self.channel}:PAR:SEL '{measurement}'")

    def single_sweep(self, timeout_ms: Optional[int] = None) -> None:
        """Start one sweep and poll ``SENS:SWE:MODE?`` until it returns HOLD.

        The VNA changes the channel back to HOLD after a single sweep.  This
        method therefore checks the actual channel sweep mode instead of using
        a blocking ``*OPC?`` query or relying on HiSLIP status-byte events.
        """
        command = f"SENS{self.channel}:SWE:MODE SING"
        self._raw_write(command)
        self._raise_for_scpi_errors(command)
        self._log(f"WRITE OK: {command}")

        query_command = f"SENS{self.channel}:SWE:MODE?"
        limit_ms = int(timeout_ms or self.timeout_ms)
        deadline = time.monotonic() + limit_ms / 1000.0
        last_mode = ""
        poll_count = 0

        while time.monotonic() < deadline:
            mode = self._raw_query(query_command).strip().upper()
            self._raise_for_scpi_errors(query_command)
            poll_count += 1

            # Avoid flooding the log while still showing state transitions and
            # periodic evidence that status queries are being received.
            if mode != last_mode or poll_count == 1 or poll_count % 10 == 0:
                self._log(
                    f"SWEEP STATUS: {query_command} -> {mode} "
                    f"(poll {poll_count})"
                )
                last_mode = mode

            if mode in {"HOLD", "HOLDING"}:
                self._log(
                    f"SWEEP COMPLETE: channel {self.channel} returned HOLD "
                    f"after {poll_count} status queries"
                )
                return

            if mode not in {"SING", "SINGLE", "CONT", "CONTINUOUS", "GRO", "GROUPS"}:
                raise RuntimeError(
                    f"Unexpected response to {query_command!r}: {mode!r}"
                )

            time.sleep(0.1)

        raise TimeoutError(
            f"The single-sweep command was accepted, but {query_command} did "
            f"not return HOLD within {limit_ms / 1000.0:.1f} s. "
            f"Last reported mode: {last_mode or 'no response'}."
        )

    def set_continuous(self, enabled: bool = True) -> None:
        mode = "CONT" if enabled else "HOLD"
        self.write(f"SENS{self.channel}:SWE:MODE {mode}")

    def read_s_parameter(
        self,
        parameter: str = "S21",
        measurement: str = "Meas1",
        perform_sweep: bool = True,
        timeout_ms: Optional[int] = None,
    ) -> SParameterData:
        self.create_measurement(parameter, measurement)
        if perform_sweep:
            self.single_sweep(timeout_ms)
        # Re-select the exact measurement that was swept before reading SDATA.
        self.select_measurement(measurement)

        if self._linear_sweep_config is None:
            raise RuntimeError(
                "No verified linear-sweep configuration is available. "
                "Call configure_linear_sweep() before reading S-parameter data."
            )
        start_hz, stop_hz, point_count = self._linear_sweep_config
        frequency = np.linspace(start_hz, stop_hz, point_count)
        self._log(
            f"FREQUENCY AXIS FROM VERIFIED SETTINGS: {point_count} points, "
            f"{start_hz:g} Hz to {stop_hz:g} Hz"
        )

        # Use an IEEE binary block instead of a long ASCII response. SWAP makes
        # the returned REAL,64 values little-endian, matching Windows/Python.
        self.write("FORM:DATA REAL,64")
        self.write("FORM:BORD SWAP")
        data_command = f"CALC{self.channel}:MEAS:DATA:SDATA?"
        raw = self.query_binary_values(data_command)
        if raw.size % 2:
            raise RuntimeError(f"Expected real/imaginary pairs, received {raw.size} values.")
        complex_data = raw[0::2] + 1j * raw[1::2]
        if frequency.size != complex_data.size:
            raise RuntimeError(
                f"Frequency/data length mismatch: expected {frequency.size} points "
                f"but {data_command} returned {complex_data.size} complex points."
            )
        return SParameterData(frequency, complex_data, parameter.upper())

    def read_all_s_parameters(
        self,
        measurement_prefix: str = "AllS",
        timeout_ms: Optional[int] = None,
    ) -> dict[str, SParameterData]:
        """Acquire S11, S21, S12 and S22 in one channel sweep.

        All four measurements are created before the sweep.  A single sweep
        therefore updates every trace, after which each selected measurement is
        read from the same acquisition.
        """
        if self._linear_sweep_config is None:
            raise RuntimeError(
                "Call configure_linear_sweep() before acquiring S-parameters."
            )

        # Explicit measurement numbers are essential: CALC:MEAS:DATA without an
        # mnum can keep returning the default/active measurement even after a
        # legacy CALC:PAR:SEL command.  These numbered definitions and queries
        # follow Keysight's PXIe/USB VNA programming examples.
        measurement_numbers = self.prepare_two_port_measurements()

        self.single_sweep(timeout_ms)

        start_hz, stop_hz, point_count = self._linear_sweep_config
        frequency = np.linspace(start_hz, stop_hz, point_count)
        self._log(
            f"FREQUENCY AXIS FROM VERIFIED SETTINGS: {point_count} points, "
            f"{start_hz:g} Hz to {stop_hz:g} Hz"
        )

        self.write("FORM:DATA REAL,64")
        self.write("FORM:BORD SWAP")
        result: dict[str, SParameterData] = {}

        for parameter, number in measurement_numbers.items():
            data_command = (
                f"CALC{self.channel}:MEAS{number}:DATA:SDATA?"
            )
            raw = self.query_binary_values(data_command)
            if raw.size != point_count * 2:
                raise RuntimeError(
                    f"{parameter}: expected {point_count * 2} real/imaginary "
                    f"values, received {raw.size}."
                )
            complex_data = raw[0::2] + 1j * raw[1::2]
            result[parameter] = SParameterData(
                frequency.copy(), complex_data, parameter
            )
            self._log(f"{parameter} DATA READY: {point_count} complex points")

        return result

    def save_touchstone(self, file_path: str | Path) -> str:
        """Save a two-port Touchstone file on the VNA host computer."""
        path = str(Path(file_path).with_suffix(".s2p")).replace("/", "\\")
        escaped = path.replace("'", "''")
        self.write(f"CALC{self.channel}:MEAS:SNP:PORTS '1,2'")
        self.write(f"CALC{self.channel}:MEAS:SNP:SAVE '{escaped}'")
        self.wait_until_complete()
        return path

    def save_csv(self, data: SParameterData, file_path: str | Path) -> Path:
        path = Path(file_path).with_suffix(".csv")
        table = np.column_stack(
            (data.frequency_hz, data.complex_data.real, data.complex_data.imag,
             data.magnitude_db, data.phase_deg)
        )
        np.savetxt(
            path, table, delimiter=",",
            header="frequency_hz,real,imag,magnitude_db,phase_deg", comments=""
        )
        return path

    def save_all_csv(
        self,
        data: dict[str, SParameterData],
        file_path: str | Path,
    ) -> Path:
        """Save frequency plus real/imaginary values for all four S-parameters."""
        self._validate_two_port_data(data)
        path = Path(file_path).with_suffix(".csv")
        frequency = data["S11"].frequency_hz
        columns = [frequency]
        headers = ["frequency_hz"]
        for parameter in ("S11", "S21", "S12", "S22"):
            columns.extend([data[parameter].complex_data.real, data[parameter].complex_data.imag])
            headers.extend([f"{parameter}_real", f"{parameter}_imag"])
        np.savetxt(
            path,
            np.column_stack(columns),
            delimiter=",",
            header=",".join(headers),
            comments="",
        )
        return path

    @staticmethod
    def _validate_two_port_data(data: dict[str, SParameterData]) -> None:
        required = ("S11", "S21", "S12", "S22")
        missing = [parameter for parameter in required if parameter not in data]
        if missing:
            raise ValueError(f"Missing S-parameters: {', '.join(missing)}")
        reference = data["S11"].frequency_hz
        for parameter in required:
            item = data[parameter]
            if item.frequency_hz.size != reference.size or not np.allclose(
                item.frequency_hz, reference, rtol=0.0, atol=0.0
            ):
                raise ValueError(f"{parameter} frequency axis does not match S11.")
            if item.complex_data.size != reference.size:
                raise ValueError(f"{parameter} data length does not match frequency axis.")

    def save_s2p_data(
        self,
        data: dict[str, SParameterData],
        file_path: str | Path,
    ) -> Path:
        """Write the acquired two-port data as Touchstone 1.0, Hz/RI/50 ohm."""
        self._validate_two_port_data(data)
        path = Path(file_path).with_suffix(".s2p")
        frequency = data["S11"].frequency_hz
        # Touchstone two-port order is S11, S21, S12, S22.
        with path.open("w", encoding="ascii", newline="\n") as stream:
            stream.write("! Keysight P5002A data acquired by P5002A_controller.py\n")
            stream.write("# Hz S RI R 50\n")
            for index, freq in enumerate(frequency):
                values = [float(freq)]
                for parameter in ("S11", "S21", "S12", "S22"):
                    value = data[parameter].complex_data[index]
                    values.extend([float(value.real), float(value.imag)])
                stream.write(" ".join(f"{value:.12e}" for value in values) + "\n")
        return path

    def __enter__(self) -> "P5002A":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.disconnect()


def main() -> None:
    print("Keysight P5002A direct HiSLIP connection test")
    print("=" * 45)
    try:
        print(f"Target address: {DEFAULT_P5002A_ADDRESS}")
        print("The program will connect directly without VISA discovery.")
        with P5002A() as vna:
            print(f"\nConnected successfully: {vna.identify()}")
            print(f"SCPI address: {vna.resource_name}")
            errors = vna.check_errors()
            if errors:
                print("Instrument error queue:")
                for error in errors:
                    print(f"  {error}")
            else:
                print("Instrument error queue: no errors")
    except (VisaIOError, RuntimeError, ValueError) as error:
        print(f"\nConnection failed:\n{error}")
        print("\nTroubleshooting:")
        print("  1. Open the Keysight PXIe/USB VNA application.")
        print("  2. Confirm that it has loaded the P5002A hardware.")
        print("  3. Enable HiSLIP in Remote Interface settings.")
        print("  4. Confirm the computer name is DESKTOP-QM3MBMD.")
        print("  5. Confirm Keysight IO Libraries Suite is installed.")


if __name__ == "__main__":
    main()