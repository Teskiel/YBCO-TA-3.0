# _lib/resonance.py — resonance detection, SNR validation, interactive picking, and cross-temperature tracking
#
# The core detection algorithms (_robust_local_snr, find_true_resonances,
# interactive_pick_resonances) are migrated from Data_process/otherwise/dataprocess.py
# (Jie Hu, Purple Mountain Observatory, jiehu@pmo.ac.cn) and preserved exactly.
# match_peaks_across_temps is new for the refactored pipeline.

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import find_peaks


# ---------------------------------------------------------------------------
# 1. Local SNR + FWHM shape validation (exact copy from dataprocess.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 2. Dual-criterion resonance detection (exact copy from dataprocess.py)
# ---------------------------------------------------------------------------

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
    hint_freqs=None,
    n_resonances=0,
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
    hint_freqs : list of float or None
        Expected resonance frequencies (Hz). When n_resonances > 0, peaks are
        ranked by proximity to the nearest hint. High-temperature manual picks
        serve as hints to guide auto-detection in low-SNR regions.
    n_resonances : int
        Desired number of resonances to return. 0 = return all accepted.
        If > 0 and too many are found, the top-N (by hint proximity or SNR)
        are kept. If too few, detection is retried with relaxed thresholds
        (up to 2 retries). Final list is sorted by frequency.

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

    if max_phase_diff_width is None:
        max_phase_diff_width = 2 * phase_window

    # ---- internal single-pass detection ----
    def _do_detect(_min_prominence, _snr_threshold):
        _min_indices, _min_props = find_peaks(
            -transmission,
            prominence=_min_prominence,
            distance=distance,
        )
        _pd_indices, _pd_props = find_peaks(
            phase_diff,
            prominence=phase_diff_prominence,
            distance=distance,
        )
        _accepted = []
        for _min_pos, _min_idx in enumerate(_min_indices):
            _nearby = _pd_indices[
                np.abs(_pd_indices - _min_idx) <= phase_window
            ]
            if len(_nearby) == 0:
                continue
            _pd_idx = _nearby[np.argmax(phase_diff[_nearby])]
            (
                _valid, _bg, _height, _sigma, _snr,
                _width, _support,
            ) = _robust_local_snr(
                trace=phase_diff,
                peak_index=_pd_idx,
                center_index=_min_idx,
                inner_window=noise_inner_window,
                outer_window=noise_outer_window,
                min_peak_support_points=min_phase_diff_support_points,
                min_peak_width=min_phase_diff_width,
                max_peak_width=max_phase_diff_width,
            )
            if not _valid or _snr < _snr_threshold:
                continue
            _accepted.append({
                "frequency": freq[_min_idx],
                "transmission": transmission[_min_idx],
                "index": int(_min_idx),
                "transmission_prominence": _min_props["prominences"][_min_pos],
                "phase_diff_peak_index": int(_pd_idx),
                "phase_diff_peak_frequency": phase_diff_freq[_pd_idx],
                "phase_diff_peak_value": phase_diff[_pd_idx],
                "phase_diff_background": _bg,
                "phase_diff_peak_height": _height,
                "phase_diff_noise_sigma": _sigma,
                "phase_diff_snr": _snr,
                "phase_diff_width": _width,
                "phase_diff_support_points": _support,
            })
        return _accepted

    accepted = _do_detect(min_prominence, phase_diff_snr_threshold)

    # ---- n_resonances filtering + re-run with relaxed thresholds if too few ----
    if n_resonances > 0:
        _retry = 0
        while len(accepted) < n_resonances and _retry < 2:
            _retry += 1
            _mp = max(0.2, min_prominence / (2 ** _retry))
            _snr = max(0.05, phase_diff_snr_threshold / (2 ** _retry))
            print(f"  [find_true_resonances] Only {len(accepted)} peaks, "
                  f"retry {_retry}/2 (prominence={_mp:.2f}, snr_threshold={_snr:.2f})")
            accepted = _do_detect(_mp, _snr)

        if len(accepted) > n_resonances:
            if hint_freqs:
                def _dist_to_hint(peak):
                    return min(abs(peak["frequency"] - h) for h in hint_freqs)
                accepted.sort(key=_dist_to_hint)
            else:
                accepted.sort(key=lambda p: p["phase_diff_snr"], reverse=True)
            accepted = accepted[:n_resonances]

        if len(accepted) < n_resonances:
            print(f"  [find_true_resonances] WARNING: only found {len(accepted)}/{n_resonances} resonances")

        accepted.sort(key=lambda p: p["frequency"])

    fig = None
    axes = None

    if plot:
        fig, axes = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
        ax_amp, ax_phase = axes

        ax_amp.plot(freq, transmission, label="Transmission")
        ax_phase.plot(phase_diff_freq, phase_diff, label="diff(unwrapped phase)", marker='d')

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
                label="Accepted diff(phase) maxima", marker='s',
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


