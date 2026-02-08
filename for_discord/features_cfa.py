"""
features_cfa.py - Color Filter Array (CFA) / Demosaic Features for AI detection.

Photo-specific features that detect Bayer pattern demosaicing:
- 2×2 periodic correlation tests
- Bayer hypothesis scoring
- Demosaic periodic peaks in FFT
- Re-demosaic consistency errors

Real camera photos have CFA artifacts from the Bayer pattern.
AI-generated images don't have this signature.
These features should be gated (use only for photo-like images).
"""

import numpy as np
import cv2
from scipy import ndimage, fftpack, stats
from typing import Dict, Tuple, Optional, TYPE_CHECKING
import warnings

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

try:
    from jit_utils import fast_corrcoef
    HAS_JIT = True
except ImportError:
    HAS_JIT = False

# Fast FFT using scipy.fft (single-threaded to limit resource usage)
try:
    from scipy.fft import fft2 as _sp_fft2, ifft2 as _sp_ifft2, fftshift as _sp_fftshift, ifftshift as _sp_ifftshift, fft as _sp_fft
    def _fast_fft2(x, **kw): return _sp_fft2(x, workers=1, **kw)
    def _fast_ifft2(x, **kw): return _sp_ifft2(x, workers=1, **kw)
    def _fast_fft(x, **kw): return _sp_fft(x, workers=1, **kw)
    _fast_fftshift = _sp_fftshift
    _fast_ifftshift = _sp_ifftshift
except ImportError:
    from numpy.fft import fft2 as _fast_fft2, ifft2 as _fast_ifft2, fftshift as _fast_fftshift, ifftshift as _fast_ifftshift, fft as _fast_fft


