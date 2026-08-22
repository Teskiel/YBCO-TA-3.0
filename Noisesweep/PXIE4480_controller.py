# -*- coding: utf-8 -*-
"""
Created on Wed Aug 12 14:54:33 2026

@author: smlab
"""

"""NI PXIe-4480 acquisition controller based on NI-DAQmx.

The default device name is PXI2Slot2, as reported by NI MAX.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Iterator, Optional, Sequence, Tuple, Union

import h5py
import nidaqmx
import numpy as np
from nidaqmx.constants import (
    AcquisitionType,
    Coupling,
    Edge,
    TerminalConfiguration,
)
from nidaqmx.stream_readers import AnalogMultiChannelReader


@dataclass
class AcquisitionResult:
    """Data and metadata returned by one acquisition."""

    data: np.ndarray
    time: np.ndarray
    channels: Tuple[str, ...]
    requested_sample_rate: float
    actual_sample_rate: float

    @property
    def sample_count(self) -> int:
        return self.data.shape[1]

    @property
    def duration(self) -> float:
        return self.sample_count / self.actual_sample_rate

    def save_hdf5(
        self,
        file_path: Union[str, Path],
        compression: Optional[str] = "gzip",
    ) -> Path:
        """Save this acquisition to an HDF5 file and return its path."""
        path = Path(file_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        with h5py.File(path, "w") as h5_file:
            h5_file.attrs["format"] = "PXIe-4480 acquisition"
            h5_file.attrs["created_at"] = datetime.now().isoformat(timespec="seconds")
            h5_file.attrs["requested_sample_rate_hz"] = self.requested_sample_rate
            h5_file.attrs["actual_sample_rate_hz"] = self.actual_sample_rate
            h5_file.attrs["sample_count_per_channel"] = self.sample_count
            h5_file.attrs["duration_seconds"] = self.duration
            h5_file.attrs["channel_count"] = len(self.channels)

            string_type = h5py.string_dtype(encoding="utf-8")
            h5_file.create_dataset(
                "channels", data=np.asarray(self.channels, dtype=object), dtype=string_type
            )
            h5_file.create_dataset(
                "time_s", data=self.time, compression=compression, shuffle=True
            )
            data_set = h5_file.create_dataset(
                "voltage_V", data=self.data, compression=compression, shuffle=True
            )
            data_set.attrs["axis_0"] = "channel"
            data_set.attrs["axis_1"] = "sample"
            data_set.attrs["unit"] = "V"

        return path


class PXIe4480:
    """Configure and acquire voltage data from an NI PXIe-4480.

    Parameters are kept in the object and applied when ``acquire`` is called.
    Channel numbers may be provided as integers (0..5) or strings such as
    ``"ai0"`` and ``"PXI2Slot2/ai0"``.
    """

    VALID_CHANNELS = tuple(range(6))
    VALID_VOLTAGE_RANGES = (0.5, 1.0, 5.0, 10.0)

    def __init__(
        self,
        device_name: str = "PXI2Slot2",
        channels: Sequence[Union[int, str]] = (0,),
        sample_rate: float = 100_000.0,
        voltage_range: float = 10.0,
        coupling: str = "DC",
        terminal_config: str = "DIFFERENTIAL",
    ) -> None:
        self.device_name = device_name
        self._channels: Tuple[str, ...] = ()
        self._sample_rate = 0.0
        self._voltage_range = 0.0
        self._coupling = Coupling.DC
        self._terminal_config = TerminalConfiguration.DIFF
        self._continuous_stop_event = Event()

        self.set_channels(channels)
        self.set_sample_rate(sample_rate)
        self.set_voltage_range(voltage_range)
        self.set_coupling(coupling)
        self.set_terminal_config(terminal_config)

    @property
    def channels(self) -> Tuple[str, ...]:
        return self._channels

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @property
    def voltage_range(self) -> float:
        return self._voltage_range

    @property
    def coupling(self) -> str:
        return "AC" if self._coupling == Coupling.AC else "DC"

    def set_channels(self, channels: Sequence[Union[int, str]]) -> None:
        """Select any combination of ai0..ai5, without duplicates."""
        if isinstance(channels, (str, bytes)):
            channels = (channels,)
        if not channels:
            raise ValueError("At least one acquisition channel must be selected.")

        normalized = []
        for channel in channels:
            if isinstance(channel, int):
                index = channel
            else:
                text = str(channel).strip()
                short_name = text.rsplit("/", 1)[-1].lower()
                if not short_name.startswith("ai") or not short_name[2:].isdigit():
                    raise ValueError("Invalid channel: {!r}".format(channel))
                index = int(short_name[2:])

            if index not in self.VALID_CHANNELS:
                raise ValueError("PXIe-4480 channel must be ai0..ai5.")

            physical_name = "{}/ai{}".format(self.device_name, index)
            if physical_name not in normalized:
                normalized.append(physical_name)

        self._channels = tuple(normalized)

    def set_sample_rate(self, sample_rate: float) -> None:
        """Set requested samples/s per channel.

        PXIe-4480 supports frequency-domain sampling up to 1.25 MS/s and
        time-domain operation above that. DAQmx may coerce a requested rate
        to a hardware-supported value; the actual value is returned with the
        acquisition result.
        """
        sample_rate = float(sample_rate)
        if not 100.0 <= sample_rate <= 20_000_000.0:
            raise ValueError("sample_rate must be between 100 and 20,000,000 S/s.")
        self._sample_rate = sample_rate

    def set_voltage_range(self, voltage_range: float) -> None:
        """Set symmetric full-scale range: +/-0.5, 1, 5, or 10 V."""
        voltage_range = abs(float(voltage_range))
        if voltage_range not in self.VALID_VOLTAGE_RANGES:
            raise ValueError("voltage_range must be one of 0.5, 1, 5, or 10 V.")
        self._voltage_range = voltage_range

    def set_coupling(self, coupling: str) -> None:
        choices = {"AC": Coupling.AC, "DC": Coupling.DC}
        key = str(coupling).upper()
        if key not in choices:
            raise ValueError("coupling must be 'AC' or 'DC'.")
        self._coupling = choices[key]

    def set_terminal_config(self, terminal_config: str) -> None:
        choices = {
            "DIFFERENTIAL": TerminalConfiguration.DIFF,
            "DIFF": TerminalConfiguration.DIFF,
        }
        key = str(terminal_config).replace("_", "").upper()
        if key not in choices:
            raise ValueError(
                "This nidaqmx version supports terminal_config='DIFFERENTIAL' "
                "(or 'DIFF') in this class."
            )
        self._terminal_config = choices[key]

    def verify_device(self) -> None:
        """Raise an error if NI-DAQmx cannot find this device."""
        names = [device.name for device in nidaqmx.system.System.local().devices]
        if self.device_name not in names:
            raise RuntimeError(
                "Device {!r} was not found. Available devices: {}".format(
                    self.device_name, names
                )
            )

    def read_device_info(self) -> dict:
        """Return hardware information available from NI-DAQmx."""
        self.verify_device()
        device = nidaqmx.system.System.local().devices[self.device_name]
        ai_channels = tuple(channel.name for channel in device.ai_physical_chans)
        return {
            "name": device.name,
            "product_type": str(device.product_type),
            "serial_number": int(device.dev_serial_num),
            "ai_channels": ai_channels,
        }

    def acquire(
        self,
        duration: Optional[float] = None,
        sample_count: Optional[int] = None,
        trigger_mode: str = "IMMEDIATE",
        trigger_source: Optional[str] = None,
        trigger_edge: str = "RISING",
        timeout: Optional[float] = None,
    ) -> AcquisitionResult:
        """Perform a finite, simultaneous acquisition.

        Specify exactly one of ``duration`` or ``sample_count``.

        trigger_mode:
            ``"IMMEDIATE"`` starts by software.
            ``"DIGITAL"`` waits for an edge on ``trigger_source``. If the
            source is omitted, ``/PXI2Slot2/PFI0`` is used.
        """
        samples = self._resolve_sample_count(duration, sample_count)
        mode = str(trigger_mode).upper()
        if mode not in ("IMMEDIATE", "DIGITAL"):
            raise ValueError("trigger_mode must be 'IMMEDIATE' or 'DIGITAL'.")

        edges = {"RISING": Edge.RISING, "FALLING": Edge.FALLING}
        edge_key = str(trigger_edge).upper()
        if edge_key not in edges:
            raise ValueError("trigger_edge must be 'RISING' or 'FALLING'.")

        with nidaqmx.Task() as task:
            ai_channels = task.ai_channels.add_ai_voltage_chan(
                physical_channel=",".join(self._channels),
                terminal_config=self._terminal_config,
                min_val=-self._voltage_range,
                max_val=self._voltage_range,
            )
            for channel in ai_channels:
                channel.ai_coupling = self._coupling

            task.timing.cfg_samp_clk_timing(
                rate=self._sample_rate,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=samples,
            )

            if mode == "DIGITAL":
                source = trigger_source or "/{}/PFI0".format(self.device_name)
                task.triggers.start_trigger.cfg_dig_edge_start_trig(
                    trigger_source=source,
                    trigger_edge=edges[edge_key],
                )

            # Commit first, so the coerced hardware rate can be read reliably.
            task.control(nidaqmx.constants.TaskMode.TASK_COMMIT)
            actual_rate = float(task.timing.samp_clk_rate)
            data = np.empty((len(self._channels), samples), dtype=np.float64)
            reader = AnalogMultiChannelReader(task.in_stream)

            if timeout is None:
                acquisition_time = samples / actual_rate
                timeout = acquisition_time + (30.0 if mode == "DIGITAL" else 10.0)

            task.start()
            reader.read_many_sample(
                data=data,
                number_of_samples_per_channel=samples,
                timeout=float(timeout),
            )

        time_axis = np.arange(samples, dtype=np.float64) / actual_rate
        return AcquisitionResult(
            data=data,
            time=time_axis,
            channels=self._channels,
            requested_sample_rate=self._sample_rate,
            actual_sample_rate=actual_rate,
        )

    def acquire_continuous(
        self,
        samples_per_read: int = 10_000,
        buffer_seconds: float = 10.0,
        trigger_mode: str = "IMMEDIATE",
        trigger_source: Optional[str] = None,
        trigger_edge: str = "RISING",
        read_timeout: float = 10.0,
    ) -> Iterator[AcquisitionResult]:
        """Continuously acquire and yield one data block at a time.

        Acquisition continues until ``stop_continuous()`` is called, the
        generator is closed, or the caller interrupts the loop (Ctrl+C).
        Data are yielded in arrays shaped ``(channel_count, samples_per_read)``.
        The time axis is continuous across successive blocks.

        For a digital trigger, the trigger is used once to start the entire
        continuous acquisition; it is not reapplied for every returned block.
        """
        samples_per_read = int(samples_per_read)
        if samples_per_read <= 0:
            raise ValueError("samples_per_read must be greater than zero.")
        if float(read_timeout) <= 0:
            raise ValueError("read_timeout must be greater than zero.")
        if float(buffer_seconds) <= 0:
            raise ValueError("buffer_seconds must be greater than zero.")

        mode = str(trigger_mode).upper()
        if mode not in ("IMMEDIATE", "DIGITAL"):
            raise ValueError("trigger_mode must be 'IMMEDIATE' or 'DIGITAL'.")

        edges = {"RISING": Edge.RISING, "FALLING": Edge.FALLING}
        edge_key = str(trigger_edge).upper()
        if edge_key not in edges:
            raise ValueError("trigger_edge must be 'RISING' or 'FALLING'.")

        self._continuous_stop_event.clear()
        total_samples = 0

        with nidaqmx.Task() as task:
            ai_channels = task.ai_channels.add_ai_voltage_chan(
                physical_channel=",".join(self._channels),
                terminal_config=self._terminal_config,
                min_val=-self._voltage_range,
                max_val=self._voltage_range,
            )
            for channel in ai_channels:
                channel.ai_coupling = self._coupling

            # Keep several seconds of PC-side headroom.  A samples_per_read*10
            # buffer is only 0.1 s when reading 10k blocks at 1 MS/s and is
            # too small when Python is also writing HDF5 and updating plots.
            buffer_samples = max(
                samples_per_read * 4,
                int(round(self._sample_rate * float(buffer_seconds))),
                samples_per_read + 1,
            )
            task.timing.cfg_samp_clk_timing(
                rate=self._sample_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=buffer_samples,
            )
            task.in_stream.input_buf_size = buffer_samples

            if mode == "DIGITAL":
                source = trigger_source or "/{}/PFI0".format(self.device_name)
                task.triggers.start_trigger.cfg_dig_edge_start_trig(
                    trigger_source=source,
                    trigger_edge=edges[edge_key],
                )

            task.control(nidaqmx.constants.TaskMode.TASK_COMMIT)
            actual_rate = float(task.timing.samp_clk_rate)
            reader = AnalogMultiChannelReader(task.in_stream)
            task.start()

            while not self._continuous_stop_event.is_set():
                block = np.empty(
                    (len(self._channels), samples_per_read), dtype=np.float64
                )
                reader.read_many_sample(
                    data=block,
                    number_of_samples_per_channel=samples_per_read,
                    timeout=float(read_timeout),
                )

                start_time = total_samples / actual_rate
                time_axis = (
                    np.arange(samples_per_read, dtype=np.float64) / actual_rate
                    + start_time
                )
                total_samples += samples_per_read

                yield AcquisitionResult(
                    data=block,
                    time=time_axis,
                    channels=self._channels,
                    requested_sample_rate=self._sample_rate,
                    actual_sample_rate=actual_rate,
                )

    def stop_continuous(self) -> None:
        """Request a running ``acquire_continuous`` loop to stop safely."""
        self._continuous_stop_event.set()

    def _resolve_sample_count(
        self,
        duration: Optional[float],
        sample_count: Optional[int],
    ) -> int:
        if (duration is None) == (sample_count is None):
            raise ValueError("Specify exactly one of duration or sample_count.")

        if duration is not None:
            duration = float(duration)
            if duration <= 0:
                raise ValueError("duration must be greater than zero.")
            samples = int(round(duration * self._sample_rate))
        else:
            samples = int(sample_count)  # type: ignore[arg-type]

        if samples <= 0:
            raise ValueError("sample_count must be greater than zero.")
        return samples


# if __name__ == "__main__":
#     # Example: acquire ai0, ai2 and ai5 for one second, starting immediately.
#     daq = PXIe4480(
#         device_name="PXI2Slot2",
#         channels=(0, 2, 5),
#         sample_rate=100_000,
#         voltage_range=10.0,
#         coupling="DC",
#     )

#     daq.verify_device()
#     result = daq.acquire(duration=1.0)

#     print("Channels:", result.channels)
#     print("Data shape:", result.data.shape)
#     print("Actual sample rate: {:.3f} S/s".format(result.actual_sample_rate))

    # External PFI0 rising-edge trigger example:
    # result = daq.acquire(
    #     sample_count=100_000,
    #     trigger_mode="DIGITAL",
    #     trigger_source="/PXI2Slot2/PFI0",
    #     trigger_edge="RISING",
    #     timeout=60.0,
    # )

    # Unlimited acquisition example (stop with Ctrl+C):
    # try:
    #     for block in daq.acquire_continuous(samples_per_read=10_000):
    #         print(block.time[-1], block.data.shape)
    #         # Process or save each block here; do not append forever in RAM.
    # except KeyboardInterrupt:
    #     daq.stop_continuous()
    #     print("Continuous acquisition stopped.")