# ---------------------------------------------------------------------------
# 2.5 Window-based dip validation for high-T (model-driven) tracking
# ---------------------------------------------------------------------------

def find_dip_peaks_in_windows(freq, transmission, f_pred_hz, half_width_hz,
                              min_depth_db=0.5, edge_frac=0.15, smooth_pts=11,
                              min_window_points=30):
    """在预测窗口内用"下凹深度"验证谐振，返回候选中心频率。

    为什么需要它（重要实测经验）
    ---------------------------
    逼近 Tc 时 Qi 崩塌，谐振变浅变宽。70K 实测：金标准谐振在 transmission
    上已不是严格局部极小（prominence 仅 0.001–0.01 dB，凹陷宽到与背景
    融合），find_peaks / find_true_resonances 的"局部极小 + diff(phase) 峰"
    双判据物理性失效——这正是用户当初必须手工点选的原因。

    但模型已经把搜索范围缩小到预测中心 ± 窗口半宽（实测覆盖 100%），
    窗口内凹陷仍然清晰（70K/77K 实测深度 1.8–2.8 dB）。所以退而求其次：
    平滑后取窗口内最小点，要求它与窗口边缘均值的深度差 > min_depth_db，
    即认为该窗口内确有谐振，最小点频率即为候选中心。这比全局 prominence
    门槛稳健得多，是模型驱动选点能替代人工的核心手段。

    Parameters
    ----------
    freq : array (n,)
        频率轴 (Hz)。
    transmission : array (n,)
        幅度 trace (dB)，即 20*log10(abs(s21))。
    f_pred_hz, half_width_hz : array (n_mode,)
        预测中心频率与窗口半宽。窗口 = f_pred ± half。
    min_depth_db : float
        窗口最小点相对边缘均值的下凹深度门槛，低于此值视为窗口内无谐振。
    edge_frac : float
        用窗口首尾各此比例的点估计"边缘水平"。
    smooth_pts : int
        Savitzky-Golay 平滑窗口宽度（奇数），抑制单点噪声造成的假下凹。
    min_window_points : int
        窗口内少于该点数时跳过（窗口太窄没有统计意义）。

    Returns
    -------
    list[float]
        候选中心频率 (Hz)，升序。
    """
    from scipy.signal import savgol_filter

    freq = np.asarray(freq, dtype=float)
    transmission = np.asarray(transmission, dtype=float)
    f_pred = np.asarray(f_pred_hz, dtype=float).ravel()
    half = np.asarray(half_width_hz, dtype=float).ravel()
    if len(f_pred) != len(half):
        raise ValueError("f_pred_hz 与 half_width_hz 长度必须一致")

    if smooth_pts and smooth_pts >= 3 and smooth_pts <= len(transmission):
        smooth = savgol_filter(transmission, smooth_pts, 2)
    else:
        smooth = transmission

    candidates = set()
    for j in range(len(f_pred)):
        lo, hi = f_pred[j] - half[j], f_pred[j] + half[j]
        mask = (freq >= lo) & (freq <= hi)
        idx = np.where(mask)[0]
        if idx.size < min_window_points:
            continue
        sub = smooth[idx]
        i_local = int(np.argmin(sub))
        center = float(freq[idx[i_local]])
        n_edge = max(1, int(idx.size * edge_frac))
        edge_level = float(np.concatenate([sub[:n_edge], sub[-n_edge:]]).mean())
        depth = edge_level - float(sub[i_local])
        if depth >= min_depth_db:
            candidates.add(center)
    return sorted(candidates)


# ---------------------------------------------------------------------------
# 3. Interactive 3-panel GUI picker (exact copy from dataprocess.py)
# ---------------------------------------------------------------------------

