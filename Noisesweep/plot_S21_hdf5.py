"""Read a KID S21 HDF5 file and plot all available S21-related data."""

from pathlib import Path
from typing import Optional

import h5py
import matplotlib.pyplot as plt
import numpy as np


# ============================================================================
# 只需要修改这里的HDF5文件地址，然后直接运行本脚本
# ============================================================================
FILE_PATH = r"C:\Users\smlab\Desktop\S21_measurement.h5"

# 是否将图片保存到HDF5文件所在目录；不需要保存时设为 False
SAVE_FIGURES = False

# 保存图片的分辨率
FIGURE_DPI = 200

# mean_voltage_V中的原始I/Q列编号。默认前两列分别是I和Q。
# 如果采集时使用了其他通道，请修改这两个编号。
RAW_I_COLUMN = 0
RAW_Q_COLUMN = 1


def _read_optional(group, name):
    return np.asarray(group[name]) if name in group else None


def read_s21_hdf5(file_path):
    """Return all available S21 arrays and metadata from one HDF5 file."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    with h5py.File(path, "r") as h5:
        if "frequency_hz" not in h5:
            raise ValueError("This HDF5 file does not contain frequency_hz.")
        data = {
            "path": path,
            "frequency_hz": np.asarray(h5["frequency_hz"]),
            "raw_voltage_V": _read_optional(h5, "raw_voltage_V"),
            "mean_voltage_V": _read_optional(h5, "mean_voltage_V"),
            "calibrated_iq_voltage_V": _read_optional(
                h5, "calibrated_iq_voltage_V"
            ),
            "calibrated_mean_iq_V": _read_optional(
                h5, "calibrated_mean_iq_V"
            ),
            "iq_calibration_parameters": _read_optional(
                h5, "iq_calibration_parameters"
            ),
            "s21_magnitude_db": _read_optional(h5, "s21_magnitude_db"),
            "s21_phase_deg": _read_optional(h5, "s21_phase_deg"),
            "attributes": {key: h5.attrs[key] for key in h5.attrs},
            "scraps_fit": None,
        }
        if "channels" in h5:
            data["channels"] = tuple(
                item.decode("utf-8") if isinstance(item, bytes) else str(item)
                for item in np.asarray(h5["channels"])
            )
        else:
            data["channels"] = ()

        if "scraps_fit" in h5:
            fit_group = h5["scraps_fit"]
            fit = {
                name: np.asarray(fit_group[name])
                for name in (
                    "resultI", "resultQ", "INorm", "QNorm",
                    "resultINorm", "resultQNorm", "values",
                )
                if name in fit_group
            }
            if "labels" in fit_group:
                fit["labels"] = tuple(
                    item.decode("utf-8") if isinstance(item, bytes) else str(item)
                    for item in np.asarray(fit_group["labels"])
                )
            fit["attributes"] = {
                key: fit_group.attrs[key] for key in fit_group.attrs
            }
            data["scraps_fit"] = fit
    return data


def plot_s21_hdf5(file_path, show=True, save_path: Optional[str] = None):
    """Plot IQ, magnitude, phase, calibration parameters and SCRAPS fit.

    Returns
    -------
    data : dict
        Arrays and metadata returned by :func:`read_s21_hdf5`.
    figures : list[matplotlib.figure.Figure]
        All generated figure objects.
    """
    data = read_s21_hdf5(file_path)
    frequency_hz = data["frequency_hz"]
    frequency_ghz = frequency_hz / 1e9
    figures = []

    # Figure 1: measured S21 before and after IQ calibration.
    figure, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    figure.suptitle("S21 measurement: {}".format(data["path"].name))
    mean_iq = data["calibrated_mean_iq_V"]
    raw_mean = data["mean_voltage_V"]
    if (raw_mean is not None and raw_mean.ndim == 2 and
            raw_mean.shape[1] > max(RAW_I_COLUMN, RAW_Q_COLUMN)):
        axes[0, 0].plot(
            raw_mean[:, RAW_I_COLUMN], raw_mean[:, RAW_Q_COLUMN], ".-",
            color="tab:gray", alpha=.75, label="IQ before calibration",
        )
    if mean_iq is not None:
        axes[0, 0].plot(
            mean_iq[:, 0], mean_iq[:, 1], ".-",
            color="tab:blue", label="IQ after calibration",
        )
        axes[0, 0].set_xlabel("I (V)")
        axes[0, 0].set_ylabel("Q (V)")
        axes[0, 0].set_title("IQ before and after calibration")
        axes[0, 0].axis("equal")
        axes[0, 0].legend()
    else:
        axes[0, 0].text(.5, .5, "No calibrated_mean_iq_V",
                        ha="center", va="center")

    magnitude = data["s21_magnitude_db"]
    phase = data["s21_phase_deg"]
    if magnitude is not None:
        axes[0, 1].plot(frequency_ghz, magnitude)
    axes[0, 1].set_xlabel("Frequency (GHz)")
    axes[0, 1].set_ylabel("S21 magnitude (dB)")
    axes[0, 1].set_title("Calibrated S21 magnitude")
    if phase is not None:
        axes[1, 0].plot(frequency_ghz, phase)
    axes[1, 0].set_xlabel("Frequency (GHz)")
    axes[1, 0].set_ylabel("S21 phase (deg)")
    axes[1, 0].set_title("Calibrated S21 phase")

    parameters = data["iq_calibration_parameters"]
    if parameters is not None and parameters.ndim == 2 and parameters.shape[1] >= 5:
        labels = ("I0", "Q0", "A_I", "A_Q")
        for index, label in enumerate(labels):
            axes[1, 1].plot(frequency_ghz, parameters[:, index], label=label)
        rotation_axis = axes[1, 1].twinx()
        rotation_axis.plot(
            frequency_ghz, np.degrees(parameters[:, 4]), "k--", label="q"
        )
        rotation_axis.set_ylabel("Rotation q (deg)")
        axes[1, 1].legend(loc="best")
    else:
        axes[1, 1].text(.5, .5, "No IQ calibration parameters",
                        ha="center", va="center")
    axes[1, 1].set_xlabel("Frequency (GHz)")
    axes[1, 1].set_ylabel("Calibration parameter (V)")
    axes[1, 1].set_title("Interpolated IQ calibration")
    for axis in axes.ravel():
        axis.grid(True, alpha=.3)
    figures.append(figure)

    # Figure 2: optional SCRAPS results saved by the GUI.
    fit = data["scraps_fit"]
    required = {
        "resultI", "resultQ", "INorm", "QNorm",
        "resultINorm", "resultQNorm",
    }
    if fit is not None and required.issubset(fit):
        figure_fit, fit_axes = plt.subplots(
            2, 2, figsize=(13, 9), constrained_layout=True
        )
        figure_fit.suptitle("SCRAPS cmplxIQ fit")
        epsilon = np.finfo(float).tiny
        fit_axes[0, 0].plot(fit["resultINorm"], fit["resultQNorm"], label="fit")
        if mean_iq is not None:
            fit_axes[0, 0].plot(mean_iq[:, 0], mean_iq[:, 1], ".", label="meas")
        fit_axes[0, 0].set_xlabel("I")
        fit_axes[0, 0].set_ylabel("Q")
        fit_axes[0, 0].set_title("IQ plane")
        fit_axes[0, 0].axis("equal")
        fit_axes[0, 0].legend()

        fit_axes[0, 1].plot(
            frequency_ghz,
            10*np.log10(np.maximum(fit["resultI"]**2+fit["resultQ"]**2, epsilon)),
            label="fit",
        )
        if mean_iq is not None:
            fit_axes[0, 1].plot(
                frequency_ghz,
                10*np.log10(np.maximum(mean_iq[:, 0]**2+mean_iq[:, 1]**2, epsilon)),
                label="meas",
            )
        fit_axes[0, 1].set_title("Raw magnitude")
        fit_axes[0, 1].set_ylabel("Magnitude (dB)")
        fit_axes[0, 1].legend()

        fit_axes[1, 0].plot(
            frequency_ghz,
            10*np.log10(np.maximum(
                fit["resultINorm"]**2+fit["resultQNorm"]**2, epsilon
            )), label="fit",
        )
        fit_axes[1, 0].plot(
            frequency_ghz,
            10*np.log10(np.maximum(fit["INorm"]**2+fit["QNorm"]**2, epsilon)),
            label="meas",
        )
        fit_axes[1, 0].set_title("Normalized magnitude")
        fit_axes[1, 0].set_ylabel("Magnitude (dB)")
        fit_axes[1, 0].legend()

        fit_axes[1, 1].plot(
            frequency_ghz, np.arctan2(fit["QNorm"], fit["INorm"]), label="meas"
        )
        fit_axes[1, 1].plot(
            frequency_ghz,
            np.arctan2(fit["resultQNorm"], fit["resultINorm"]), label="fit",
        )
        fit_axes[1, 1].set_title("Normalized phase")
        fit_axes[1, 1].set_ylabel("Phase (rad)")
        fit_axes[1, 1].legend()
        for axis in fit_axes.ravel():
            axis.set_xlabel("Frequency (GHz)" if axis is not fit_axes[0, 0] else "I")
            axis.grid(True, alpha=.3)
        figures.append(figure_fit)

        attributes = fit.get("attributes", {})
        print("SCRAPS fit: Qi={}, Qc={}".format(
            attributes.get("Qi", "N/A"), attributes.get("Qc", "N/A")
        ))
        if "report" in attributes:
            print(attributes["report"])

    if save_path:
        base = Path(save_path).expanduser().resolve()
        base.parent.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(figures, 1):
            target = base if len(figures) == 1 else base.with_name(
                "{}-{}{}".format(base.stem, index, base.suffix or ".png")
            )
            item.savefig(target, dpi=200)
    if show:
        plt.show()
    return data, figures


if __name__ == "__main__":
    selected_file = Path(FILE_PATH).expanduser().resolve()
    if not selected_file.is_file():
        raise FileNotFoundError(
            "没有找到HDF5文件，请修改脚本顶部的 FILE_PATH：\n{}".format(
                selected_file
            )
        )

    output_image = None
    if SAVE_FIGURES:
        output_image = selected_file.with_name(selected_file.stem + "-S21.png")

    loaded_data, generated_figures = plot_s21_hdf5(
        selected_file,
        show=True,
        save_path=output_image,
    )

    print("读取完成：{}".format(selected_file))
    print("频点数：{}".format(len(loaded_data["frequency_hz"])))
    print("包含SCRAPS拟合：{}".format(
        "是" if loaded_data["scraps_fit"] is not None else "否"
    ))
