"""
features_spectral_diffusion.py - Diffusion spectral signatures (pixel-only).

Adds diffusion-oriented spectral features that are hard to capture with
generic FFT/DCT/wavelet stats:

- 1D/2D power spectrum tail shape ("rising tail" / tail upturn)
- 8x8 / 64x64 latent grid peak checks (Stable Diffusion-style block processing)
- Gibbs-like ringing across sharp edges (oscillations perpendicular to edges)
- Residual gaussianity (diffusion noise footprint proxy)

All features are computed from pixel data only and are robust to resizing.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple, TYPE_CHECKING

import numpy as np
import cv2
from scipy import ndimage

# GPU acceleration utilities
from gpu_utils import gpu_fft2, gpu_fftshift, gpu_fft_power_shifted

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Try to import JIT utilities for faster moment computation.
try:
    from jit_utils import fast_radial_profile

    HAS_JIT = True
except Exception:
    HAS_JIT = False

from utils import fast_skew, fast_kurt

# Try Cython radial profile (fastest)
try:
    from cython_ext.fast_patches import fast_radial_profile_cy
    HAS_CYTHON_PATCHES = True
except ImportError:
    HAS_CYTHON_PATCHES = False


def _downsample_gray(gray: np.ndarray, max_dim: int = 512) -> Tuple[np.ndarray, int]:
    """Fast integer-stride downsample (deterministic, no interpolation)."""
    h, w = gray.shape
    step = 1
    if max(h, w) > max_dim:
        step = max(1, max(h, w) // max_dim)
    if step > 1:
        gray = gray[::step, ::step]
    return gray, step


def _safe_log(x: np.ndarray) -> np.ndarray:
    return np.log(x + 1e-10)


def _radial_profile(power_shift: np.ndarray) -> np.ndarray:
    """Azimuthal (radial) average of a centered (fftshifted) power spectrum."""
    h, w = power_shift.shape
    cy, cx = h // 2, w // 2
    max_r = int(min(cy, cx))
    if max_r < 8:
        return np.zeros(0, dtype=np.float64)

    # Use Cython > Numba > NumPy fallback
    if HAS_CYTHON_PATCHES:
        try:
            return fast_radial_profile_cy(power_shift.astype(np.float64), max_r, 0)
        except Exception:
            pass
    if HAS_JIT:
        try:
            return fast_radial_profile(power_shift.astype(np.float64), max_r, 0)
        except Exception:
            pass

    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(np.int32)
    r_flat = r.ravel()
    p_flat = power_shift.ravel()
    valid = r_flat < max_r
    r_valid = r_flat[valid]
    p_valid = p_flat[valid]

    radial_sum = np.bincount(r_valid, weights=p_valid, minlength=max_r)
    radial_cnt = np.bincount(r_valid, minlength=max_r).astype(np.float64)
    radial = np.divide(radial_sum, radial_cnt, out=np.zeros(max_r, dtype=np.float64), where=radial_cnt > 0)
    return radial


def _tail_band_indices(n: int, lo: float, hi: float) -> np.ndarray:
    if n <= 0:
        return np.zeros(0, dtype=np.int32)
    a = int(lo * n)
    b = int(hi * n)
    a = max(1, min(n - 1, a))
    b = max(a + 1, min(n, b))
    return np.arange(a, b, dtype=np.int32)


def _slope_loglog(profile: np.ndarray, idx: np.ndarray) -> float:
    """Slope of log(profile) vs log(freq_index) on selected indices."""
    if profile.size < 8 or idx.size < 6:
        return 0.0
    y = profile[idx]
    valid = y > 0
    if np.sum(valid) < 6:
        return 0.0
    x = idx[valid].astype(np.float64)
    y = y[valid].astype(np.float64)
    try:
        slope = np.polyfit(_safe_log(x), _safe_log(y), 1)[0]
        return float(slope)
    except Exception:
        return 0.0


def _tail_upturn_metrics(profile: np.ndarray) -> Tuple[float, float]:
    """Detect tail upturn by looking at smoothed first differences near Nyquist."""
    if profile.size < 16:
        return 0.0, 0.0

    # Smooth a little to suppress pixel noise in the profile.
    k = 7
    kernel = np.ones(k, dtype=np.float64) / float(k)
    sm = np.convolve(np.log1p(profile), kernel, mode="same")

    tail_len = max(8, int(0.2 * sm.size))
    tail = sm[-tail_len:]
    d = np.diff(tail)
    if d.size == 0:
        return 0.0, 0.0
    pos_ratio = float(np.mean(d > 0))
    max_upturn = float(np.max(d))
    return pos_ratio, max_upturn


def _symmetric_1d_profile(spec: np.ndarray, center: int) -> np.ndarray:
    """Fold a 1D spectrum around DC to get a distance-from-center profile."""
    left = spec[:center][::-1]
    right = spec[center + 1 :]
    m = int(min(left.size, right.size))
    if m <= 0:
        return np.zeros(0, dtype=np.float64)
    return (left[:m] + right[:m]) / 2.0


def _find_peak_in_range(spec: np.ndarray, center: int, n_orig: int, target_period: float, tol_ratio: float = 0.25) -> float:
    """Find strongest peak in a frequency range corresponding to target_period +/- tolerance."""
    if target_period <= 0:
        return 0.0
    
    # Frequency f = n_orig / period
    # Period range: [target * (1-tol), target * (1+tol)]
    # Freq range: [n_orig / (target * (1+tol)), n_orig / (target * (1-tol))]
    
    p_min = target_period * (1.0 - tol_ratio)
    p_max = target_period * (1.0 + tol_ratio)
    
    # Convert to bins (distance from center)
    # Bin d corresponds to period P via: d = n_orig / P
    # So d_min = n_orig / p_max, d_max = n_orig / p_min
    
    d_min = int(n_orig / p_max)
    d_max = int(n_orig / p_min) + 1 # +1 to include upper bound
    
    # Clamp to valid spectrum range
    d_min = max(1, d_min)
    d_max = min(spec.size // 2, d_max)
    
    if d_min >= d_max:
        return 0.0
        
    # Search for max peak in [center-d_max, center-d_min] AND [center+d_min, center+d_max]
    # Since spectrum is symmetric (magnitude), we can just look at one side relative to center if input is full shift
    # But here 'spec' is 1D projection. 'center' is DC.
    
    # We look at right side: spec[center + d_min : center + d_max]
    # And left side: spec[center - d_max : center - d_min]
    
    # Extract right side region
    r_start = center + d_min
    r_end = center + d_max
    if r_start >= spec.size:
        return 0.0
    r_end = min(spec.size, r_end)
    
    region = spec[r_start:r_end]
    if region.size == 0:
        return 0.0
        
    # Peak relative to mean
    peak_val = float(np.max(region))
    mean_val = float(np.mean(spec))
    
    return float(peak_val / (mean_val + 1e-10))

def extract_spectral_diffusion_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Extract diffusion-focused spectral features."""
    features: Dict[str, float] = {}

    if precomputed is not None:
        gray = precomputed.gray
        power_shift = precomputed.fft_power
        # Use existing step for grid check if we can estimate it, 
        # but precomputed doesn't store downsampling step.
        # We'll assume step=1 for precomputed or recalculate if needed.
        step = 1
    else:
        gray = np.mean(img_array, axis=2).astype(np.float32) if img_array.ndim == 3 else img_array.astype(np.float32)
        gray, step = _downsample_gray(gray, max_dim=512)
        g0 = gray - float(np.mean(gray))
        power_shift = gpu_fft_power_shifted(g0)

    h, w = gray.shape
    if min(h, w) < 64:
        return {
            "specdiff_ps_tail_ratio_hm": 0.0,
            "specdiff_ps_slope_mid": 0.0,
            "specdiff_ps_slope_high": 0.0,
            "specdiff_ps_slope_delta": 0.0,
            "specdiff_ps_tail_posdiff_ratio": 0.0,
            "specdiff_ps_tail_max_upturn": 0.0,
            "specdiff_1d_tail_ratio_x": 0.0,
            "specdiff_1d_tail_ratio_y": 0.0,
            "specdiff_grid8_peak_x": 0.0,
            "specdiff_grid8_peak_y": 0.0,
            "specdiff_grid64_peak_x": 0.0,
            "specdiff_grid64_peak_y": 0.0,
            "specdiff_grid8_peak_max": 0.0,
            "specdiff_grid64_peak_max": 0.0,
            **_residual_gaussianity(gray, precomputed=precomputed),
            **_gibbs_ringing_features(gray, precomputed=precomputed),
        }

    # --- FFT / power spectrum profiles ---
    radial = _radial_profile(power_shift)
    if radial.size >= 16:
        # Tail ratio (high vs mid)
        mid_idx = _tail_band_indices(radial.size, 0.30, 0.60)
        hi_idx = _tail_band_indices(radial.size, 0.75, 0.98)
        mid_mean = float(np.mean(radial[mid_idx])) if mid_idx.size else 0.0
        hi_mean = float(np.mean(radial[hi_idx])) if hi_idx.size else 0.0
        features["specdiff_ps_tail_ratio_hm"] = float(hi_mean / (mid_mean + 1e-10))

        features["specdiff_ps_slope_mid"] = _slope_loglog(radial, mid_idx)
        features["specdiff_ps_slope_high"] = _slope_loglog(radial, hi_idx)
        features["specdiff_ps_slope_delta"] = float(features["specdiff_ps_slope_high"] - features["specdiff_ps_slope_mid"])

        pos_ratio, max_upturn = _tail_upturn_metrics(radial)
        features["specdiff_ps_tail_posdiff_ratio"] = float(pos_ratio)
        features["specdiff_ps_tail_max_upturn"] = float(max_upturn)
    else:
        features["specdiff_ps_tail_ratio_hm"] = 0.0
        features["specdiff_ps_slope_mid"] = 0.0
        features["specdiff_ps_slope_high"] = 0.0
        features["specdiff_ps_slope_delta"] = 0.0
        features["specdiff_ps_tail_posdiff_ratio"] = 0.0
        features["specdiff_ps_tail_max_upturn"] = 0.0

    # --- 1D power spectrum profiles (X/Y) ---
    spec_x = np.mean(power_shift, axis=0)
    spec_y = np.mean(power_shift, axis=1)
    cx = w // 2
    cy = h // 2
    spec_x[cx] = 0.0
    spec_y[cy] = 0.0

    prof_x = _symmetric_1d_profile(spec_x, cx)
    prof_y = _symmetric_1d_profile(spec_y, cy)

    def tail_ratio_1d(p: np.ndarray) -> float:
        if p.size < 16:
            return 0.0
        mid = p[int(0.30 * p.size) : int(0.60 * p.size)]
        hi = p[int(0.75 * p.size) : int(0.98 * p.size)]
        if mid.size == 0 or hi.size == 0:
            return 0.0
        return float(np.mean(hi) / (np.mean(mid) + 1e-10))

    features["specdiff_1d_tail_ratio_x"] = tail_ratio_1d(prof_x)
    features["specdiff_1d_tail_ratio_y"] = tail_ratio_1d(prof_y)

    # --- 8x8 / 64x64 grid peak checks (Robust) ---
    w_orig = w * step
    h_orig = h * step

    # Search for peaks in range of expected periods (allowing for resize/crop)
    # Tolerance of 0.25 allows for significant scaling (e.g. 8 +/- 2)
    
    features["specdiff_grid8_peak_x"] = _find_peak_in_range(spec_x, cx, w_orig, 8.0)
    features["specdiff_grid8_peak_y"] = _find_peak_in_range(spec_y, cy, h_orig, 8.0)
    features["specdiff_grid64_peak_x"] = _find_peak_in_range(spec_x, cx, w_orig, 64.0)
    features["specdiff_grid64_peak_y"] = _find_peak_in_range(spec_y, cy, h_orig, 64.0)
    
    features["specdiff_grid8_peak_max"] = float(max(features["specdiff_grid8_peak_x"], features["specdiff_grid8_peak_y"]))
    features["specdiff_grid64_peak_max"] = float(max(features["specdiff_grid64_peak_x"], features["specdiff_grid64_peak_y"]))

    # --- Residual gaussianity (noise footprint) ---
    features.update(_residual_gaussianity(gray))

    # --- Gibbs-like ringing ---
    features.update(_gibbs_ringing_features(gray))

    return features