def interactive_pick_resonances(freq, s21, title="Click to pick resonances",
                                premarked_freqs=None):
    """
    Interactive resonance picking for low-SNR regions where auto-detection fails.

    Three panels:
      - Raw Transmission (dB)
      - Detrended Transmission (Savitzky-Golay baseline removal)
      - diff(unwrapped phase)

    Controls:
      LEFT click  -> add/confirm a resonance at the nearest frequency (green X)
      RIGHT click -> remove the nearest resonance
      ENTER       -> confirm selection, close window
      ESC         -> discard, return empty list

    Parameters
    ----------
    freq : array-like, frequency (Hz)
    s21 : array-like of complex, S21 complex data
    title : str, window title
    premarked_freqs : list of float or None, pre-selected resonance frequencies (Hz)
        If provided, these are pre-marked as green X on open.
        User can press ENTER directly to confirm, or right-click to remove / left-click to add.

    Returns
    -------
    selected_freqs : list of float, selected resonance frequencies (Hz), sorted ascending
    """
    from scipy.signal import savgol_filter

    freq = np.asarray(freq)
    s21 = np.asarray(s21)
    transmission = 20 * np.log10(np.abs(s21))
    phase = np.unwrap(np.angle(s21))
    phase_diff = np.diff(phase)
    phase_diff_freq = 0.5 * (freq[:-1] + freq[1:])

    # Baseline detrending
    try:
        window = min(101, len(transmission) // 4 * 2 + 1)
        if window >= 5:
            baseline = savgol_filter(transmission, window_length=window, polyorder=3)
            detrended = transmission - baseline
        else:
            detrended = transmission - np.median(transmission)
    except Exception:
        detrended = transmission - np.median(transmission)

    # ---- state ----
    selected_x = []  # frequency values
    selected_points = []  # (amp_artist, detrend_artist, phase_artist) tuples

    # ---- create figure ----
    fig, (ax_amp, ax_detrend, ax_phase) = plt.subplots(
        3, 1, figsize=(12, 9), sharex=True
    )
    fig.suptitle(title, fontsize=13)

    # Panel 1: raw transmission
    ax_amp.plot(freq, transmission, 'k-', linewidth=0.6, alpha=0.7, label='Transmission')
    ax_amp.set_ylabel('Transmission (dB)')
    ax_amp.grid(True, alpha=0.3)
    ax_amp.legend(loc='upper right', fontsize=8)

    # Panel 2: detrended transmission
    ax_detrend.plot(freq, detrended, 'b-', linewidth=0.8, label='Detrended')
    ax_detrend.axhline(0, color='gray', linestyle=':', linewidth=0.5)
    ax_detrend.set_ylabel('Detrended Trans. (dB)')
    ax_detrend.grid(True, alpha=0.3)
    ax_detrend.legend(loc='upper right', fontsize=8)

    # Panel 3: phase diff
    ax_phase.plot(phase_diff_freq, phase_diff, 'r-', linewidth=0.6, alpha=0.7,
                  label='diff(phase)')
    ax_phase.set_xlabel('Frequency (Hz)')
    ax_phase.set_ylabel('diff(phase) (rad/sample)')
    ax_phase.grid(True, alpha=0.3)
    ax_phase.legend(loc='upper right', fontsize=8)

    # Hint text
    hint = fig.text(0.5, 0.01,
                    "LEFT click = add  |  RIGHT click = remove nearest  |  ENTER = confirm  |  ESC = cancel",
                    ha='center', fontsize=10, style='italic',
                    bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))
    status_text = fig.text(0.98, 0.01, "", ha='right', fontsize=9, color='green')

    def _update_status():
        if selected_x:
            status_text.set_text(f"Selected: {len(selected_x)} resonances")
        else:
            status_text.set_text("")

    def _add_at_frequency(f):
        """Add a selection marker at frequency f."""
        idx_a = np.argmin(np.abs(freq - f))
        p1, = ax_amp.plot(freq[idx_a], transmission[idx_a], 'x',
                          color='lime', markersize=12, markeredgewidth=2.5, zorder=10)
        p2, = ax_detrend.plot(freq[idx_a], detrended[idx_a], 'x',
                              color='lime', markersize=12, markeredgewidth=2.5, zorder=10)
        idx_p = np.argmin(np.abs(phase_diff_freq - f))
        p3, = ax_phase.plot(phase_diff_freq[idx_p], phase_diff[idx_p], 'x',
                            color='lime', markersize=12, markeredgewidth=2.5, zorder=10)
        selected_x.append(f)
        selected_points.append((p1, p2, p3))
        _update_status()
        fig.canvas.draw_idle()

    def _remove_nearest(f):
        """Remove the selection nearest to frequency f."""
        if not selected_x:
            return
        distances = [abs(sx - f) for sx in selected_x]
        idx_remove = int(np.argmin(distances))
        # Remove artists
        for artist in selected_points[idx_remove]:
            artist.remove()
        selected_x.pop(idx_remove)
        selected_points.pop(idx_remove)
        _update_status()
        fig.canvas.draw_idle()

    def on_click(event):
        if event.inaxes not in (ax_amp, ax_detrend, ax_phase):
            return
        if event.button == 1:  # LEFT: add
            _add_at_frequency(event.xdata)
        elif event.button == 3:  # RIGHT: remove
            _remove_nearest(event.xdata)

    def on_key(event):
        if event.key == 'enter':
            plt.close(fig)
        elif event.key == 'escape':
            selected_x.clear()
            plt.close(fig)

    fig.canvas.mpl_connect('button_press_event', on_click)
    fig.canvas.mpl_connect('key_press_event', on_key)

    # ---- pre-load previous picks ----
    _premarked_count = 0
    if premarked_freqs:
        _freq_min, _freq_max = freq[0], freq[-1]
        for _pf in premarked_freqs:
            if _freq_min <= _pf <= _freq_max:
                _add_at_frequency(_pf)
                _premarked_count += 1
        if _premarked_count:
            hint.set_text(
                f"Pre-loaded {_premarked_count} previous picks. "
                "LEFT=add | RIGHT=remove | ENTER=confirm | ESC=cancel"
            )

    plt.tight_layout(rect=[0, 0.06, 1, 0.95])
    _update_status()
    plt.show(block=True)

    selected_freqs = sorted(selected_x)
    if selected_freqs:
        print(f"Manually picked {len(selected_freqs)} resonance frequencies (Hz):")
        for i, f in enumerate(selected_freqs):
            print(f"  [{i}] {f:.6e} Hz  ({f/1e9:.6f} GHz)")
        print(f"\nCopy the line below to MANUAL_RESONANCE_FREQS:\n{selected_freqs}")
    else:
        print("No resonances selected (cancelled or window closed)")

    return selected_freqs


