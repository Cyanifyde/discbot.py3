"""
features_forensic.py - Digital Forensics Features for AI detection.

Implements forensic analysis techniques used in image manipulation detection:
- DCT (Discrete Cosine Transform) block analysis
- PRNU (Photo Response Non-Uniformity) fingerprint analysis
- Recompression detection
- Compression artifact analysis

These techniques leverage the fact that real camera photos have specific
sensor and compression characteristics that AI-generated images lack.
"""

import numpy as np
from scipy import fftpack, ndimage
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
from io import BytesIO
import warnings
from utils import fast_entropy_hist as _entropy, fast_corrcoef as _corrcoef

try:
    from gpu_utils import gpu_fft2, gpu_fftshift, gpu_gaussian_filter, gpu_fft_magnitude_shifted
except ImportError:
    gpu_fft2 = np.fft.fft2
    gpu_fftshift = np.fft.fftshift
    def gpu_gaussian_filter(arr, sigma):
        from scipy import ndimage as _nd
        return _nd.gaussian_filter(arr, sigma=sigma)
    def gpu_fft_magnitude_shifted(arr):
        return np.abs(np.fft.fftshift(np.fft.fft2(arr)))

if TYPE_CHECKING:
    from image_precomputed import ImagePrecomputedData

# Try to import Cython forensic acceleration (fastest)
try:
    from cython_ext.fast_forensic import fast_block_boundary_cy, fast_dct_block_features_cy
    HAS_CYTHON_FORENSIC = True
except ImportError:
    HAS_CYTHON_FORENSIC = False

# Try to import JIT utilities for acceleration
try:
    from jit_utils import fast_kurtosis, fast_corrcoef, fast_entropy
    HAS_JIT = True
except ImportError:
    HAS_JIT = False


def _fast_kurtosis(data: np.ndarray) -> float:
    """Fast kurtosis computation without scipy."""
    if HAS_JIT:
        return fast_kurtosis(np.asarray(data, dtype=np.float64).ravel())
    n = len(data)
    if n < 4:
        return 0.0
    mean = np.mean(data)
    m2 = np.mean((data - mean) ** 2)
    m4 = np.mean((data - mean) ** 4)
    if m2 < 1e-10:
        return 0.0
    return m4 / (m2 ** 2) - 3.0