def _residual_gaussianity(gray: np.ndarray, precomputed: Optional["ImagePrecomputedData"] = None) -> Dict[str, float]:
    """Gaussianity proxy of a high-pass residual (diffusion noise footprint)."""
    features: Dict[str, float] = {
        "specdiff_residual_std": 0.0,
        "specdiff_residual_skew": 0.0,
        "specdiff_residual_kurtosis": 0.0,
        "specdiff_residual_jarque_bera": 0.0,
    }
    if gray.size < 64:
        return features

    if precomputed is not None:
        res = precomputed.noise_residual
    else:
        ksize = int(6 * 2.0 + 1) | 1
        low = cv2.GaussianBlur(gray.astype(np.float32), (ksize, ksize), 2.0).astype(gray.dtype)
        res = gray - low
    
    flat = res.ravel()
    std = float(np.std(flat))
    if std < 1e-10:
        return features
    skew = fast_skew(flat)
    kurt = fast_kurt(flat)
    n = float(flat.size)
    jb = (n / 6.0) * (skew * skew + 0.25 * kurt * kurt)

    features["specdiff_residual_std"] = std
    features["specdiff_residual_skew"] = float(skew)
    features["specdiff_residual_kurtosis"] = float(kurt)
    features["specdiff_residual_jarque_bera"] = float(jb)
    return features