# ---------------------------------------------------------------------------
# 3.5 Slot-based resonance picker session (mode identity = slot index)
# ---------------------------------------------------------------------------

class ResonancePickerSession:
    """槽位化交互选点器：下标即模式号，供人工校准用。

    与 interactive_pick_resonances 的区别：后者返回**排序频率列表、无模式编号**，
    本类维护 N 个有序槽位（slot[i] = 模式 i），用算法选点预填，人工只改错的那
    一两个，模式归属由结构解决、无需选后推断。

    GUI 无关：直接构造 matplotlib.figure.Figure（不碰 pyplot 全局状态），可嵌入
    FigureCanvasTkAgg。调用方负责建 canvas、调 bind_to(canvas) 绑定事件并渲染。

    交互
    ----
    左键        : 把"当前槽位"设为点击处频率
    右键        : 清空"当前槽位"
    数字键 1..9 : 切换当前槽位（1 基，下标 = 模式号 - 1）
    Tab / ← / → : 循环切换槽位
    ENTER       : 确认（confirmed=True）
    ESC         : 取消（confirmed=False）

    构造参数
    --------
    freq, s21   : S21 数据（Hz + 复数）
    n_slots     : 模式数 N
    premarked   : list[float|None] 长度 N，算法选点预填
    prediction  : list[float|None] 长度 N，模型预测中心（残差/窗口图层）
    windows     : list[float|None] 长度 N，每模式窗口半宽（窗口图层）
    mode_labels : list[str] 长度 N，默认 R1..RN
    prev_picks  : list[float|None] 长度 N，上一温度选点（虚线图层）
    show_prediction / show_mode_labels / show_prev_picks / show_residuals
                : 四组可选图层开关
    on_finish   : callable，ENTER/ESC 后回调（调用方用于关闭 Toplevel）

    结果
    ----
    get_result() -> (slot_freqs, confirmed)；slot_freqs 按下标（模式号）排序。
    """

    def __init__(self, freq, s21, n_slots, *, title="Resonance calibration",
                 premarked=None, prediction=None, windows=None, mode_labels=None,
                 prev_picks=None, show_prediction=True, show_mode_labels=True,
                 show_prev_picks=True, show_residuals=True, on_finish=None):
        from scipy.signal import savgol_filter
        from matplotlib.figure import Figure

        self.freq = np.asarray(freq, dtype=float)
        self.s21 = np.asarray(s21)
        self.n_slots = int(n_slots)

        self.show_prediction = bool(show_prediction)
        self.show_mode_labels = bool(show_mode_labels)
        self.show_prev_picks = bool(show_prev_picks)
        self.show_residuals = bool(show_residuals)

        self.mode_labels = (list(mode_labels) if mode_labels is not None
                            else [f"R{i+1}" for i in range(self.n_slots)])
        self.prediction = self._pad(prediction)
        self.windows = self._pad(windows)
        self.prev_picks = self._pad(prev_picks)

        self.slot_freqs = [None] * self.n_slots
        self.current_slot = 0
        self.confirmed = False
        self.finished = False
        self.on_finish = on_finish
        self._canvas_ready = False
        self._artists = [[] for _ in range(self.n_slots)]

        # 颜色：当前槽位醒目，其余统一
        self.current_color = "red"
        self.other_color = "lime"

        # 派生量（三面板，与 interactive_pick_resonances 一致）
        self.transmission = 20.0 * np.log10(np.abs(self.s21))
        phase = np.unwrap(np.angle(self.s21))
        self.phase_diff = np.diff(phase)
        self.phase_diff_freq = 0.5 * (self.freq[:-1] + self.freq[1:])
        try:
            window = min(101, len(self.transmission) // 4 * 2 + 1)
            if window >= 5:
                baseline = savgol_filter(self.transmission, window_length=window,
                                         polyorder=3)
                self.detrended = self.transmission - baseline
            else:
                self.detrended = self.transmission - np.median(self.transmission)
        except Exception:
            self.detrended = self.transmission - np.median(self.transmission)

        self._freq_min, self._freq_max = float(self.freq[0]), float(self.freq[-1])

        self._build_figure(title)
        self._draw_static_layers()

        # 预填算法选点
        if premarked is not None:
            for i in range(min(self.n_slots, len(premarked))):
                v = premarked[i]
                if v is not None and self._freq_min <= float(v) <= self._freq_max:
                    self.slot_freqs[i] = float(v)
                    self._draw_slot(i)

        self._update_status()

    # ---- helpers ----

    def _pad(self, arr):
        n = self.n_slots
        if arr is None:
            return [None] * n
        arr = list(arr)
        return (arr + [None] * n)[:n]

    def _build_figure(self, title):
        from matplotlib.figure import Figure

        fig = Figure(figsize=(12, 9))
        self.figure = fig
        fig.suptitle(title, fontsize=13)

        self.ax_amp = fig.add_subplot(3, 1, 1)
        self.ax_detrend = fig.add_subplot(3, 1, 2, sharex=self.ax_amp)
        self.ax_phase = fig.add_subplot(3, 1, 3, sharex=self.ax_amp)

        self.ax_amp.plot(self.freq, self.transmission, 'k-', linewidth=0.6,
                         alpha=0.7, label='Transmission')
        self.ax_amp.set_ylabel('Transmission (dB)')
        self.ax_amp.grid(True, alpha=0.3)
        self.ax_amp.legend(loc='upper right', fontsize=8)

        self.ax_detrend.plot(self.freq, self.detrended, 'b-', linewidth=0.8,
                             label='Detrended')
        self.ax_detrend.axhline(0, color='gray', linestyle=':', linewidth=0.5)
        self.ax_detrend.set_ylabel('Detrended Trans. (dB)')
        self.ax_detrend.grid(True, alpha=0.3)
        self.ax_detrend.legend(loc='upper right', fontsize=8)

        self.ax_phase.plot(self.phase_diff_freq, self.phase_diff, 'r-',
                           linewidth=0.6, alpha=0.7, label='diff(phase)')
        self.ax_phase.set_xlabel('Frequency (Hz)')
        self.ax_phase.set_ylabel('diff(phase) (rad/sample)')
        self.ax_phase.grid(True, alpha=0.3)
        self.ax_phase.legend(loc='upper right', fontsize=8)

        self.hint = fig.text(
            0.5, 0.01,
            "LEFT=set current slot  RIGHT=clear  TAB/num=slot  ENTER=confirm  ESC=cancel",
            ha='center', fontsize=10, style='italic',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))
        self.status_text = fig.text(0.98, 0.04, "", ha='right', fontsize=9,
                                    color='green')

    def _draw_static_layers(self):
        """图层 1（预测线+窗口）与图层 3（上一温度选点），不随槽位状态变化。"""
        if self.show_prediction:
            for i in range(self.n_slots):
                pred = self.prediction[i]
                if pred is None:
                    continue
                half = self.windows[i]
                for ax in (self.ax_amp, self.ax_detrend, self.ax_phase):
                    ax.axvline(pred, color='blue', linestyle='--', linewidth=1.0,
                               alpha=0.8, zorder=5)
                if half is not None and half > 0:
                    self.ax_amp.axvspan(pred - half, pred + half, color='blue',
                                        alpha=0.12, zorder=0)
        if self.show_prev_picks:
            for i in range(self.n_slots):
                prev = self.prev_picks[i]
                if prev is None:
                    continue
                for ax in (self.ax_amp, self.ax_detrend, self.ax_phase):
                    ax.axvline(prev, color='magenta', linestyle=':',
                               linewidth=1.0, alpha=0.7, zorder=5)

    # ---- slot management ----

    def _clear_artists(self, i):
        for a in self._artists[i]:
            a.remove()
        self._artists[i] = []

    def _draw_slot(self, i):
        """按当前槽位状态（重）绘制槽位 i 的标记/编号/残差。"""
        self._clear_artists(i)
        f = self.slot_freqs[i]
        if f is None:
            return
        is_current = (i == self.current_slot)
        color = self.current_color if is_current else self.other_color
        ia = int(np.argmin(np.abs(self.freq - f)))
        ip = int(np.argmin(np.abs(self.phase_diff_freq - f)))
        artists = [
            self.ax_amp.plot(self.freq[ia], self.transmission[ia], 'x',
                             color=color, markersize=12, markeredgewidth=2.5,
                             zorder=10)[0],
            self.ax_detrend.plot(self.freq[ia], self.detrended[ia], 'x',
                                 color=color, markersize=12, markeredgewidth=2.5,
                                 zorder=10)[0],
            self.ax_phase.plot(self.phase_diff_freq[ip], self.phase_diff[ip], 'x',
                               color=color, markersize=12, markeredgewidth=2.5,
                               zorder=10)[0],
        ]
        if self.show_mode_labels:
            lbl = self.mode_labels[i] if i < len(self.mode_labels) else f"R{i+1}"
            artists.append(self.ax_amp.annotate(
                lbl, xy=(f, self.transmission[ia]), xytext=(0, 14),
                textcoords='offset points', fontsize=10, fontweight='bold',
                color=color, ha='center', zorder=11))
        if self.show_residuals and self.prediction[i] is not None:
            pred = float(self.prediction[i])
            d_hz = f - pred
            artists.append(self.ax_amp.annotate(
                f"Δ {d_hz/1e6:+.2f} MHz / {d_hz/pred*1e6:+.0f} ppm",
                xy=(f, self.transmission[ia]), xytext=(0, -18),
                textcoords='offset points', fontsize=8, color='purple',
                ha='center', zorder=11))
        self._artists[i] = artists

    def _refresh(self):
        self._update_status()
        if self._canvas_ready:
            self.figure.canvas.draw_idle()

    def _set_slot(self, i, freq):
        self.slot_freqs[i] = float(freq)
        self._draw_slot(i)
        self._refresh()

    def _clear_slot(self, i):
        self.slot_freqs[i] = None
        self._draw_slot(i)
        self._refresh()

    def _set_current_slot(self, i):
        if i == self.current_slot:
            return
        old = self.current_slot
        self.current_slot = i
        self._draw_slot(old)
        self._draw_slot(i)
        self._refresh()

    def _cycle_slot(self, direction):
        self._set_current_slot((self.current_slot + direction) % self.n_slots)

    def _update_status(self):
        filled = sum(1 for f in self.slot_freqs if f is not None)
        self.status_text.set_text(
            f"Slot {self.current_slot+1}/{self.n_slots}  |  "
            f"Filled {filled}/{self.n_slots}")

    # ---- events ----

    def _on_click(self, event):
        if event.inaxes not in (self.ax_amp, self.ax_detrend, self.ax_phase):
            return
        if event.xdata is None:
            return
        if event.button == 1:
            self._set_slot(self.current_slot, float(event.xdata))
        elif event.button == 3:
            self._clear_slot(self.current_slot)

    def _on_key(self, event):
        key = event.key
        if key in ('enter', 'return'):
            self.confirmed = True
            self._finish()
        elif key == 'escape':
            self.confirmed = False
            self._finish()
        elif key == 'tab':
            self._cycle_slot(1)
        elif key in ('1', '2', '3', '4', '5', '6', '7', '8', '9'):
            idx = int(key) - 1
            if idx < self.n_slots:
                self._set_current_slot(idx)
        elif key in ('left', 'right'):
            self._cycle_slot(1 if key == 'right' else -1)

    def _finish(self):
        self.finished = True
        if self.on_finish is not None:
            self.on_finish()

    # ---- public API ----

    def bind_to(self, canvas):
        """绑定事件并把 figure 交给 canvas 渲染。必须在 FigureCanvasTkAgg 之后调用。

        Figure() 创建时带的是占位 canvas；FigureCanvasTkAgg 会替换它。因此事件
        必须绑定在**新的** canvas 上，而不是 __init__ 里绑在占位 canvas 上。
        """
        self._canvas = canvas
        canvas.mpl_connect('button_press_event', self._on_click)
        canvas.mpl_connect('key_press_event', self._on_key)
        self._canvas_ready = True
        self._update_status()
        canvas.draw_idle()

    def get_result(self):
        """返回 (slot_freqs, confirmed)。slot_freqs 下标即模式号。"""
        return list(self.slot_freqs), self.confirmed