def _corrcoef(a, b):
    """Fast pairwise Pearson correlation."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    if a.size < 2 or b.size < 2:
        return 0.0
    if HAS_JIT:
        return float(fast_corrcoef(a, b))
    r = np.corrcoef(a, b)[0, 1]
    return float(r) if not np.isnan(r) else 0.0


def compute_bayer_pattern_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect Bayer pattern artifacts in the image.
    """
    features = {}
    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return {'bayer_score': 0.0}
    
    h, w, _ = img_array.shape
    channels = [('r', precomputed.r), ('g', precomputed.g), ('b', precomputed.b)]
    
    for name, channel in channels:
        # Compute per-channel residuals
        try:
            import cv2 as _cv2
            residual = channel - _cv2.GaussianBlur(channel.astype(np.float32), (11, 11), 1.5)
        except Exception:
            residual = channel - ndimage.gaussian_filter(channel, sigma=1.5)
        
        even_rows, odd_rows = residual[::2, :], residual[1::2, :]
        min_h = min(even_rows.shape[0], odd_rows.shape[0])
        features[f'cfa_{name}_row_diff'] = float(np.mean(np.abs(even_rows[:min_h, :] - odd_rows[:min_h, :])))
        
        even_cols, odd_cols = residual[:, ::2], residual[:, 1::2]
        min_w = min(even_cols.shape[1], odd_cols.shape[1])
        features[f'cfa_{name}_col_diff'] = float(np.mean(np.abs(even_cols[:, :min_w] - odd_cols[:, :min_w])))
        
        if h >= 4 and w >= 4:
            h_trim, w_trim = (h // 2) * 2, (w // 2) * 2
            blocks = residual[:h_trim, :w_trim].reshape(h_trim // 2, 2, w_trim // 2, 2)
            # Vectorized: compute variance for all 4 Bayer positions at once
            pos_vars = np.var(blocks, axis=(0, 2)).ravel()  # shape (4,) for 2x2 positions
            features[f'cfa_{name}_block_var_range'] = float(pos_vars.max() - pos_vars.min())
    
    # Vectorized bayer_score computation
    bayer_keys = [f'cfa_{c}_{d}_diff' for c in ['r', 'b'] for d in ['row', 'col']]
    features['bayer_score'] = float(np.mean([features.get(k, 0.0) for k in bayer_keys]))
    return features


def compute_demosaic_fft_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect demosaicing artifacts in frequency domain.
    """
    features = {}
    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return {'demosaic_peak_strength': 0.0}
    
    r, g, b = precomputed.r, precomputed.g, precomputed.b
    
    h, w = r.shape
    rg_diff, bg_diff = r - g, b - g
    
    for name, diff in [('rg', rg_diff), ('bg', bg_diff)]:
        fft = _fast_fft2(diff)
        magnitude = np.abs(_fast_fftshift(fft))
        cy, cx = h // 2, w // 2
        
        peak_h = np.mean(magnitude[cy-2:cy+2, 0:4]) + np.mean(magnitude[cy-2:cy+2, -4:])
        peak_v = np.mean(magnitude[0:4, cx-2:cx+2]) + np.mean(magnitude[-4:, cx-2:cx+2])
        peak_corner = (np.mean(magnitude[:4, :4]) + np.mean(magnitude[:4, -4:]) + np.mean(magnitude[-4:, :4]) + np.mean(magnitude[-4:, -4:]))
        center_mean = np.mean(magnitude[cy-10:cy+10, cx-10:cx+10])
        
        features[f'demosaic_{name}_peak_h'] = float(peak_h / (center_mean + 1e-10))
        features[f'demosaic_{name}_peak_v'] = float(peak_v / (center_mean + 1e-10))
        features[f'demosaic_{name}_peak_corner'] = float(peak_corner / (center_mean + 1e-10))
    
    # Vectorized peak strength computation
    peak_values = np.array([
        features.get('demosaic_rg_peak_h', 0), features.get('demosaic_rg_peak_v', 0),
        features.get('demosaic_bg_peak_h', 0), features.get('demosaic_bg_peak_v', 0)
    ])
    features['demosaic_peak_strength'] = float(np.mean(peak_values))
    return features


def compute_redemosaic_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Re-demosaic test: simulate mosaic -> demosaic and compare.
    """
    features = {}
    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return {'redemosaic_diff': 0.0}
    
    h, w, _ = img_array.shape
    mosaic = np.zeros((h, w), dtype=np.float64)
    mosaic[0::2, 0::2] = img_array[0::2, 0::2, 0]
    mosaic[0::2, 1::2] = img_array[0::2, 1::2, 1]
    mosaic[1::2, 0::2] = img_array[1::2, 0::2, 1]
    mosaic[1::2, 1::2] = img_array[1::2, 1::2, 2]
    
    # Vectorized demosaic simulation
    demosaiced = np.zeros_like(img_array, dtype=np.float64)
    kernel = np.ones((3, 3))
    
    # Red channel (positions 0::2, 0::2)
    r_mask = np.zeros((h, w), dtype=np.float64)
    r_mask[0::2, 0::2] = 1
    demosaiced[:, :, 0] = cv2.filter2D(mosaic * r_mask, -1, kernel / 5, borderType=cv2.BORDER_REFLECT)
    demosaiced[0::2, 0::2, 0] = img_array[0::2, 0::2, 0]
    
    # Green channel (positions 0::2,1::2 and 1::2,0::2)
    g_mask = np.zeros((h, w), dtype=np.float64)
    g_mask[0::2, 1::2] = 1
    g_mask[1::2, 0::2] = 1
    demosaiced[:, :, 1] = cv2.filter2D(mosaic * g_mask, -1, kernel / 4, borderType=cv2.BORDER_REFLECT)
    demosaiced[0::2, 1::2, 1] = mosaic[0::2, 1::2]
    demosaiced[1::2, 0::2, 1] = mosaic[1::2, 0::2]
    
    # Blue channel (positions 1::2, 1::2)
    b_mask = np.zeros((h, w), dtype=np.float64)
    b_mask[1::2, 1::2] = 1
    demosaiced[:, :, 2] = cv2.filter2D(mosaic * b_mask, -1, kernel / 5, borderType=cv2.BORDER_REFLECT)
    demosaiced[1::2, 1::2, 2] = img_array[1::2, 1::2, 2]
    
    diff = np.abs(img_array.astype(np.float32) - demosaiced)
    features['redemosaic_diff_mean'] = float(np.mean(diff))
    features['redemosaic_diff_std'] = float(np.std(diff))
    features['redemosaic_diff_max'] = float(np.max(diff))
    
    # Vectorized per-channel diff computation
    channel_means = np.mean(diff, axis=(0, 1))  # shape (3,)
    features['redemosaic_r_diff'] = float(channel_means[0])
    features['redemosaic_g_diff'] = float(channel_means[1])
    features['redemosaic_b_diff'] = float(channel_means[2])
    
    # Vectorized Bayer position diff
    bayer_diffs = np.array([
        np.mean(diff[0::2, 0::2, 0]),
        np.mean(diff[0::2, 1::2, 1]),
        np.mean(diff[1::2, 0::2, 1]),
        np.mean(diff[1::2, 1::2, 2])
    ])
    features['redemosaic_bayer_pos_diff'] = float(np.sum(bayer_diffs))
    
    return features


def compute_green_imbalance_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect green channel imbalance from Bayer pattern.
    """
    features = {}
    if img_array.ndim != 3 or img_array.shape[2] < 3:
        return {'green_imbalance': 0.0}
    
    g = precomputed.g
    gr, gb = g[0::2, 1::2], g[1::2, 0::2]
    min_h, min_w = min(gr.shape[0], gb.shape[0]), min(gr.shape[1], gb.shape[1])
    gr, gb = gr[:min_h, :min_w], gb[:min_h, :min_w]
    imbalance = gr - gb
    features['green_imbalance_mean'] = float(np.mean(imbalance))
    features['green_imbalance_std'] = float(np.std(imbalance))
    features['green_imbalance_abs'] = float(np.mean(np.abs(imbalance)))
    corr = _corrcoef(gr, gb)
    features['green_correlation'] = float(corr)
    
    return features


def extract_cfa_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all CFA/demosaic features.
    """
    features = {}
    try: features.update(compute_bayer_pattern_features(img_array, precomputed=precomputed))
    except Exception as e: warnings.warn(f"Error in Bayer features: {e}")
    try: features.update(compute_demosaic_fft_features(img_array, precomputed=precomputed))
    except Exception as e: warnings.warn(f"Error in demosaic FFT: {e}")
    try: features.update(compute_redemosaic_features(img_array, precomputed=precomputed))
    except Exception as e: warnings.warn(f"Error in redemosaic: {e}")
    try: features.update(compute_green_imbalance_features(img_array, precomputed=precomputed))
    except Exception as e: warnings.warn(f"Error in green imbalance: {e}")
    return features