def compute_dct_block_artifact_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Analyze DCT block artifacts for compression forensics.
    """
    features = {}
    if precomputed is not None:
        gray = precomputed.gray
    else:
        gray = np.mean(img_array, axis=2).astype(np.float32, copy=False)
    
    h, w = gray.shape
    block_size = 8

    # Estimate block-grid phase
    row_phase = 0
    col_phase = 0
    try:
        from blockgrid import best_periodic_boundary_stats
        if precomputed is not None:
            row_diffs, col_diffs = precomputed.boundary_diffs
        else:
            from blockgrid import line_boundary_diffs
            row_diffs, col_diffs = line_boundary_diffs(gray)
        hb, hi, _, hstd, row_phase = best_periodic_boundary_stats(row_diffs, block_size)
        vb, vi, _, vstd, col_phase = best_periodic_boundary_stats(col_diffs, block_size)

        features['dct_boundary_h_mean'] = float(hb)
        features['dct_boundary_v_mean'] = float(vb)
        features['dct_boundary_h_std'] = float(hstd)
        features['dct_boundary_v_std'] = float(vstd)
        features['dct_boundary_ratio'] = 1.0 if hi <= 1e-10 else float(hb / (hi + 1e-10))
    except Exception:
        features['dct_boundary_h_mean'] = 0.0
        features['dct_boundary_v_mean'] = 0.0
        features['dct_boundary_h_std'] = 0.0
        features['dct_boundary_v_std'] = 0.0
        features['dct_boundary_ratio'] = 1.0

    if row_phase or col_phase:
        if h > row_phase and w > col_phase:
            gray = gray[int(row_phase) :, int(col_phase) :]
    
    h2, w2 = gray.shape
    pad_h = (block_size - h2 % block_size) % block_size
    pad_w = (block_size - w2 % block_size) % block_size
    gray_padded = np.pad(gray, ((0, pad_h), (0, pad_w)), mode='reflect')

    new_h, new_w = gray_padded.shape
    n_blocks_h, n_blocks_w = new_h // block_size, new_w // block_size
    
    blocks = gray_padded.reshape(n_blocks_h, block_size, n_blocks_w, block_size)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, block_size, block_size)

    # Use GPU-accelerated DCT if available (much faster for large batch of blocks)
    import gpu_utils
    dct_blocks = gpu_utils.gpu_dct2(blocks, norm='ortho')
    dc_coefficients = dct_blocks[:, 0, 0]
    ac_energies = np.sum(dct_blocks[:, 1:, :] ** 2, axis=(1, 2)) + np.sum(dct_blocks[:, 0, 1:] ** 2, axis=1)
    
    features['dct_block_dc_mean'] = float(np.mean(dc_coefficients))
    features['dct_block_dc_std'] = float(np.std(dc_coefficients))
    features['dct_block_ac_mean'] = float(np.mean(ac_energies))
    features['dct_block_ac_std'] = float(np.std(ac_energies))
    
    dc_hist, _ = np.histogram(dc_coefficients, bins=50)
    dc_hist = dc_hist / (dc_hist.sum() + 1e-10)
    features['dct_block_dc_entropy'] = _entropy(dc_hist)
    
    ac_hist, _ = np.histogram(ac_energies, bins=50)
    ac_hist = ac_hist / (ac_hist.sum() + 1e-10)
    features['dct_block_ac_entropy'] = _entropy(ac_hist)
    
    return features


def compute_prnu_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Analyze Photo Response Non-Uniformity (PRNU) patterns.
    """
    features = {}
    
    noise_residuals = []

    channels = [None, None, None]
    if precomputed is not None:
        channels = [precomputed.r, precomputed.g, precomputed.b]

    # Build the 3-channel stack first so we can denoise in one call when supported.
    ch_list = []
    for c in range(3):
        channel = channels[c]
        if channel is None:
            channel = img_array[:, :, c].astype(np.float32)
        ch_list.append(channel)

    try:
        # Shape (3, H, W); sigma=(0,2,2) avoids smoothing across channel axis.
        ch_stack = np.stack(ch_list, axis=0)
        den = gpu_gaussian_filter(ch_stack, sigma=(0.0, 2.0, 2.0))
        res = ch_stack - den
        noise_residuals = [res[i] for i in range(3)]
    except Exception:
        # Conservative fallback to per-channel denoising.
        for channel in ch_list:
            denoised = gpu_gaussian_filter(channel, sigma=2)
            noise_residuals.append(channel - denoised)
    
    r_prnu = noise_residuals[0].ravel()
    g_prnu = noise_residuals[1].ravel()
    b_prnu = noise_residuals[2].ravel()
    
    features['prnu_corr_rg'] = _corrcoef(r_prnu, g_prnu)
    features['prnu_corr_rb'] = _corrcoef(r_prnu, b_prnu)
    features['prnu_corr_gb'] = _corrcoef(g_prnu, b_prnu)
    
    avg_prnu_corr = (features['prnu_corr_rg'] + features['prnu_corr_rb'] + features['prnu_corr_gb']) / 3
    features['prnu_avg_correlation'] = float(avg_prnu_corr)
    
    combined_prnu = np.mean(noise_residuals, axis=0)
    h, w = combined_prnu.shape
    block_size = min(64, h // 4, w // 4)
    
    if block_size > 8:
        n_blocks_h = h // block_size
        n_blocks_w = w // block_size
        if n_blocks_h > 0 and n_blocks_w > 0:
            trimmed = combined_prnu[:n_blocks_h * block_size, :n_blocks_w * block_size]
            blocks = trimmed.reshape(n_blocks_h, block_size, n_blocks_w, block_size)
            blocks = blocks.transpose(0, 2, 1, 3).reshape(-1, block_size, block_size)
            block_variances = np.var(blocks, axis=(1, 2))
            block_means = np.mean(blocks, axis=(1, 2))
            features['prnu_block_var_cv'] = float(np.std(block_variances) / (np.mean(block_variances) + 1e-10))
            features['prnu_block_mean_std'] = float(np.std(block_means))
            features['prnu_spatial_consistency'] = float(1.0 / (features['prnu_block_var_cv'] + 1))
        else:
            features['prnu_block_var_cv'] = 0.0
            features['prnu_block_mean_std'] = 0.0
            features['prnu_spatial_consistency'] = 0.0
    else:
        features['prnu_block_var_cv'] = 0.0
        features['prnu_block_mean_std'] = 0.0
        features['prnu_spatial_consistency'] = 0.0
    
    prnu_magnitude = gpu_fft_magnitude_shifted(combined_prnu)
    cy, cx = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    
    low_mask = r < min(cy, cx) * 0.3
    mid_mask = (r >= min(cy, cx) * 0.3) & (r < min(cy, cx) * 0.7)
    high_mask = r >= min(cy, cx) * 0.7
    
    total_energy = np.sum(prnu_magnitude ** 2) + 1e-10
    features['prnu_low_freq_ratio'] = float(np.sum(prnu_magnitude[low_mask] ** 2) / total_energy)
    features['prnu_mid_freq_ratio'] = float(np.sum(prnu_magnitude[mid_mask] ** 2) / total_energy)
    features['prnu_high_freq_ratio'] = float(np.sum(prnu_magnitude[high_mask] ** 2) / total_energy)
    
    return features


def compute_recompression_features(img_array: np.ndarray, *, precomputed=None) -> Dict[str, float]:
    """
    Detect signs of recompression (multiple JPEG compression cycles).
    """
    features = {}
    
    try:
        quality_levels = [30, 70, 95]
        errors = []
        if precomputed is not None:
            original_gray = precomputed.gray.astype(np.float32)
        else:
            original_gray = np.mean(img_array, axis=2).astype(np.float32)
        img_uint8 = img_array.astype(np.uint8)
        
        try:
            import cv2
            # OpenCV JPEG encode/decode is 2-3x faster than PIL
            bgr = cv2.cvtColor(img_uint8, cv2.COLOR_RGB2BGR)
            for q in quality_levels:
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), q]
                ok, encoded = cv2.imencode('.jpg', bgr, encode_param)
                if ok:
                    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
                    recomp_gray = np.mean(cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB), axis=2).astype(np.float32)
                else:
                    recomp_gray = original_gray
                errors.append(np.mean(np.abs(original_gray - recomp_gray)))
        except ImportError:
            from PIL import Image
            pil_img = Image.fromarray(img_uint8)
            for q in quality_levels:
                buffer = BytesIO()
                pil_img.save(buffer, format='JPEG', quality=q)
                buffer.seek(0)
                recompressed = np.array(Image.open(buffer))
                recomp_gray = np.mean(recompressed, axis=2).astype(np.float32)
                errors.append(np.mean(np.abs(original_gray - recomp_gray)))
        
        errors = np.array(errors)
        features['recomp_error_mean'] = float(np.mean(errors))
        features['recomp_error_std'] = float(np.std(errors))
        features['recomp_error_min'] = float(np.min(errors))
        features['recomp_error_max'] = float(np.max(errors))
        
        error_diffs = np.diff(errors)
        features['recomp_curve_slope'] = float(np.mean(error_diffs))
        features['recomp_curve_convexity'] = float(np.mean(np.diff(error_diffs)))
        min_idx = np.argmin(errors)
        features['recomp_estimated_quality'] = float(quality_levels[min_idx])
        
        if len(quality_levels) > 1:
            est_q = quality_levels[min_idx]
            other_q = quality_levels[(min_idx + 2) % len(quality_levels)]
            try:
                import cv2
                encode_param1 = [int(cv2.IMWRITE_JPEG_QUALITY), est_q]
                ok1, enc1 = cv2.imencode('.jpg', bgr, encode_param1)
                if ok1:
                    dec1 = cv2.imdecode(enc1, cv2.IMREAD_COLOR)
                    encode_param2 = [int(cv2.IMWRITE_JPEG_QUALITY), other_q]
                    ok2, enc2 = cv2.imencode('.jpg', dec1, encode_param2)
                    if ok2:
                        dec2 = cv2.imdecode(enc2, cv2.IMREAD_COLOR)
                        img2 = cv2.cvtColor(dec2, cv2.COLOR_BGR2RGB)
                        double_error = np.mean(np.abs(img_array.astype(np.float32) - img2.astype(np.float32)))
                        features['recomp_double_single_ratio'] = float(double_error / (errors[min_idx] + 1e-10))
            except ImportError:
                from PIL import Image
                pil_img = Image.fromarray(img_uint8)
                buffer1 = BytesIO()
                pil_img.save(buffer1, format='JPEG', quality=est_q)
                buffer1.seek(0)
                img1 = Image.open(buffer1)
                buffer2 = BytesIO()
                img1.save(buffer2, format='JPEG', quality=other_q)
                buffer2.seek(0)
                img2 = np.array(Image.open(buffer2))
                double_error = np.mean(np.abs(img_array.astype(np.float32) - img2.astype(np.float32)))
                features['recomp_double_single_ratio'] = float(double_error / (errors[min_idx] + 1e-10))
    
    except Exception:
        features['recomp_error_mean'] = 0.0
        features['recomp_error_std'] = 0.0
        features['recomp_error_min'] = 0.0
        features['recomp_error_max'] = 0.0
        features['recomp_curve_slope'] = 0.0
        features['recomp_curve_convexity'] = 0.0
        features['recomp_estimated_quality'] = 75.0
        features['recomp_double_single_ratio'] = 1.0
    
    return features


