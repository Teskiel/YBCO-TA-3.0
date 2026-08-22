"""Ellipse-based IQ imbalance calibration translated from the MATLAB routine."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class IQCalibrationParameters:
    i_offset: float
    q_offset: float
    i_axis: float
    q_axis: float
    rotation_rad: float
    amplitude_imbalance_db: float
    phase_imbalance_deg: float
    fit_rms: float

    def to_dict(self) -> dict:
        return asdict(self)

    def save_json(self, path) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target


class IQEllipseCalibrator:
    """Fit an IQ ellipse and apply the same correction as the MATLAB code.

    The calibrated coordinates are::

        Ical = (I-I0)*cos(q) + (Q-Q0)*sin(q)
        Qcal = ((Q-Q0)*cos(q) - (I-I0)*sin(q)) * AI/AQ
    """

    def __init__(self):
        self.parameters = None

    @staticmethod
    def _data(i_data, q_data) -> Tuple[np.ndarray, np.ndarray]:
        i = np.asarray(i_data, dtype=float).ravel()
        q = np.asarray(q_data, dtype=float).ravel()
        if i.size != q.size or i.size < 20:
            raise ValueError("IQ ellipse fitting requires at least 20 paired samples.")
        finite = np.isfinite(i) & np.isfinite(q)
        i, q = i[finite], q[finite]
        if i.size < 20 or np.ptp(i) == 0 or np.ptp(q) == 0:
            raise ValueError("IQ samples are insufficient or degenerate for ellipse fitting.")
        return i, q

    def fit(self, i_data, q_data) -> IQCalibrationParameters:
        i, q = self._data(i_data, q_data)
        # Normalize before the constrained direct least-squares ellipse fit.
        mx, my = np.mean(i), np.mean(q)
        scale = max(np.std(i), np.std(q))
        x, y = (i - mx) / scale, (q - my) / scale
        d1 = np.column_stack((x*x, x*y, y*y))
        d2 = np.column_stack((x, y, np.ones_like(x)))
        s1, s2, s3 = d1.T @ d1, d1.T @ d2, d2.T @ d2
        transform = -np.linalg.pinv(s3) @ s2.T
        reduced = s1 + s2 @ transform
        constraint = np.array([[0., 0., 2.], [0., -1., 0.], [2., 0., 0.]])
        values, vectors = np.linalg.eig(np.linalg.solve(constraint, reduced))
        candidates = []
        for column in range(vectors.shape[1]):
            abc = np.real_if_close(vectors[:, column]).astype(float)
            if 4*abc[0]*abc[2] - abc[1]**2 > 0:
                candidates.append(abc)
        if not candidates:
            raise ValueError("The acquired IQ data do not form a valid ellipse.")
        abc = candidates[0]
        coeff = np.concatenate((abc, transform @ abc))
        a, b, c, d, e, f = coeff
        quadratic = np.array([[a, b/2], [b/2, c]])
        linear = np.array([d, e])
        center_n = -0.5 * np.linalg.solve(quadratic, linear)
        level = -(f + linear @ center_n + center_n @ quadratic @ center_n)
        eigenvalues, eigenvectors = np.linalg.eigh(quadratic / level)
        if np.any(eigenvalues <= 0):
            raise ValueError("Ellipse axes are not positive; acquire a complete IQ circle.")
        axes_n = 1.0 / np.sqrt(eigenvalues)
        order = np.argsort(axes_n)[::-1]
        axes_n, eigenvectors = axes_n[order], eigenvectors[:, order]
        major_vector = eigenvectors[:, 0]
        angle = float(np.arctan2(major_vector[1], major_vector[0]))
        # q and q+pi describe the same ellipse; keep a compact reproducible angle.
        angle = (angle + np.pi/2) % np.pi - np.pi/2
        center = center_n * scale + np.array([mx, my])
        axis_i, axis_q = float(axes_n[0]*scale), float(axes_n[1]*scale)
        ci, cq = self._apply(i, q, center[0], center[1], axis_i, axis_q, angle)
        radius = np.hypot(ci, cq)
        fit_rms = float(np.sqrt(np.mean((radius - np.mean(radius))**2)))
        self.parameters = IQCalibrationParameters(
            i_offset=float(center[0]), q_offset=float(center[1]),
            i_axis=axis_i, q_axis=axis_q, rotation_rad=angle,
            amplitude_imbalance_db=float(20*np.log10(axis_i/axis_q)),
            phase_imbalance_deg=float(np.degrees(angle)), fit_rms=fit_rms,
        )
        return self.parameters

    @staticmethod
    def _apply(i, q, i0, q0, ai, aq, angle):
        di, dq = i-i0, q-q0
        cosine, sine = np.cos(angle), np.sin(angle)
        i_cal = di*cosine + dq*sine
        q_cal = (dq*cosine - di*sine) * ai/aq
        return i_cal, q_cal

    def transform(self, i_data, q_data):
        if self.parameters is None:
            raise RuntimeError("Call fit() before transform().")
        i, q = self._data(i_data, q_data)
        p = self.parameters
        return self._apply(i, q, p.i_offset, p.q_offset,
                           p.i_axis, p.q_axis, p.rotation_rad)

    def fit_transform(self, i_data, q_data):
        parameters = self.fit(i_data, q_data)
        i_cal, q_cal = self.transform(i_data, q_data)
        return i_cal, q_cal, parameters

    def report(self, frequency_hz=None) -> str:
        if self.parameters is None:
            return "IQ calibration has not been fitted."
        p = self.parameters
        prefix = "frequency = {:.12g} Hz\n".format(frequency_hz) if frequency_hz else ""
        return prefix + (
            "I0 = {0.i_offset:.12e} V\nQ0 = {0.q_offset:.12e} V\n"
            "A_I = {0.i_axis:.12e} V\nA_Q = {0.q_axis:.12e} V\n"
            "rotation q = {0.rotation_rad:.12e} rad ({0.phase_imbalance_deg:.6f} deg)\n"
            "amplitude imbalance = {0.amplitude_imbalance_db:.6f} dB\n"
            "calibrated-radius RMS = {0.fit_rms:.12e} V"
        ).format(p)


class IQCalibrationTable:
    """Load frequency-dependent calibration parameters and interpolate them."""

    def __init__(self, frequencies_hz, parameter_rows, source_path=""):
        frequencies = np.asarray(frequencies_hz, dtype=float).ravel()
        rows = np.asarray(parameter_rows, dtype=float)
        if rows.shape != (frequencies.size, 5) or frequencies.size < 1:
            raise ValueError("Calibration table must contain frequency plus I0,Q0,A_I,A_Q,q.")
        order = np.argsort(frequencies)
        self.frequencies_hz = frequencies[order]
        self.rows = rows[order]
        if np.any(np.diff(self.frequencies_hz) <= 0):
            raise ValueError("Calibration frequencies must be unique and increasing.")
        self.source_path = str(source_path)

    @classmethod
    def load(cls, file_path):
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError("IQ calibration summary not found: {}".format(path))
        data = np.loadtxt(path, comments="#", ndmin=2)
        if data.shape[1] < 6:
            raise ValueError(
                "IQ summary needs at least 6 columns: frequency,I0,Q0,A_I,A_Q,rotation_q."
            )
        return cls(data[:, 0], data[:, 1:6], path)

    @staticmethod
    def _interpolate_with_extrapolation(x, xp, fp):
        if xp.size == 1:
            return float(fp[0])
        value = float(np.interp(x, xp, fp))
        if x < xp[0]:
            value = float(fp[0] + (x-xp[0])*(fp[1]-fp[0])/(xp[1]-xp[0]))
        elif x > xp[-1]:
            value = float(fp[-1] + (x-xp[-1])*(fp[-1]-fp[-2])/(xp[-1]-xp[-2]))
        return value

    def parameters_at(self, frequency_hz) -> IQCalibrationParameters:
        frequency = float(frequency_hz)
        rows = self.rows.copy()
        # Unwrap the ellipse angle before interpolation across +/-90 degree boundaries.
        rows[:, 4] = np.unwrap(rows[:, 4], period=np.pi)
        values = [self._interpolate_with_extrapolation(
            frequency, self.frequencies_hz, rows[:, index]
        ) for index in range(5)]
        i0, q0, ai, aq, angle = values
        if ai <= 0 or aq <= 0:
            raise ValueError("Interpolated IQ axes must be positive.")
        return IQCalibrationParameters(
            i_offset=i0, q_offset=q0, i_axis=ai, q_axis=aq,
            rotation_rad=angle,
            amplitude_imbalance_db=float(20*np.log10(ai/aq)),
            phase_imbalance_deg=float(np.degrees(angle)), fit_rms=float("nan"),
        )

    def transform(self, frequency_hz, i_data, q_data):
        p = self.parameters_at(frequency_hz)
        i = np.asarray(i_data, dtype=float)
        q = np.asarray(q_data, dtype=float)
        if i.shape != q.shape:
            raise ValueError("I and Q arrays must have the same shape.")
        i_cal, q_cal = IQEllipseCalibrator._apply(
            i, q, p.i_offset, p.q_offset, p.i_axis, p.q_axis, p.rotation_rad
        )
        return i_cal, q_cal, p