# ---------------------------------------------------------------------------
# 4. Cross-temperature nearest-neighbor peak tracking (new for refactored pipeline)
# ---------------------------------------------------------------------------

def match_peaks_across_temps(all_temp_peaks, n_resonators, max_jump_hz):
    """
    Track resonator peaks across temperatures using nearest-neighbor matching.

    Anchors on the first temperature's peaks and greedily matches each
    subsequent temperature's peaks to the last known frequency of each
    resonator track. If a temperature has no peak within max_jump_hz of
    a resonator's expected frequency, that entry is None.

    Parameters
    ----------
    all_temp_peaks : list of list of dict
        Outer list: one entry per temperature.
        Inner list: peaks found at that temperature. Each peak is a dict
        with at minimum a "freq_hz" key (float).
    n_resonators : int
        Number of resonator tracks to produce.
    max_jump_hz : float
        Maximum allowed frequency change between adjacent temperatures.
        Peaks beyond this distance from the last known frequency are
        considered missing.

    Returns
    -------
    result : list of list of dict or None
        Shape: n_resonators x n_temps.
        result[i][t] is the peak dict for resonator i at temperature t,
        or None if no matching peak was found.
    """
    n_temps = len(all_temp_peaks)

    # Initialize result matrix: n_resonators x n_temps, all None
    result = [[None] * n_temps for _ in range(n_resonators)]

    # Anchor on first temperature: sort peaks by frequency, take up to n_resonators
    if n_temps == 0:
        return result

    anchor_peaks = sorted(all_temp_peaks[0], key=lambda p: p["freq_hz"])
    for i in range(min(n_resonators, len(anchor_peaks))):
        result[i][0] = anchor_peaks[i]

    # Track last known frequency for each resonator
    last_freqs = [
        result[i][0]["freq_hz"] if result[i][0] is not None else None
        for i in range(n_resonators)
    ]

    # For each subsequent temperature, greedily match nearest peak
    for t in range(1, n_temps):
        unmatched = list(all_temp_peaks[t])  # working copy

        # Process resonators in order of their last known frequency (ascending)
        # so that lower-frequency resonators get first pick
        active_resonators = [(i, last_freqs[i]) for i in range(n_resonators)
                             if last_freqs[i] is not None]
        active_resonators.sort(key=lambda x: x[1])

        for i, ref_freq in active_resonators:
            if not unmatched:
                break

            # Find nearest unmatched peak
            best_j = min(
                range(len(unmatched)),
                key=lambda j: abs(unmatched[j]["freq_hz"] - ref_freq),
            )
            distance = abs(unmatched[best_j]["freq_hz"] - ref_freq)

            if distance <= max_jump_hz:
                result[i][t] = unmatched.pop(best_j)
                last_freqs[i] = result[i][t]["freq_hz"]
            # else: stays None (last_freqs unchanged)

    return result