def compute_quantization_table_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Analyze quantization patterns in DCT domain.
    """
    features = {}
    if precomputed is not None:
        gray = precomputed.gray
    else:
        gray = np.mean(img_array, axis=2).astype(np.float32)
    h, w = gray.shape
    
    block_size = 8
    n_blocks_h = (h - block_size + 1) // block_size
    n_blocks_w = (w - block_size + 1) // block_size
    
    if n_blocks_h == 0 or n_blocks_w == 0:
        features['quant_entropy_mean'] = 0.0
        features['quant_entropy_std'] = 0.0
        features['quant_coeff_std_mean'] = 0.0
        features['quant_lf_hf_entropy_ratio'] = 1.0
        return features
    
    blocks = np.lib.stride_tricks.as_strided(
        gray,
        shape=(n_blocks_h, n_blocks_w, block_size, block_size),
        strides=(gray.strides[0] * block_size, gray.strides[1] * block_size, 
                 gray.strides[0], gray.strides[1])
    ).copy()
    
    blocks_flat = blocks.reshape(-1, block_size, block_size)
    from gpu_utils import gpu_dct2
    dct_blocks = gpu_dct2(blocks_flat)
    dct_flat = dct_blocks.reshape(dct_blocks.shape[0], block_size * block_size)
    
    entropies = []
    stds = []
    
    for pos in range(block_size * block_size):
        coeffs = dct_flat[:, pos]
        if len(coeffs) > 1:
            hist, _ = np.histogram(coeffs, bins=50)
            hist = hist / (hist.sum() + 1e-10)
            entropies.append(_entropy(hist))
            stds.append(np.std(coeffs))
    
    if entropies:
        features['quant_entropy_mean'] = float(np.mean(entropies))
        features['quant_entropy_std'] = float(np.std(entropies))
        features['quant_coeff_std_mean'] = float(np.mean(stds))
        low_freq_entropy = np.mean(entropies[:min(9, len(entropies))])
        high_freq_entropy = np.mean(entropies[max(0, len(entropies)-9):])
        features['quant_lf_hf_entropy_ratio'] = float(low_freq_entropy / (high_freq_entropy + 1e-10))
    else:
        features['quant_entropy_mean'] = 0.0
        features['quant_entropy_std'] = 0.0
        features['quant_coeff_std_mean'] = 0.0
        features['quant_lf_hf_entropy_ratio'] = 1.0
    
    return features


def compute_splicing_detection_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Detect potential image splicing/compositing.
    """
    features = {}
    if precomputed is not None:
        gray = precomputed.gray
        noise_residual = precomputed.noise_residual
    else:
        gray = np.mean(img_array, axis=2).astype(np.float32)
        noise_residual = gray - gpu_gaussian_filter(gray, sigma=1.5)
    
    h, w = gray.shape
    grid_size = 4
    block_h, block_w = h // grid_size, w // grid_size
    
    if block_h > 16 and block_w > 16:
        block_noise_levels = []
        for i in range(grid_size):
            for j in range(grid_size):
                block_noise = noise_residual[i*block_h:(i+1)*block_h, j*block_w:(j+1)*block_w]
                block_noise_levels.append(np.std(block_noise))
        
        block_noise_levels = np.array(block_noise_levels)
        features['splice_noise_var'] = float(np.var(block_noise_levels))
        features['splice_noise_range'] = float(np.max(block_noise_levels) - np.min(block_noise_levels))
        features['splice_noise_cv'] = float(np.std(block_noise_levels) / (np.mean(block_noise_levels) + 1e-10))
        
        noise_grid = block_noise_levels.reshape(grid_size, grid_size)
        h_diffs = np.abs(np.diff(noise_grid, axis=1))
        v_diffs = np.abs(np.diff(noise_grid, axis=0))
        features['splice_h_discontinuity'] = float(np.mean(h_diffs))
        features['splice_v_discontinuity'] = float(np.mean(v_diffs))
        features['splice_max_discontinuity'] = float(max(np.max(h_diffs), np.max(v_diffs)))
    else:
        features['splice_noise_var'] = 0.0
        features['splice_noise_range'] = 0.0
        features['splice_noise_cv'] = 0.0
        features['splice_h_discontinuity'] = 0.0
        features['splice_v_discontinuity'] = 0.0
        features['splice_max_discontinuity'] = 0.0
    
    return features


def extract_forensic_features(
    img_array: np.ndarray,
    *,
    precomputed: Optional["ImagePrecomputedData"] = None,
) -> Dict[str, float]:
    """
    Extract all forensic analysis features.
    """
    features = {}
    features.update(compute_dct_block_artifact_features(img_array, precomputed=precomputed))
    features.update(compute_prnu_features(img_array, precomputed=precomputed))
    features.update(compute_recompression_features(img_array, precomputed=precomputed))
    features.update(compute_quantization_table_features(img_array, precomputed=precomputed))
    features.update(compute_splicing_detection_features(img_array, precomputed=precomputed))
    return features
