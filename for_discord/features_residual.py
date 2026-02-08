"""
features_residual.py - Diffusion Residual and Denoise Signatures for AI detection.

Specific to diffusion model artifacts:
- High-pass residual distribution
- Residual stationarity
- Denoise ladder signatures
- Upsampling artifact detection

Diffusion models leave characteristic denoising patterns.
"""

import numpy as np
from scipy import ndimage
from typing import Dict, Optional, TYPE_CHECKING
import warnings

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Optional OpenCV acceleration
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# Try to import JIT utilities for acceleration
try:
    from jit_utils import fast_skewness, fast_kurtosis, fast_entropy
    HAS_JIT = True
except ImportError:
    HAS_JIT = False

from utils import fast_skew, fast_kurt, fast_entropy_hist

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




def compute_highpass_residual_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Analyze high-pass residual statistics."""
    features = {}
    
    gray = precomputed.gray
    gaussian_2 = precomputed.gaussian_2
    
    residual = gray - gaussian_2
    flat = residual.ravel()
    
    features['res_mean'] = float(np.mean(flat))
    features['res_std'] = float(np.std(flat))
    features['res_skew'] = float(fast_skew(flat))
    features['res_kurtosis'] = float(fast_kurt(flat))
    _n = len(flat)
    _i5, _i25, _i75, _i95 = int(0.05 * (_n - 1)), int(0.25 * (_n - 1)), int(0.75 * (_n - 1)), int(0.95 * (_n - 1))
    _part = np.partition(flat, [_i5, _i25, _i75, _i95])
    features['res_p05'] = float(_part[_i5])
    features['res_p95'] = float(_part[_i95])
    features['res_iqr'] = float(_part[_i75] - _part[_i25])
    
    hist, _ = np.histogram(flat, bins=64)
    hist = hist / (hist.sum() + 1e-10)
    features['res_entropy'] = fast_entropy_hist(hist)
    
    return features


def compute_residual_stationarity_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Analyze residual stationarity across blocks."""
    features = {}
    
    gray = precomputed.gray
    gaussian_2 = precomputed.gaussian_2
    
    h, w = gray.shape
    residual = gray - gaussian_2
    
    block_size = min(32, h // 4, w // 4)
    if block_size < 8:
        return {'res_stationarity': 0.0}
    
    n_blocks_h = h // block_size
    n_blocks_w = w // block_size
    
    # Reshape into blocks for vectorized computation
    blocks = residual[:n_blocks_h*block_size, :n_blocks_w*block_size]
    blocks = blocks.reshape(n_blocks_h, block_size, n_blocks_w, block_size)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, block_size, block_size)
    
    # Vectorized stats
    means = np.mean(blocks, axis=(1, 2))
    stds = np.std(blocks, axis=(1, 2))
    skews = np.array([fast_skew(b.ravel()) for b in blocks])
    
    block_stats = [{'mean': float(m), 'std': float(s), 'skew': float(sk)} 
                   for m, s, sk in zip(means, stds, skews)]
    
    if len(block_stats) > 1:
        means = means
        stds = stds
        features['res_mean_var'] = float(np.var(means))
        features['res_std_var'] = float(np.var(stds))
        features['res_stationarity'] = float(1.0 / (np.var(stds) + 1e-10))
    else:
        features['res_mean_var'] = 0.0
        features['res_std_var'] = 0.0
        features['res_stationarity'] = 0.0
    
    return features


