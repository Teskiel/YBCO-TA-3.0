"""S21 fitting using the exact SCRAPS procedure supplied by the user."""

from dataclasses import dataclass
from pathlib import Path
import h5py
import numpy as np


@dataclass
class S21FitResult:
    res: object
    freq: np.ndarray
    I: np.ndarray
    Q: np.ndarray
    resultI: np.ndarray
    resultQ: np.ndarray
    INorm: np.ndarray
    QNorm: np.ndarray
    resultINorm: np.ndarray
    resultQNorm: np.ndarray
    Qi: object
    Qc: object
    lmfit_result: dict
    labels: tuple
    values: np.ndarray
    report: str


class ScrapsS21Fitter:
    def __init__(self, resonator_name="resname", temperature_k=0.1,
                 readout_power_dbm=-60.0):
        self.resonator_name = resonator_name
        self.temperature_k = temperature_k
        self.readout_power_dbm = readout_power_dbm
        self.res = None

    def fit(self, freq, I, Q):
        import scraps.resonator as scr
        from scraps.fitsS21 import cmplxIQ_params, cmplxIQ_fit

        freq = np.asarray(freq, dtype=float)
        I = np.asarray(I, dtype=float)
        Q = np.asarray(Q, dtype=float)

        res = scr.Resonator(
            self.resonator_name, self.temperature_k, self.readout_power_dbm,
            freq, I, Q,
        )
        res.load_params(cmplxIQ_params)
        res.do_lmfit(cmplxIQ_fit)
        self.res = res

        fit_I = np.asarray(res.resultI)
        fit_Q = np.asarray(res.resultQ)
        fit_I_norm = np.asarray(res.resultINorm)
        fit_Q_norm = np.asarray(res.resultQNorm)
        default_result = res.lmfit_result["default"]
        labels = tuple(default_result["labels"])
        values = np.asarray(default_result["values"])
        lmfit_result = default_result["result"]

        lines = [
            "SCRAPS cmplxIQ fit result",
            "resonator = {}".format(self.resonator_name),
            "temperature = {} K".format(self.temperature_k),
            "readout_power = {} dBm".format(self.readout_power_dbm),
            "Qi = {}".format(res.Qi),
            "Qc = {}".format(res.Qc),
        ]
        lines.extend("{} = {}".format(k, v) for k, v in zip(labels, values))
        lines.extend([
            "success = {}".format(lmfit_result.success),
            "message = {}".format(lmfit_result.message),
            "chisqr = {}".format(lmfit_result.chisqr),
            "redchi = {}".format(lmfit_result.redchi),
        ])
        return S21FitResult(
            res=res, freq=np.asarray(res.freq), I=np.asarray(res.I),
            Q=np.asarray(res.Q), resultI=fit_I, resultQ=fit_Q,
            INorm=np.asarray(res.INorm), QNorm=np.asarray(res.QNorm),
            resultINorm=fit_I_norm, resultQNorm=fit_Q_norm,
            Qi=res.Qi, Qc=res.Qc, lmfit_result=res.lmfit_result,
            labels=labels, values=values, report="\n".join(lines),
        )