def _gibbs_ringing_features(gray: np.ndarray, precomputed: Optional["ImagePrecomputedData"] = None) -> Dict[str, float]:
    """
    Gibbs-like ringing proxy: oscillations in intensity profiles perpendicular to sharp edges.
    """
    features: Dict[str, float] = {
        "specdiff_gibbs_sign_change_mean": 0.0,
        "specdiff_gibbs_sign_change_p95": 0.0,
        "specdiff_gibbs_second_deriv_mean": 0.0,
        "specdiff_gibbs_nonmonotonic_ratio": 0.0,
    }

    h, w = gray.shape
    if min(h, w) < 64:
        return features

    if precomputed is not None:
        gx, gy = precomputed.sobel_v, precomputed.sobel_h
        mag = precomputed.edges
        edge_mask = precomputed.edge_binary
    else:
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        mag = np.sqrt(gx ** 2 + gy ** 2)
        _flat_m = mag.ravel()
        _idx95 = int(0.95 * (len(_flat_m) - 1))
        thr = float(np.partition(_flat_m, _idx95)[_idx95])
        edge_mask = mag > thr
    
    coords = np.argwhere(edge_mask)
    if coords.shape[0] < 50:
        return features

    # Deterministic subsample for speed.
    max_samples = 300
    if coords.shape[0] > max_samples:
        rng = np.random.default_rng(0)
        idx = rng.choice(coords.shape[0], size=max_samples, replace=False)
        coords = coords[idx]

    L = 6  # profile half-length (pixels) along edge normal
    ts = np.arange(-L, L + 1, dtype=np.int32)  # shape (13,)
    n_ts = len(ts)

    # Vectorized processing of all edge coordinates
    y_coords = coords[:, 0]
    x_coords = coords[:, 1]
    
    # Get gradient magnitudes and filter small values
    g_vals = mag[y_coords, x_coords]
    valid_mag = g_vals >= 1e-8
    
    # Compute normal directions for all points
    ny_all = np.zeros(len(coords))
    nx_all = np.zeros(len(coords))
    ny_all[valid_mag] = gy[y_coords[valid_mag], x_coords[valid_mag]] / g_vals[valid_mag]
    nx_all[valid_mag] = gx[y_coords[valid_mag], x_coords[valid_mag]] / g_vals[valid_mag]
    
    # Compute boundary samples for all points at once
    # ts[0] = -L, ts[-1] = L
    y0_all = y_coords + np.round(ts[0] * ny_all).astype(np.int32)
    y1_all = y_coords + np.round(ts[-1] * ny_all).astype(np.int32)
    x0_all = x_coords + np.round(ts[0] * nx_all).astype(np.int32)
    x1_all = x_coords + np.round(ts[-1] * nx_all).astype(np.int32)
    
    # Valid bounds check
    valid_bounds = (
        (y0_all >= 1) & (y0_all < h - 1) &
        (y1_all >= 1) & (y1_all < h - 1) &
        (x0_all >= 1) & (x0_all < w - 1) &
        (x1_all >= 1) & (x1_all < w - 1)
    )
    
    valid_mask = valid_mag & valid_bounds
    valid_indices = np.where(valid_mask)[0]
    
    if len(valid_indices) == 0:
        return features
    
    # Extract valid coordinates and normals
    y_valid = y_coords[valid_indices]
    x_valid = x_coords[valid_indices]
    ny_valid = ny_all[valid_indices]
    nx_valid = nx_all[valid_indices]
    
    # Compute all sample positions: (n_valid, n_ts)
    ts_float = ts.astype(np.float64)
    ys_all = y_valid[:, None] + np.round(ts_float[None, :] * ny_valid[:, None]).astype(np.int32)
    xs_all = x_valid[:, None] + np.round(ts_float[None, :] * nx_valid[:, None]).astype(np.int32)
    
    # Extract profiles: (n_valid, n_ts)
    prof_all = gray[ys_all, xs_all]
    
    # Smoothing kernel [1/3, 1/3, 1/3] - apply as moving average
    kernel = np.ones(3) / 3.0
    # Use ndimage for batch convolution or manual implementation
    # Manual: (prof[i-1] + prof[i] + prof[i+1]) / 3 for i in 1..n-2
    prof_smooth = np.zeros_like(prof_all)
    prof_smooth[:, 1:-1] = (prof_all[:, :-2] + prof_all[:, 1:-1] + prof_all[:, 2:]) / 3.0
    prof_smooth[:, 0] = prof_all[:, 0]  # Keep edges as-is (matching "same" mode behavior)
    prof_smooth[:, -1] = prof_all[:, -1]
    
    # First derivative: diff along axis 1, shape (n_valid, n_ts-1)
    d1 = np.diff(prof_smooth, axis=1)
    
    # Count sign changes per profile (vectorized)
    eps = 1e-3
    d1c = d1.copy()
    d1c[np.abs(d1c) < eps] = 0.0
    s = np.sign(d1c)  # (n_valid, n_ts-1)

    # Vectorized sign-change counting: for each row, count transitions
    # between consecutive non-zero sign values.
    # Strategy: multiply adjacent signs; where product < 0 AND both != 0, it's a change.
    s_left = s[:, :-1]   # (n_valid, n_ts-2)
    s_right = s[:, 1:]   # (n_valid, n_ts-2)
    both_nonzero = (s_left != 0) & (s_right != 0)
    sign_changes = np.sum((s_left * s_right < 0) & both_nonzero, axis=1).astype(np.int32)
    
    # Second derivative energy: mean(abs(diff(prof, 2)))
    d2 = np.diff(prof_smooth, n=2, axis=1)  # (n_valid, n_ts-2)
    second_energy = np.mean(np.abs(d2), axis=1)

    sc_arr = sign_changes.astype(np.float32)
    se_arr = second_energy

    features["specdiff_gibbs_sign_change_mean"] = float(np.mean(sc_arr))
    _flat_sc = sc_arr.ravel()
    _idx95_sc = int(0.95 * (len(_flat_sc) - 1))
    features["specdiff_gibbs_sign_change_p95"] = float(np.partition(_flat_sc, _idx95_sc)[_idx95_sc])
    features["specdiff_gibbs_second_deriv_mean"] = float(np.mean(se_arr))
    features["specdiff_gibbs_nonmonotonic_ratio"] = float(np.mean(sc_arr > 0))
    return features