def compute_denoise_ladder_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Apply bilateral at different strengths and measure residual changes."""
    features = {}
    
    gray = precomputed.gray

    if HAS_CV2:
        gray_uint8 = precomputed.gray_uint8
        
        # Bilateral filters — edge-preserving denoising at two strengths
        d1 = cv2.bilateralFilter(gray_uint8, 5, 25, 25)
        d2 = cv2.bilateralFilter(gray_uint8, 9, 75, 75)
        
        r1 = gray_uint8.astype(np.float32) - d1.astype(np.float32)
        r2 = gray_uint8.astype(np.float32) - d2.astype(np.float32)
    else:
        # Fallback to precomputed Gaussian approximations
        d1 = precomputed.gaussian_1
        d2 = precomputed.gaussian_3
        
        if d1 is None:
            d1 = ndimage.gaussian_filter(gray, sigma=1)
        if d2 is None:
            d2 = ndimage.gaussian_filter(gray, sigma=3)
        
        r1 = gray - d1
        r2 = gray - d2
    
    features['denoise_r1_std'] = float(np.std(r1))
    features['denoise_r2_std'] = float(np.std(r2))
    delta = r2 - r1
    features['denoise_delta_std'] = float(np.std(delta))
    features['denoise_delta_skew'] = float(fast_skew(delta.ravel()))
    features['denoise_delta_kurtosis'] = float(fast_kurt(delta.ravel()))
    features['denoise_ratio'] = float(np.std(r2) / (np.std(r1) + 1e-10))
    
    return features


def compute_residual_bimodality_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Detect bimodality in residual (diffusion signature)."""
    features = {}
    
    gray = precomputed.gray
    gaussian_2 = precomputed.gaussian_2
    
    residual = gray - gaussian_2
    flat = residual.ravel()
    
    skew = fast_skew(flat)
    kurt = fast_kurt(flat)
    features['res_bimodality'] = float((skew ** 2 + 1) / (kurt + 3 + 1e-10))
    
    hist, _ = np.histogram(flat, bins=50)
    hist_smooth = ndimage.gaussian_filter1d(hist.astype(float), sigma=2)
    local_max = (hist_smooth[1:-1] > hist_smooth[:-2]) & (hist_smooth[1:-1] > hist_smooth[2:])
    features['res_histogram_peaks'] = float(np.sum(local_max))
    
    return features