class S21NoiseCalibration:
    """Convert an IQ noise timestream with a fitted SCRAPS S21 circle.

    This is the ``cal_pulse_outside`` calculation supplied by the user, with
    pulse-only arguments removed.  The input IQ must be in the same
    IQ-calibrated coordinate system as the data passed to SCRAPS.
    """

    def __init__(self, freq, i, q, i_norm, q_norm, x0, y0,
                 i_offset=0.0, q_offset=0.0, source_path="",
                 fit_labels=(), fit_values=()):
        self.freq = np.asarray(freq, dtype=float).ravel()
        self.i = np.asarray(i, dtype=float).ravel()
        self.q = np.asarray(q, dtype=float).ravel()
        self.i_norm = np.asarray(i_norm, dtype=float).ravel()
        self.q_norm = np.asarray(q_norm, dtype=float).ravel()
        if not (self.freq.size == self.i.size == self.q.size ==
                self.i_norm.size == self.q_norm.size):
            raise ValueError("S21 fit arrays have inconsistent lengths.")
        self.x0, self.y0 = float(x0), float(y0)
        self.i_offset, self.q_offset = float(i_offset), float(q_offset)
        self.source_path = str(source_path)
        self.fit_parameters = {
            str(label): float(value)
            for label, value in zip(fit_labels, fit_values)
        }

    @classmethod
    def load(cls, file_path):
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError("S21 HDF5 file not found: {}".format(path))
        with h5py.File(path, "r") as h:
            if "scraps_fit" not in h:
                raise ValueError("The selected S21 file does not contain scraps_fit.")
            group = h["scraps_fit"]
            freq = np.asarray(h["frequency_hz"])
            i = np.asarray(group["I"] if "I" in group else
                           h["calibrated_mean_iq_V"][:, 0])
            q = np.asarray(group["Q"] if "Q" in group else
                           h["calibrated_mean_iq_V"][:, 1])
            i_norm, q_norm = np.asarray(group["INorm"]), np.asarray(group["QNorm"])
            if "x0" not in group.attrs or "y0" not in group.attrs:
                raise ValueError(
                    "The selected S21 file does not contain SCRAPS res.x0/res.y0; "
                    "run the S21 fit again with the updated program."
                )
            x0, y0 = group.attrs["x0"], group.attrs["y0"]
            i_offset = group.attrs.get("Ioffset", 0.0)
            q_offset = group.attrs.get("Qoffset", 0.0)
            labels = [item.decode("utf-8") if isinstance(item, bytes) else str(item)
                      for item in np.asarray(group["labels"])]
            values = np.asarray(group["values"], dtype=float)
        return cls(freq, i, q, i_norm, q_norm, x0, y0,
                   i_offset, q_offset, path, labels, values)

    def _balanced_frequency(self):
        """均衡偏置频点：圆上相对 |S21| 极小点 ±45°（≈-3dB 半功率）处。

        该处幅度(径向)与相位(切向)噪声投影相当 → 两条 PSD 重叠，与 GUI 一键面板
        在深谐振上得到的 f0+df 效果一致。对 df 退化的浅/高 T 谐振也稳健。
        取 Q 较高的一侧（图上方向，与面板一致）；±45° 都落出采样弧时回退最大响应点。
        """
        i_min = int(np.argmin(np.hypot(self.i_norm, self.q_norm)))  # |S21|min ≈ 谐振底
        rel = (self.i_norm + 1j*self.q_norm) - complex(self.x0, self.y0)
        phi = np.angle(rel)
        phi_min = phi[i_min]
        cands = []
        for sgn in (1, -1):                     # ±45°：幅度/相位响应相当
            target = phi_min + sgn*np.pi/4
            dphi = np.angle(np.exp(1j*(phi - target)))
            idx = int(np.argmin(np.abs(dphi)))
            cands.append((idx, float(self.q_norm[idx])))
        best = min(abs(np.angle(np.exp(1j*(phi - (phi_min + np.pi/4))))).min(),
                   abs(np.angle(np.exp(1j*(phi - (phi_min - np.pi/4))))).min())
        if best > 1.2:                          # ±45° 都落出采样弧 → 回退最大响应点
            d_i = np.gradient(self.i_norm); d_q = np.gradient(self.q_norm)
            return float(self.freq[np.argmax(d_i*d_i+d_q*d_q)])
        idx = cands[0][0] if cands[0][1] > cands[1][1] else cands[1][0]
        return float(self.freq[idx])

    def measurement_frequency(self, mode, manual_frequency_hz=None):
        """Select manual, fitted f0+df, maximum-IQ-response, or balanced frequency.

        ``BALANCED`` picks the point on the fitted S21 circle at ±45° from the
        |S21| minimum direction (≈ the -3 dB half-power bias).  There the radial
        (amplitude) and tangential (phase) noise projections are comparable, so the
        two PSDs overlap — matching the GUI one-key panel.

        ``F0_PLUS_DF`` uses f0+df normally, but if ``df`` is degenerate (~0 —
        shallow/overcoupled high-T resonance where the narrow scan can't resolve it),
        f0+df collapses to the resonance bottom (direct-left on the circle, huge
        phase drift, non-overlapping PSDs).  In that case it falls back to the
        balanced point, so it reproduces what the GUI panel shows on a good fit.
        """
        mode = str(mode).upper()
        if mode == "MANUAL":
            if manual_frequency_hz is None:
                raise ValueError("Manual noise frequency is not specified.")
            frequency = float(manual_frequency_hz)
        elif mode == "F0_PLUS_DF":
            if "f0" not in self.fit_parameters or "df" not in self.fit_parameters:
                raise ValueError("SCRAPS fit does not contain both f0 and df.")
            frequency = self.fit_parameters["f0"] + self.fit_parameters["df"]
            idx_f = int(np.argmin(np.abs(self.freq-frequency)))
            i_min = int(np.argmin(np.hypot(self.i_norm, self.q_norm)))
            if abs(idx_f - i_min) <= 1:         # df 退化 → f0+df 落在 |S21| 极小 → 均衡偏置
                frequency = self._balanced_frequency()
        elif mode == "MAX_RESPONSE":
            d_i = np.gradient(self.i_norm)
            d_q = np.gradient(self.q_norm)
            frequency = float(self.freq[np.argmax(d_i*d_i+d_q*d_q)])
        elif mode == "BALANCED":
            frequency = self._balanced_frequency()
        else:
            raise ValueError("Unknown noise-frequency selection: {}".format(mode))
        index = int(np.argmin(np.abs(self.freq-frequency)))
        return float(frequency), index

    def transform(self, frequency_hz, noise_i, noise_q):
        index = int(np.argmin(np.abs(self.freq-float(frequency_hz))))
        offset = complex(self.i_offset, self.q_offset)
        normalized_reference = complex(self.i_norm[index], self.q_norm[index])
        if abs(normalized_reference) <= np.finfo(float).tiny:
            raise ZeroDivisionError("S21 normalized reference is zero.")
        cal_factor = (complex(self.i[index], self.q[index])-offset) / normalized_reference
        if abs(cal_factor) <= np.finfo(float).tiny:
            raise ZeroDivisionError("S21 noise calibration factor is zero.")
        center = complex(self.x0, self.y0)
        rcal = abs(normalized_reference-center)
        if rcal <= np.finfo(float).tiny:
            raise ZeroDivisionError("S21 calibration radius is zero.")
        noise = (np.asarray(noise_i, float)+1j*np.asarray(noise_q, float)-offset) / cal_factor
        amplitude = np.abs(noise-center) / rcal
        phase = np.arctan2(noise.imag-self.y0, noise.real-self.x0)
        return amplitude, phase, noise.real, noise.imag, index


def welch_psd(values, sample_rate_hz, segment_seconds=1.0, window="hann",
              unwrap=False):
    """Return a one-sided Welch PSD using a segment duration in seconds."""
    from scipy import signal as sps

    data = np.asarray(values, dtype=float).ravel()
    if unwrap:
        data = np.unwrap(data)
    if data.size < 8:
        return np.empty(0), np.empty(0), 0
    nperseg = int(round(float(segment_seconds)*float(sample_rate_hz)))
    nperseg = min(data.size, max(8, nperseg))
    frequency, psd = sps.welch(
        data, fs=float(sample_rate_hz), window=str(window), nperseg=nperseg,
    )
    return frequency, psd, nperseg
