# -*- coding: utf-8 -*-
"""
Created on Mon Jun  1 18:52:28 2026

@author: Jie Hu

Purple Mountain Observatory

Email: jiehu@pmo.ac.cn

"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import find_peaks
import skrf as rf


import numpy as np
import matplotlib.pyplot as plt
import skrf as rf
from scipy.signal import find_peaks


def load_s_param(filename, port1 = 1, port2 = 0):
    sparams = rf.Network(filename)
    s21 = sparams.s[:, port1, port2]
    freq = sparams.f
    return freq, s21


def _robust_local_snr(
    trace,
    peak_index,
    center_index,
    inner_window=5,
    outer_window=40,
    min_peak_support_points=2,
    min_peak_width=2,
    max_peak_width=None,
):
    """
    Check local SNR and peak shape around a candidate resonance.

    The old criterion only used:
        peak_height / noise_sigma

    That can accept either a one-point noisy spike or a slow broad bump.
    This version also requires enough points above half-height and a reasonable
    width, measured as the longest continuous group of points above half-height
    near the candidate peak.
    """
    trace = np.asarray(trace)
    n = len(trace)

    left_start = max(0, center_index - outer_window)
    left_end = max(0, center_index - inner_window)
    right_start = min(n, center_index + inner_window + 1)
    right_end = min(n, center_index + outer_window + 1)

    local_background_region = np.concatenate(
        (trace[left_start:left_end], trace[right_start:right_end])
    )
    local_background_region = local_background_region[
        np.isfinite(local_background_region)
    ]

    if len(local_background_region) < 3:
        return False, np.nan, np.nan, np.nan, np.nan, np.nan, 0

    background = np.nanmedian(local_background_region)
    noise_sigma = 1.4826 * np.nanmedian(
        np.abs(local_background_region - background)
    )

    if not np.isfinite(noise_sigma) or noise_sigma == 0:
        noise_sigma = np.nanstd(local_background_region)

    if not np.isfinite(noise_sigma) or noise_sigma == 0:
        return False, background, np.nan, np.nan, np.nan, np.nan, 0

    peak_height = trace[peak_index] - background
    snr = peak_height / noise_sigma

    if not np.isfinite(snr) or peak_height <= 0:
        return False, background, peak_height, noise_sigma, snr, np.nan, 0

    # Reject sudden one-point spikes: a real peak should have support around it.
    peak_left = max(0, peak_index - inner_window)
    peak_right = min(n, peak_index + inner_window + 1)
    peak_region = trace[peak_left:peak_right]
    half_height_level = background + 0.5 * peak_height
    above_half_height = peak_region >= half_height_level
    support_points = int(np.sum(above_half_height))
    
    
    if snr < 10:
    
        if support_points < min_peak_support_points:
            return (
                False,
                background,
                peak_height,
                noise_sigma,
                snr,
                np.nan,
                support_points,
            )

    # Use the longest continuous high region near the peak as the width.
    # This treats a broad resonance feature as one object instead of measuring
    # only the narrow half-prominence width of a single noisy local maximum.
    continuous_widths = []
    current_width = 0
    for is_high in above_half_height:
        if is_high:
            current_width += 1
        elif current_width > 0:
            continuous_widths.append(current_width)
            current_width = 0

    if current_width > 0:
        continuous_widths.append(current_width)

    peak_width = max(continuous_widths) if continuous_widths else 0
    
    if snr > 5:
        
        return True, background, peak_height, noise_sigma, snr, peak_width, support_points
    
    else:
        
        pass  # print(snr) commented

    if min_peak_width is not None and peak_width < min_peak_width:
        return (
            False,
            background,
            peak_height,
            noise_sigma,
            snr,
            peak_width,
            support_points,
        )

    if max_peak_width is not None and peak_width > max_peak_width:
        return (
            False,
            background,
            peak_height,
            noise_sigma,
            snr,
            peak_width,
            support_points,
        )

    return True, background, peak_height, noise_sigma, snr, peak_width, support_points


def find_true_resonances(
    freq,
    s21,
    transmission=None,
    min_prominence=2,
    phase_diff_prominence=None,
    distance=20,
    phase_window=10,
    phase_diff_snr_threshold=5,
    noise_inner_window=5,
    noise_outer_window=40,
    min_phase_diff_support_points=2,
    min_phase_diff_width=2,
    max_phase_diff_width=None,
    plot=True,
):
    """
    Find true resonances from amplitude minima and diff(phase) maxima.

    Parameters
    ----------
    freq : array-like
        Frequency array.
    s21 : array-like of complex
        Complex S21 data.
    transmission : array-like, optional
        Amplitude trace used for minima detection. If None, 20*log10(abs(s21))
        is used.
    min_prominence : float
        Required prominence for transmission minima.
    phase_diff_prominence : float or None
        Required prominence for diff(phase) maxima. If None, scipy chooses all
        local maxima and the SNR/shape gate decides which are accepted.
    distance : int or None
        Minimum index distance between detected extrema.
    phase_window : int
        Maximum index distance between amplitude minimum and diff(phase)
        maximum.
    phase_diff_snr_threshold : float
        Minimum diff(phase) peak SNR required for a true resonance.
    noise_inner_window : int
        Excluded half-width around resonance when estimating phase noise.
    noise_outer_window : int
        Outer half-width used for the local phase noise estimate.
    min_phase_diff_support_points : int
        Minimum number of local points above half peak height. This rejects
        isolated noisy spikes.
    min_phase_diff_width : float or None
        Minimum continuous number of local points above half peak height.
    max_phase_diff_width : float or None
        Maximum continuous number of local points above half peak height. If
        None, this is set to 2*phase_window to reject very broad slow humps.
    plot : bool
        If True, plot amplitude and phase with accepted resonances.

    Returns
    -------
    accepted : list of dict
        Accepted resonance information.
    fig, axes : matplotlib figure and axes, or None, None if plot=False.
    """
    freq = np.asarray(freq)
    s21 = np.asarray(s21)

    if transmission is None:
        transmission = 20 * np.log10(np.abs(s21))
    else:
        transmission = np.asarray(transmission)

    phase = np.unwrap(np.angle(s21))
    phase_diff = np.diff(phase)
    phase_diff_freq = 0.5 * (freq[:-1] + freq[1:])

    min_indices, min_props = find_peaks(
        -transmission,
        prominence=min_prominence,
        distance=distance,
    )

    phase_diff_peak_indices, phase_diff_props = find_peaks(
        phase_diff,
        prominence=phase_diff_prominence,
        distance=distance,
    )

    if max_phase_diff_width is None:
        max_phase_diff_width = 2 * phase_window

    accepted = []

    for min_pos, min_idx in enumerate(min_indices):
        nearby_phase_diff_peaks = phase_diff_peak_indices[
            np.abs(phase_diff_peak_indices - min_idx) <= phase_window
        ]

        if len(nearby_phase_diff_peaks) == 0:
            continue

        phase_diff_idx = nearby_phase_diff_peaks[
            np.argmax(phase_diff[nearby_phase_diff_peaks])
        ]

        (
            valid_phase_peak,
            phase_diff_background,
            phase_diff_peak_height,
            phase_diff_noise_sigma,
            phase_diff_snr,
            phase_diff_width,
            phase_diff_support_points,
        ) = _robust_local_snr(
            trace=phase_diff,
            peak_index=phase_diff_idx,
            center_index=min_idx,
            inner_window=noise_inner_window,
            outer_window=noise_outer_window,
            min_peak_support_points=min_phase_diff_support_points,
            min_peak_width=min_phase_diff_width,
            max_peak_width=max_phase_diff_width,
        )

        if (
            not valid_phase_peak
            or phase_diff_snr < phase_diff_snr_threshold
        ):
            continue

        accepted.append(
            {
                "frequency": freq[min_idx],
                "transmission": transmission[min_idx],
                "index": int(min_idx),
                "transmission_prominence": min_props["prominences"][min_pos],
                "phase_diff_peak_index": int(phase_diff_idx),
                "phase_diff_peak_frequency": phase_diff_freq[phase_diff_idx],
                "phase_diff_peak_value": phase_diff[phase_diff_idx],
                "phase_diff_background": phase_diff_background,
                "phase_diff_peak_height": phase_diff_peak_height,
                "phase_diff_noise_sigma": phase_diff_noise_sigma,
                "phase_diff_snr": phase_diff_snr,
                "phase_diff_width": phase_diff_width,
                "phase_diff_support_points": phase_diff_support_points,
            }
        )

    fig = None
    axes = None

    if plot:
        fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
        ax_amp, ax_phase = axes

        ax_amp.plot(freq, transmission, label="Transmission")
        ax_phase.plot(phase_diff_freq, phase_diff, label="diff(unwrapped phase)",marker = 'd')

        if accepted:
            resonance_freq = [item["frequency"] for item in accepted]
            resonance_transmission = [item["transmission"] for item in accepted]
            phase_diff_freq_accepted = [
                item["phase_diff_peak_frequency"] for item in accepted
            ]
            phase_diff_values = [item["phase_diff_peak_value"] for item in accepted]

            ax_amp.scatter(
                resonance_freq,
                resonance_transmission,
                color="red",
                zorder=3,
                label="Accepted transmission minima",
            )
            ax_phase.scatter(
                phase_diff_freq_accepted,
                phase_diff_values,
                color="red",
                zorder=3,
                label="Accepted diff(phase) maxima",marker = 's',
            )

            for item in accepted:
                label = (
                    f'{item["frequency"]:.6g}\n'
                    f'SNR={item["phase_diff_snr"]:.1f}, '
                    f'W={item["phase_diff_width"]:.1f}'
                )
                ax_amp.annotate(
                    label,
                    xy=(item["frequency"], item["transmission"]),
                    xytext=(8, 8),
                    textcoords="offset points",
                    fontsize=9,
                    color="red",
                    arrowprops=dict(arrowstyle="->", color="red", lw=0.8),
                )

        ax_amp.set_ylabel("Transmission (dB)")
        ax_phase.set_xlabel("Frequency")
        ax_phase.set_ylabel("diff(phase) (rad/sample)")

        for ax in axes:
            ax.grid(True, alpha=0.3)
            ax.legend()

        fig.tight_layout()

    return accepted, fig, axes

#############################################################   

#example


# filename = (
#     "D:/OneDrive/Work At PMO/ZGD/waveguide measurement/GJ Data/YBCO-KID/YBCO - KID - 2026-05-21 -  H1244/data with light/data/4K/4K-00mW-laser/3GHz-6GHz-45dBm-laser-0mW.s2p"
# )

# freq, s21 = load_s_param(filename)

# accepted, fig, axes = find_true_resonances(
#     freq=freq,
#     s21=s21,
#     min_prominence=3,
#     phase_diff_prominence=None,
#     distance=10,
#     phase_window=10,
#     phase_diff_snr_threshold=0.5,
#     noise_inner_window=5,
#     noise_outer_window=40,
#     min_phase_diff_support_points=4,
#     min_phase_diff_width=4,
#     max_phase_diff_width=None,
#     plot=True,
# )

#############################################################   