def compute_upsampling_residual_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Detect upsampling artifacts in residual."""
    features = {}
    
    gray = precomputed.gray
    residual = precomputed.residual_1p5
    
    h, w = gray.shape
    # Reuse cached FFT of the residual.
    fft = precomputed.get_fft_residual(1.5)
    magnitude = np.abs(_fast_fftshift(fft))
    
    cy, cx = h // 2, w // 2
    center_size = min(10, h // 20, w // 20)
    magnitude_masked = magnitude.copy()
    magnitude_masked[cy-center_size:cy+center_size, cx-center_size:cx+center_size] = 0
    
    mask = magnitude_masked > 0
    if np.any(mask):
        vals = magnitude_masked[mask]
        median_mag = np.median(vals)
        std_mag = np.std(vals)
        if std_mag > 1e-10:
            peak_threshold = median_mag + 5 * std_mag
            peaks = magnitude_masked > peak_threshold
            features['upsample_peak_count'] = float(np.sum(peaks))
            features['upsample_peak_max'] = float(np.max(magnitude_masked))
        else:
            features['upsample_peak_count'] = 0.0
            features['upsample_peak_max'] = 0.0
    else:
        features['upsample_peak_count'] = 0.0
        features['upsample_peak_max'] = 0.0
    
    return features


def compute_residual_spectrum_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Noise spectrum skew toward mid-frequencies.
    """
    features = {
        'res_spec_low_ratio': 0.0,
        'res_spec_mid_ratio': 0.0,
        'res_spec_high_ratio': 0.0,
        'res_spec_mid_skew': 0.0,
    }

    gray = precomputed.gray
    gaussian_1p5 = precomputed.gaussian_1p5
    residual = precomputed.residual_1p5

    h, w = gray.shape
    if max(h, w) > 256:
        step = max(1, max(h, w) // 256)
        gray = gray[::step, ::step]
        residual = residual[::step, ::step]
        h, w = gray.shape

    f = _fast_fft2(residual)
    mag = np.abs(_fast_fftshift(f)) ** 2

    cy, cx = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    r_norm = r / (np.max(r) + 1e-10)

    low = r_norm <= 0.2
    mid = (r_norm > 0.2) & (r_norm <= 0.5)
    high = r_norm > 0.5

    total = np.sum(mag) + 1e-10
    low_e = np.sum(mag[low]) / total
    mid_e = np.sum(mag[mid]) / total
    high_e = np.sum(mag[high]) / total

    features['res_spec_low_ratio'] = float(low_e)
    features['res_spec_mid_ratio'] = float(mid_e)
    features['res_spec_high_ratio'] = float(high_e)
    features['res_spec_mid_skew'] = float(mid_e / (low_e + high_e + 1e-10))

    return features


def compute_residual_directional_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Directional noise remnants from iterative steps.
    """
    features = {'res_dir_entropy': 0.0, 'res_dir_anisotropy': 0.0}

    gray = precomputed.gray
    gaussian_1p5 = precomputed.gaussian_1p5
    
    residual = gray - gaussian_1p5

    if HAS_CV2:
        r32 = residual.astype(np.float32)
        gx = cv2.Sobel(r32, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(r32, cv2.CV_64F, 0, 1, ksize=3)
    else:
        gx = cv2.Sobel(residual, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(residual, cv2.CV_64F, 0, 1, ksize=3)

    mag = np.sqrt(gx ** 2 + gy ** 2)
    if mag.size == 0:
        return features

    _flat_mag = mag.ravel()
    _idx75 = int(0.75 * (len(_flat_mag) - 1))
    thresh = np.partition(_flat_mag, _idx75)[_idx75]
    mask = mag > thresh
    if np.sum(mask) < 50:
        return features

    angles = np.arctan2(gy[mask], gx[mask])
    n_bins = 18
    bins = ((angles + np.pi) * (n_bins / (2 * np.pi))).astype(np.int32)
    bins = np.clip(bins, 0, n_bins - 1)
    hist = np.bincount(bins, minlength=n_bins).astype(np.float64)
    hist = hist / (hist.sum() + 1e-10)

    features['res_dir_entropy'] = fast_entropy_hist(hist)
    features['res_dir_anisotropy'] = float(np.max(hist) / (np.mean(hist) + 1e-10))

    return features


def compute_phase_aligned_noise_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Phase-aligned noise patches.
    """
    features = {'phase_alignment_mean': 0.0, 'phase_alignment_std': 0.0}

    gray = precomputed.gray
    gaussian_1p2 = precomputed.gaussian(1.2)

    h, w = gray.shape
    if max(h, w) > 192:
        step = max(1, max(h, w) // 192)
        gray = gray[::step, ::step]
        gaussian_1p2 = gaussian_1p2[::step, ::step]
        h, w = gray.shape

    residual = gray - gaussian_1p2
    patch = 24
    stride = 24
    patches = []
    for y in range(0, h - patch + 1, stride):
        for x in range(0, w - patch + 1, stride):
            patches.append(residual[y:y+patch, x:x+patch])
            if len(patches) >= 16: break
        if len(patches) >= 16: break

    if len(patches) < 4:
        return features

    phase_vectors = []
    yy, xx = np.ogrid[:patch, :patch]
    cy, cx = patch // 2, patch // 2
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r_norm = r / (np.max(r) + 1e-10)
    mask = (r_norm > 0.2) & (r_norm <= 0.5)

    for p in patches:
        f = _fast_fft2(p)
        ph = np.angle(f)
        if np.sum(mask) == 0: continue
        phase_vectors.append(np.exp(1j * ph[mask]))

    if len(phase_vectors) < 2:
        return features

    min_len = min(v.size for v in phase_vectors)
    stacked = np.stack([v.ravel()[:min_len] for v in phase_vectors], axis=0)
    alignment = np.abs(np.mean(stacked, axis=0))
    features['phase_alignment_mean'] = float(np.mean(alignment))
    features['phase_alignment_std'] = float(np.std(alignment))

    return features


def compute_hf_suppression_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Uneven high-frequency suppression across regions.
    """
    features = {'hf_block_cv': 0.0, 'hf_block_var': 0.0}

    gray = precomputed.gray
    gaussian_2 = precomputed.gaussian_2

    residual = gray - gaussian_2
    abs_res = np.abs(residual)
    h, w = abs_res.shape
    block = max(16, min(64, min(h, w) // 4))
    if block < 8:
        return features

    nbh, nbw = h // block, w // block
    cropped = abs_res[:nbh * block, :nbw * block]
    blocks = cropped.reshape(nbh, block, nbw, block).transpose(0, 2, 1, 3).reshape(-1, block, block)
    block_means = np.mean(blocks, axis=(1, 2))
    
    if block_means.size > 0:
        features['hf_block_var'] = float(np.var(block_means))
        features['hf_block_cv'] = float(np.std(block_means) / (np.mean(block_means) + 1e-10))
    
    return features


def extract_residual_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """Extract all residual/diffusion features."""
    features = {}
    
    try:
        features.update(compute_highpass_residual_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in highpass: {e}")
    
    try:
        features.update(compute_residual_stationarity_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in stationarity: {e}")
    
    try:
        features.update(compute_denoise_ladder_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in denoise ladder: {e}")
    
    try:
        features.update(compute_residual_bimodality_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in bimodality: {e}")
    
    try:
        features.update(compute_upsampling_residual_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in upsampling: {e}")

    try:
        features.update(compute_residual_spectrum_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in residual spectrum: {e}")

    try:
        features.update(compute_residual_directional_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in residual directional: {e}")

    try:
        features.update(compute_phase_aligned_noise_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in phase alignment: {e}")

    try:
        features.update(compute_hf_suppression_features(img_array, precomputed=precomputed))
    except Exception as e:
        warnings.warn(f"Error in HF suppression: {e}")
    
    return features
